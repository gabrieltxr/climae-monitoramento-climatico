import pytest
from datetime import datetime, timezone
from app import create_app
from app.models import db, Region, WeatherObservation, Occurrence, Alert, TrafficEvent
from app.services.risk_engine import calculate_region_risk
from app.services.alert_engine import evaluate_region_alerts


class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False


@pytest.fixture
def client():
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        reg = Region(
            municipality="Rio Grande",
            name="Centro / Zona Portuária",
            latitude=-32.0350,
            longitude=-52.0986,
        )
        db.session.add(reg)
        db.session.commit()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def test_health_and_regions(client):
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    assert res_health.get_json()["status"] == "ok"

    res_reg = client.get("/api/regions")
    assert res_reg.status_code == 200
    data = res_reg.get_json()
    assert len(data) == 1
    assert data[0]["municipality"] == "Rio Grande"


def test_risk_inconclusive_when_no_weather(client):
    """Se não houver dados meteorológicos, o risco deve ser marcado como Inconclusivo."""
    with client.application.app_context():
        reg = Region.query.first()
        assessment = calculate_region_risk(reg)
        assert assessment.score is None
        assert "Inconclusivo" in assessment.risk_level


def test_risk_and_alert_deduplication(client):
    """Valida cálculo de risco elevado e garante que o alerta não duplica no mesmo dia."""
    with client.application.app_context():
        reg = Region.query.first()
        now = datetime.now(timezone.utc)

        # Insere chuva intensa (40mm) e vento forte (65 km/h)
        obs = WeatherObservation(
            region_id=reg.id,
            observed_at=now,
            is_forecast=True,
            precipitation_mm=40.0,
            rain_probability=95.0,
            wind_speed=65.0,
            source="Open-Meteo",
        )
        occ = Occurrence(
            region_id=reg.id,
            event_type="Inundação",
            occurred_at=now,
            severity="Alta",
            source="Defesa Civil",
            reliability="confirmado",
        )
        db.session.add_all([obs, occ])
        db.session.commit()

        # 1ª execução: deve criar alertas
        first_run = evaluate_region_alerts(reg)
        assert len(first_run) >= 2

        # 2ª execução no mesmo dia: deve retornar 0 novos alertas (deduplicação funcionando)
        second_run = evaluate_region_alerts(reg)
        assert len(second_run) == 0
        assert Alert.query.count() == len(first_run)


def test_traffic_impact_on_risk_assessment(client):
    """Valida a integração dinâmica da telemetria de trânsito no cálculo de risco da região."""
    with client.application.app_context():
        reg = Region.query.first()  # municipality = "Rio Grande"
        now = datetime.now(timezone.utc)

        obs = WeatherObservation(
            region_id=reg.id,
            observed_at=now,
            is_forecast=False,
            precipitation_mm=5.0,
            wind_speed=20.0,
            humidity_percent=80.0,
            source="Open-Meteo",
        )
        db.session.add(obs)
        db.session.commit()

        base_risk = calculate_region_risk(reg)
        assert base_risk.factors["condicoes_complementares_25pct"]["eventos_transito_ativos"] == 0
        assert base_risk.factors["condicoes_complementares_25pct"]["impacto_transito_pontos"] == 0.0

        # Adiciona intercorrência crítica no corredor ERS-734 de Rio Grande
        traffic_ev = TrafficEvent(
            highway="ERS-734 (Corredor Urbano-Litorâneo — Rio Grande ↔ Balneário Cassino)",
            status="Alerta de Alagamento na Pista",
            description="Lâmina d'água no trevo de acesso",
            source="DAER-RS / CRBM",
            reported_at=now,
        )
        db.session.add(traffic_ev)
        db.session.commit()

        # Recalcula o risco: agora deve detectar o corredor afetado do município
        updated_risk = calculate_region_risk(reg)
        comp = updated_risk.factors["condicoes_complementares_25pct"]
        assert comp["eventos_transito_ativos"] == 1
        assert comp["impacto_transito_pontos"] == 3.0
        assert updated_risk.score > base_risk.score
        # A intercorrência em rodovia também eleva o fator dinâmico de ativação da vulnerabilidade
        assert updated_risk.factors["vulnerabilidade_historico_35pct"]["pontos"] > base_risk.factors["vulnerabilidade_historico_35pct"]["pontos"]


def test_alerts_pagination_api(client):
    """Valida o endpoint paginado /api/alerts com metadados."""
    with client.application.app_context():
        reg = Region.query.first()
        now = datetime.now(timezone.utc)
        for i in range(7):
            alert = Alert(
                region_id=reg.id,
                triggered_rule=f"Regra Teste #{i}",
                created_at=now,
                channel="PAINEL",
                status="SENT",
                dedup_key=f"test_dedup_key_{i}",
            )
            db.session.add(alert)
        db.session.commit()

    # Página 1 (5 itens)
    res_p1 = client.get("/api/alerts?page=1&per_page=5")
    assert res_p1.status_code == 200
    data_p1 = res_p1.get_json()
    assert data_p1["total"] == 7
    assert data_p1["pages"] == 2
    assert data_p1["current_page"] == 1
    assert data_p1["has_next"] is True
    assert data_p1["has_prev"] is False
    assert len(data_p1["alerts"]) == 5

    # Página 2 (2 itens)
    res_p2 = client.get("/api/alerts?page=2&per_page=5")
    assert res_p2.status_code == 200
    data_p2 = res_p2.get_json()
    assert data_p2["current_page"] == 2
    assert data_p2["has_next"] is False
    assert data_p2["has_prev"] is True
    assert len(data_p2["alerts"]) == 2


def test_weather_summary_and_traffic_api(client):
    """Valida o resumo de camadas do mapa e a API do painel de trânsito."""
    res_summary = client.get("/api/weather-summary")
    assert res_summary.status_code == 200
    data_summary = res_summary.get_json()
    assert data_summary["success"] is True
    assert "summary" in data_summary
    assert len(data_summary["summary"]) == 1
    assert data_summary["summary"][0]["municipality"] == "Rio Grande"

    res_traffic = client.get("/api/traffic")
    assert res_traffic.status_code == 200
    data_traffic = res_traffic.get_json()
    assert len(data_traffic) == 7


def test_prune_expired_data_service(client):
    """Valida a purga automática de previsões antigas e avaliações de risco expiradas."""
    from app.services.pruning_service import prune_expired_data
    from datetime import timedelta

    with client.application.app_context():
        reg = Region.query.first()
        now = datetime.now(timezone.utc)
        expired_time = now - timedelta(hours=36)

        # Previsão antiga (> 24h atrás) -> deve ser purgada
        old_fc = WeatherObservation(
            region_id=reg.id,
            observed_at=expired_time,
            is_forecast=True,
            precipitation_mm=10.0,
            source="Open-Meteo",
        )
        # Previsão recente / futura -> NÃO deve ser purgada
        future_fc = WeatherObservation(
            region_id=reg.id,
            observed_at=now + timedelta(hours=6),
            is_forecast=True,
            precipitation_mm=5.0,
            source="Open-Meteo",
        )
        db.session.add_all([old_fc, future_fc])
        db.session.commit()

        # Executa a purga
        res = prune_expired_data(forecast_retention_hours=24, risk_retention_days=30)
        assert res["status"] == "success"
        assert res["deleted_forecasts"] >= 1

        # A previsão futura deve continuar existindo
        remaining_fc = WeatherObservation.query.filter_by(region_id=reg.id, is_forecast=True).all()
        assert len(remaining_fc) == 1
        assert remaining_fc[0].precipitation_mm == 5.0


def test_monthly_consolidation_service(client):
    """Valida o cálculo de mês fechado e verificação de consolidação incremental."""
    from app.services.monthly_consolidation import get_closed_month, is_month_consolidated, consolidate_closed_month
    from datetime import datetime

    # Teste de cálculo de data: em 01/11/2026, o mês fechado deve ser 10/2026
    dt_nov = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)
    y, m = get_closed_month(dt_nov)
    assert y == 2026
    assert m == 10

    # Teste em 05/01/2027: o mês fechado deve ser 12/2026
    dt_jan = datetime(2027, 1, 5, 12, 0, tzinfo=timezone.utc)
    y_jan, m_jan = get_closed_month(dt_jan)
    assert y_jan == 2026
    assert m_jan == 12

    with client.application.app_context():
        # Executa consolidação no contexto atual
        res = consolidate_closed_month()
        assert "status" in res
        assert res["status"] in ("already_consolidated", "success")


def test_power_outage_crowdsourcing_api(client):
    """Valida o módulo de monitoramento de energia elétrica via crowdsourcing e proteção anti-spam."""
    with client.application.app_context():
        reg = Region.query.first()
        assert reg is not None

        # 1. Status inicial deve ser estável
        res = client.get(f"/api/power/status/{reg.id}")
        assert res.status_code == 200
        data = res.get_json()
        assert data["status"] == "stable"
        assert data["reports_30m"] == 0
        assert data["user_can_report"] is True

        # 2. Registra o 1º relato
        res = client.post("/api/power/report", json={"region_id": reg.id, "comment": "Queda de luz no quarteirão"})
        assert res.status_code == 200
        rep_data = res.get_json()
        assert rep_data["success"] is True
        assert rep_data["reports_30m"] == 1
        assert rep_data["status"] == "investigating"

        # 3. Tentativa de relato duplicado do mesmo IP -> deve ser bloqueado com 429
        res_dup = client.post("/api/power/report", json={"region_id": reg.id, "comment": "Tentativa repetida"})
        assert res_dup.status_code == 429
        dup_data = res_dup.get_json()
        assert dup_data["success"] is False
        assert "cooldown_remaining" in dup_data

        # 4. Simula relatos de outros 2 IPs diferentes para atingir >= 3 relatos
        client.post("/api/power/report", json={"region_id": reg.id}, headers={"X-Forwarded-For": "192.168.1.10"})
        res_3 = client.post("/api/power/report", json={"region_id": reg.id}, headers={"X-Forwarded-For": "192.168.1.20"})
        assert res_3.status_code == 200
        data_3 = res_3.get_json()
        assert data_3["reports_30m"] >= 3
        assert data_3["status"] == "outage_reported"


def test_power_restoration_crowdsourcing_api(client):
    """Valida o restabelecimento colaborativo de energia elétrica ('A Luz Voltou')."""
    with client.application.app_context():
        reg = Region.query.first()
        assert reg is not None

        # 1. Cria um relato de falta de energia inicial
        res_rep = client.post("/api/power/report", json={"region_id": reg.id, "comment": "Bairro sem energia"})
        assert res_rep.status_code == 200
        assert res_rep.get_json()["status"] == "investigating"

        # 2. Confirma restabelecimento ("A Luz Voltou")
        res_rest = client.post("/api/power/restore", json={"region_id": reg.id, "comment": "A luz retornou estável"})
        assert res_rest.status_code == 200
        rest_data = res_rest.get_json()
        assert rest_data["success"] is True
        assert rest_data["status"] == "restored"
        assert "Energia Restabelecida" in rest_data["label"]
        assert rest_data["reports_30m"] == 0
        assert rest_data["restored_time"] is not None

        # 3. Tentativa de confirmação repetida de retorno do mesmo IP -> rate limited (429)
        res_dup_rest = client.post("/api/power/restore", json={"region_id": reg.id})
        assert res_dup_rest.status_code == 429
        assert "cooldown_remaining" in res_dup_rest.get_json()

        # 4. Se houver nova queda de energia posterior (de outro IP), o status deve voltar para investigando/outage
        res_new_outage = client.post(
            "/api/power/report",
            json={"region_id": reg.id, "comment": "Caiu novamente agora"},
            headers={"X-Forwarded-For": "10.0.0.50"}
        )
        assert res_new_outage.status_code == 200
        assert res_new_outage.get_json()["status"] == "investigating"


def test_power_telemetry_probes_and_consensus(client):
    """Valida a telemetria por sondas virtuais e o algoritmo de consenso multi-operadora da Fase 2."""
    from app.models import PowerGridProbe
    from app.services.power_service import seed_default_probes_if_empty, evaluate_region_probes, get_power_status

    with client.application.app_context():
        reg = Region.query.first()
        assert reg is not None

        # 1. Popula sondas padrão multi-operadora para a região de teste
        seed_default_probes_if_empty()
        probes = PowerGridProbe.query.filter_by(region_id=reg.id).all()
        assert len(probes) >= 3

        # 2. Avaliação inicial -> todas saudáveis (healthy)
        eval_healthy = evaluate_region_probes(reg.id)
        assert eval_healthy["status"] == "healthy"
        assert eval_healthy["up_probes"] == len(probes)

        # 3. Simula falha isolada de 1 operadora (ex: Claro cai, Vivo e RNP continuam up)
        # Filtro Anti-Falso Positivo: NÃO deve acusar blackout elétrico
        for p in probes:
            if p.provider_name == "Claro":
                p.is_up = False
        db.session.commit()

        eval_telecom = evaluate_region_probes(reg.id)
        assert eval_telecom["status"] == "telecom_instability"
        assert "Claro" in eval_telecom["providers_down"]

        status_telecom = get_power_status(reg.id)
        assert status_telecom["status"] == "stable"
        assert "Instabilidade de dados" in status_telecom["label"]

        # 4. Simula queda simultânea de TODAS as operadoras (Blackout Suspected)
        for p in probes:
            p.is_up = False
        db.session.commit()

        eval_blackout = evaluate_region_probes(reg.id)
        assert eval_blackout["status"] == "blackout_suspected"

        status_blackout = get_power_status(reg.id)
        assert status_blackout["status"] == "blackout_suspected"
        assert "Suspeita Técnica" in status_blackout["label"]

        # 5. Correlaciona: Suspeita Técnica + 1 relato de morador -> eleva imediatamente para 'outage_reported'
        res_corr = client.post(
            "/api/power/report",
            json={"region_id": reg.id, "comment": "Confirmando que caiu a energia no bairro"},
            headers={"X-Forwarded-For": "172.16.0.99"}
        )
        assert res_corr.status_code == 200
        corr_data = res_corr.get_json()
        assert corr_data["status"] == "outage_reported"
        assert "Sondas Offline" in corr_data["label"]

        # 6. Validação das rotas de API da Fase 2
        res_probes_api = client.get(f"/api/power/probes/{reg.id}")
        assert res_probes_api.status_code == 200
        assert "total_probes" in res_probes_api.get_json()


def test_power_official_bulletins_api(client):
    """Valida a ingestão, consulta e impacto de notas oficiais da concessionária e Defesa Civil (Fase 3)."""
    with client.application.app_context():
        reg = Region.query.first()  # municipality = "Rio Grande"
        assert reg is not None

        # 1. Cria um boletim oficial via POST /api/power/bulletins
        bulletin_payload = {
            "municipality": "Rio Grande",
            "source": "CEEE Grupo Equatorial",
            "headline": "Interrupção emergencial de alimentador na Zona Portuária",
            "body": "Desligamento programado e emergencial para substituição de isoladores avariados por fortes rajadas de vento.",
            "affected_customers": 2400,
            "severity": "critico",
            "status": "ativo"
        }
        res_post = client.post("/api/power/bulletins", json=bulletin_payload)
        assert res_post.status_code == 201
        post_data = res_post.get_json()
        assert post_data["success"] is True
        assert post_data["bulletin"]["affected_customers"] == 2400

        # 2. Consulta listagem de boletins via GET /api/power/bulletins
        res_get = client.get(f"/api/power/bulletins?municipality=Rio Grande")
        assert res_get.status_code == 200
        bulletins_list = res_get.get_json()
        assert len(bulletins_list) >= 1
        assert bulletins_list[0]["source"] == "CEEE Grupo Equatorial"

        # 3. O status de energia da região deve refletir a confirmação oficial da concessionária
        res_status = client.get(f"/api/power/status/{reg.id}")
        assert res_status.status_code == 200
        status_data = res_status.get_json()
        assert status_data["status"] == "official_outage"
        assert "Concessionária" in status_data["label"]
        assert status_data["official_bulletin"] is not None
        assert status_data["official_bulletin"]["affected_customers"] == 2400
        assert status_data["official_bulletin"]["severity"] == "critico"




