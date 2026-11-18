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


def test_monthly_consolidation_service(client, monkeypatch):
    """Valida o cálculo de mês fechado e verificação de consolidação incremental."""
    from app.services import monthly_consolidation as mc
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

    # API ERA5 simulada: o teste não depende de rede nem da data em que roda.
    monkeypatch.setattr(mc, "fetch_month_daily", _fake_era5(incomplete_months=()))
    ref = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)
    with client.application.app_context():
        res = consolidate_closed_month(reference_date=ref)
        assert res["status"] == "success"
        assert (res["year"], res["month"]) == (2026, 10)
        assert is_month_consolidated(2026, 10)

        # Segunda verificação no mesmo dia: nada a fazer.
        res = consolidate_closed_month(reference_date=ref)
        assert res["status"] == "already_consolidated"
        assert res["updated_regions"] == 0


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
        client.post("/api/power/report", json={"region_id": reg.id}, environ_base={"REMOTE_ADDR": "192.168.1.10"})
        res_3 = client.post("/api/power/report", json={"region_id": reg.id}, environ_base={"REMOTE_ADDR": "192.168.1.20"})
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
            environ_base={"REMOTE_ADDR": "10.0.0.50"}
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
            environ_base={"REMOTE_ADDR": "172.16.0.99"}
        )
        assert res_corr.status_code == 200
        corr_data = res_corr.get_json()
        assert corr_data["status"] == "outage_reported"
        assert "Sondas Offline" in corr_data["label"]

        # 6. Validação das rotas de API da Fase 2
        res_probes_api = client.get(f"/api/power/probes/{reg.id}")
        assert res_probes_api.status_code == 200
        assert "total_probes" in res_probes_api.get_json()


def test_power_official_bulletins_api(client, monkeypatch):
    """Valida a ingestão, consulta e impacto de notas oficiais da concessionária e Defesa Civil (Fase 3)."""
    monkeypatch.setenv("ADMIN_API_TOKEN", "token-de-teste")
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
        res_post = client.post("/api/power/bulletins", json=bulletin_payload,
                               headers={"X-Admin-Token": "token-de-teste"})
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






def test_risk_ignores_expired_forecasts(client):
    """Previsões vencidas (horários passados ainda não purgados) não entram no risco nem nas APIs."""
    from datetime import timedelta
    with client.application.app_context():
        reg = Region.query.first()
        hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        for h in range(1, 25):
            # Passado: chuva forte (deve ser ignorada) | Futuro: tempo seco
            db.session.add(WeatherObservation(region_id=reg.id, observed_at=hour - timedelta(hours=h),
                                              is_forecast=True, precipitation_mm=3.0, wind_speed=10.0, source="t"))
            db.session.add(WeatherObservation(region_id=reg.id, observed_at=hour + timedelta(hours=h),
                                              is_forecast=True, precipitation_mm=0.0, wind_speed=10.0, source="t"))
        db.session.add(WeatherObservation(region_id=reg.id, observed_at=hour, is_forecast=False,
                                          precipitation_mm=0.0, wind_speed=5.0, humidity_percent=60.0, source="t"))
        db.session.commit()

        assessment = calculate_region_risk(reg)
        assert assessment.factors["precipitacao_40pct"]["acumulado_previsto_24h_mm"] == 0.0
        assert assessment.score < 25

    summary = client.get("/api/weather-summary?format=list").get_json()
    assert summary[0]["rain_next_24h_mm"] == 0.0


def test_weather_api_has_no_stale_forecast_fallback(client):
    """Sem horários futuros, a previsão de 24h deve vir vazia (sem exibir dados passados)."""
    from datetime import timedelta
    with client.application.app_context():
        reg = Region.query.first()
        past = datetime.now(timezone.utc) - timedelta(hours=5)
        db.session.add(WeatherObservation(region_id=reg.id, observed_at=past, is_forecast=True,
                                          precipitation_mm=10.0, source="t"))
        db.session.commit()
        region_id = reg.id

    data = client.get(f"/api/weather/{region_id}").get_json()
    assert data["forecast_24h"] == []


class _NoTestingConfig:
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False


def test_create_app_does_not_start_scheduler(monkeypatch):
    """create_app() é usado por scripts e pelo preflight: não pode iniciar o agendador."""
    from app.scheduler import scheduler
    monkeypatch.delenv("DISABLE_SCHEDULER", raising=False)
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    create_app(_NoTestingConfig)
    assert not scheduler.running


def test_scheduler_skips_reloader_parent_process(monkeypatch):
    """Com debug/reloader ativo, o processo pai (sem WERKZEUG_RUN_MAIN) não inicia o agendador."""
    from app.scheduler import scheduler, init_scheduler
    monkeypatch.delenv("DISABLE_SCHEDULER", raising=False)
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    app = create_app(_NoTestingConfig)
    app.debug = True
    init_scheduler(app)
    assert not scheduler.running


def test_circular_mean_wind_direction():
    """Direção do vento usa média circular: 350° e 10° = 0° (norte), não 180° (sul)."""
    from app.collectors.open_meteo import _circular_mean_deg
    assert _circular_mean_deg([350, 10]) == 0.0
    assert _circular_mean_deg([340, 350, 20]) == 356.5
    assert _circular_mean_deg([80, 100]) == 90.0
    assert _circular_mean_deg([270, None, 270]) == 270.0
    assert _circular_mean_deg([None, None]) is None
    assert _circular_mean_deg([90, 270]) == 90.0  # opostos: mantém o modelo principal


def test_collector_saves_circular_wind_direction(client):
    """O coletor grava a direção consensual correta nas séries observada e prevista."""
    from datetime import timedelta
    from app.collectors.open_meteo import save_region_weather
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    times = [(hour + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00") for h in range(3)]
    hourly = {"time": times}
    for suffix, direction in [("_best_match", 350), ("_ecmwf_ifs025", 10), ("_gfs_seamless", 0)]:
        hourly[f"wind_direction_10m{suffix}"] = [direction] * 3
        hourly[f"wind_speed_10m{suffix}"] = [20.0] * 3
        hourly[f"temperature_2m{suffix}"] = [18.0] * 3
    current = {
        "time": times[0],
        "wind_direction_10m_best_match": 355,
        "wind_direction_10m_ecmwf_ifs025": 5,
        "wind_speed_10m_best_match": 20.0,
    }

    with client.application.app_context():
        reg = Region.query.first()
        save_region_weather(reg, {"current": current, "hourly": hourly})
        obs = WeatherObservation.query.filter_by(region_id=reg.id, is_forecast=False).first()
        fcs = WeatherObservation.query.filter_by(region_id=reg.id, is_forecast=True).all()
        assert obs.wind_direction == 0.0
        assert len(fcs) == 3
        assert all(f.wind_direction == 0.0 for f in fcs)


def _fake_era5(missing_last_days: int = 0, incomplete_months: tuple = ("2026-10",)):
    """Série diária simulada do mês pedido; nos meses de `incomplete_months`, os últimos
    `missing_last_days` dias vêm null (ERA5 atrasado)."""
    def fake_fetch(lat, lon, start_date, end_date):
        days = int(end_date[-2:])
        rain = [2.0] * days
        temp = [18.0] * days
        if start_date[:7] in incomplete_months:
            for i in range(days - missing_last_days, days):
                rain[i] = None
                temp[i] = None
        return {
            "precipitation_sum": rain,
            "temperature_2m_mean": temp,
            "temperature_2m_max": [24.0] * days,
            "temperature_2m_min": [12.0] * days,
            "wind_speed_10m_max": [30.0] * days,
        }
    return fake_fetch


def _fake_era5_october(missing_last_days: int = 0):
    return _fake_era5(missing_last_days, incomplete_months=("2026-10",))


def _seed_months(region_ids, months):
    """Grava meses já consolidados (para isolar o mês sob teste da janela de revisão)."""
    from app.models import db, MonthlyClimateSummary
    for rid in region_ids:
        for year, month in months:
            db.session.add(MonthlyClimateSummary(region_id=rid, year=year, month=month, total_rain_mm=1.0))
    db.session.commit()


def test_era5_availability_window():
    """Outubro (último dia 31) só fica disponível a partir de 06/11."""
    from app.services.monthly_consolidation import is_era5_available
    assert not is_era5_available(2026, 10, datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc))
    assert not is_era5_available(2026, 10, datetime(2026, 11, 5, 23, 0, tzinfo=timezone.utc))
    assert is_era5_available(2026, 10, datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc))


def test_consolidation_waits_for_era5(client, monkeypatch):
    """No dia 1º, o mês fechado não é consolidado nem consultado na API."""
    from app.models import MonthlyClimateSummary
    from app.services import monthly_consolidation as mc
    calls = []
    monkeypatch.setattr(mc, "fetch_month_daily", lambda *a: calls.append(a) or _fake_era5_october()(*a))
    with client.application.app_context():
        from app.models import Region
        _seed_months([r.id for r in Region.query.all()], [(2026, 8), (2026, 9)])
        res = mc.consolidate_closed_month(reference_date=datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc))
        assert res["status"] == "awaiting_era5"
        assert calls == []
        assert MonthlyClimateSummary.query.filter_by(year=2026, month=10).count() == 0


def test_consolidation_skips_incomplete_series_and_retries(client, monkeypatch):
    """Série com dias null não é gravada; numa verificação seguinte, com a série completa, consolida."""
    from app.models import MonthlyClimateSummary
    from app.services import monthly_consolidation as mc
    ref = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)
    with client.application.app_context():
        from app.models import Region
        _seed_months([r.id for r in Region.query.all()], [(2026, 8), (2026, 9)])
        monkeypatch.setattr(mc, "fetch_month_daily", _fake_era5_october(missing_last_days=5))
        res = mc.consolidate_closed_month(reference_date=ref)
        assert res["incomplete_regions"] == 1
        assert res["updated_regions"] == 0
        assert MonthlyClimateSummary.query.filter_by(year=2026, month=10).count() == 0

        monkeypatch.setattr(mc, "fetch_month_daily", _fake_era5_october(missing_last_days=0))
        res = mc.consolidate_closed_month(reference_date=ref)
        assert res["updated_regions"] == 1
        rec = MonthlyClimateSummary.query.filter_by(year=2026, month=10).one()
        assert rec.total_rain_mm == 62.0
        assert rec.rainy_days == 31


def test_recent_closed_months_window():
    """A janela cobre os 3 últimos meses fechados, inclusive na virada do ano."""
    from app.services.monthly_consolidation import get_recent_closed_months
    ref = datetime(2027, 2, 10, 3, 0, tzinfo=timezone.utc)
    assert get_recent_closed_months(ref) == [(2026, 11), (2026, 12), (2027, 1)]


def test_consolidation_catches_up_month_pending_at_rollover(client, monkeypatch):
    """Região pendente em outubro quando o mês vira continua sendo tentada em dezembro."""
    from app.models import MonthlyClimateSummary
    from app.services import monthly_consolidation as mc
    with client.application.app_context():
        from app.models import Region
        _seed_months([r.id for r in Region.query.all()], [(2026, 8), (2026, 9)])

        # Novembro: outubro com ERA5 incompleto fica pendente.
        monkeypatch.setattr(mc, "fetch_month_daily", _fake_era5_october(missing_last_days=5))
        mc.consolidate_closed_month(reference_date=datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc))
        assert MonthlyClimateSummary.query.filter_by(year=2026, month=10).count() == 0

        # Dezembro: o alvo passa a ser novembro, mas outubro (agora completo) é recuperado.
        monkeypatch.setattr(mc, "fetch_month_daily", _fake_era5(incomplete_months=()))
        res = mc.consolidate_closed_month(reference_date=datetime(2026, 12, 7, 3, 0, tzinfo=timezone.utc))
        assert (res["year"], res["month"]) == (2026, 11)
        assert res["status"] == "success"
        assert MonthlyClimateSummary.query.filter_by(year=2026, month=10).count() == 1
        assert MonthlyClimateSummary.query.filter_by(year=2026, month=11).count() == 1


def test_consolidation_window_makes_no_requests_without_gaps(client, monkeypatch):
    """Com os 3 meses já consolidados, nenhuma requisição é feita à API."""
    from app.services import monthly_consolidation as mc
    calls = []
    monkeypatch.setattr(mc, "fetch_month_daily", lambda *a: calls.append(a))
    with client.application.app_context():
        from app.models import Region
        _seed_months([r.id for r in Region.query.all()], [(2026, 8), (2026, 9), (2026, 10)])
        res = mc.consolidate_closed_month(reference_date=datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc))
        assert res["status"] == "already_consolidated"
        assert calls == []


# ------------------------- Segurança (item 5) -------------------------

def test_run_server_settings_never_expose_debugger():
    """Padrão local com debug; exposto na rede, o debugger é sempre desligado."""
    from run import resolve_server_settings
    assert resolve_server_settings({}) == ("127.0.0.1", 5000, True)
    assert resolve_server_settings({"CLIMAE_HOST": "0.0.0.0"}) == ("0.0.0.0", 5000, False)
    assert resolve_server_settings({"CLIMAE_HOST": "0.0.0.0", "FLASK_DEBUG": "1"})[2] is False
    assert resolve_server_settings({"FLASK_DEBUG": "0"})[2] is False
    assert resolve_server_settings({"CLIMAE_PORT": "8080"})[1] == 8080


def test_bulletin_post_requires_admin_token(client, monkeypatch):
    """Boletins 'oficiais' só podem ser criados com o token administrativo correto."""
    from app.models import PowerUtilityBulletin
    payload = {"municipality": "Rio Grande", "headline": "Falso", "body": "Falso",
               "source": "CEEE Grupo Equatorial", "affected_customers": 9999}

    monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
    assert client.post("/api/power/bulletins", json=payload).status_code == 403
    assert client.post("/api/power/bulletins", json=payload,
                       headers={"X-Admin-Token": ""}).status_code == 403

    monkeypatch.setenv("ADMIN_API_TOKEN", "segredo")
    assert client.post("/api/power/bulletins", json=payload).status_code == 403
    assert client.post("/api/power/bulletins", json=payload,
                       headers={"X-Admin-Token": "errado"}).status_code == 403
    with client.application.app_context():
        assert PowerUtilityBulletin.query.count() == 0

    assert client.post("/api/power/bulletins", json=payload,
                       headers={"X-Admin-Token": "segredo"}).status_code == 201


def test_power_cooldown_ignores_spoofed_forwarded_for(client):
    """Forjar X-Forwarded-For não cria 'novos moradores' para burlar o cooldown."""
    with client.application.app_context():
        reg_id = Region.query.first().id
    first = client.post("/api/power/report", json={"region_id": reg_id})
    assert first.status_code == 200
    for fake_ip in ("1.2.3.4", "5.6.7.8"):
        res = client.post("/api/power/report", json={"region_id": reg_id},
                          headers={"X-Forwarded-For": fake_ip})
        assert res.status_code == 429
    status = client.get(f"/api/power/status/{reg_id}").get_json()
    assert status["reports_30m"] == 1
    assert status["status"] != "outage_reported"


def test_power_ip_cap_blocks_user_agent_rotation(client):
    """Trocar o User-Agent não deixa um único IP disparar 'Falta de Luz Reportada' (3 relatos)."""
    with client.application.app_context():
        reg_id = Region.query.first().id
    codes = [
        client.post("/api/power/report", json={"region_id": reg_id},
                    headers={"User-Agent": f"agente-{i}"}).status_code
        for i in range(4)
    ]
    assert codes == [200, 200, 429, 429]
    status = client.get(f"/api/power/status/{reg_id}").get_json()
    assert status["reports_30m"] == 2
    assert status["status"] != "outage_reported"

    # Outro IP (outro morador) continua podendo relatar e completa os 3 relatos.
    res = client.post("/api/power/report", json={"region_id": reg_id},
                      environ_base={"REMOTE_ADDR": "10.0.0.7"})
    assert res.status_code == 200
    assert client.get(f"/api/power/status/{reg_id}").get_json()["status"] == "outage_reported"


def test_ip_report_limiter_window_and_regions():
    """A vaga libera quando o relato mais antigo sai da janela; regiões contam separado."""
    from datetime import timedelta
    from app.services.power_service import IpReportLimiter
    lim = IpReportLimiter(limit=2, window_minutes=30)
    t0 = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    assert lim.try_acquire("ip", 1, t0) == (True, 0)
    assert lim.try_acquire("ip", 1, t0 + timedelta(minutes=10)) == (True, 0)
    assert lim.try_acquire("ip", 1, t0 + timedelta(minutes=20)) == (False, 10)
    assert lim.try_acquire("ip", 2, t0 + timedelta(minutes=20)) == (True, 0)
    assert lim.try_acquire("ip", 1, t0 + timedelta(minutes=30, seconds=1)) == (True, 0)


def test_alerts_pagination_is_bounded(client):
    """per_page é limitado e page inválido não quebra a API."""
    data = client.get("/api/alerts?per_page=1000000&page=-3").get_json()
    assert data["current_page"] == 1
    from app.routes.main import MAX_PER_PAGE
    with client.application.app_context():
        reg = Region.query.first()
        now = datetime.now(timezone.utc)
        for i in range(MAX_PER_PAGE + 10):
            db.session.add(Alert(region_id=reg.id, triggered_rule="r", created_at=now,
                                 channel="painel", status="ok", dedup_key=f"k{i}"))
        db.session.commit()
    data = client.get("/api/alerts?per_page=1000000").get_json()
    assert len(data["alerts"]) == MAX_PER_PAGE


def test_occurrences_invalid_dates_return_400(client):
    """Datas malformadas retornam 400 com mensagem, e não erro 500."""
    assert client.get("/api/occurrences?start_date=2026-13-45").status_code == 400
    assert client.get("/api/occurrences?end_date=ontem").status_code == 400
    assert client.get("/api/occurrences?start_date=2026-01-01&end_date=2026-01-31").status_code == 200


def _stub_scheduler_startup_io(monkeypatch):
    """Evita rede nas rotinas que o agendador executa ao iniciar."""
    import app.scheduler as sched_mod
    import app.services.power_service as power_mod
    monkeypatch.delenv("DISABLE_SCHEDULER", raising=False)
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    monkeypatch.setattr(sched_mod, "run_scheduled_cycle", lambda app: None)
    monkeypatch.setattr(sched_mod, "consolidate_closed_month", lambda *a, **k: None)
    monkeypatch.setattr(power_mod, "seed_default_probes_if_empty", lambda: None)


def test_run_scheduler_script_starts_even_with_debug_env(monkeypatch):
    """O processo dedicado (uso com WSGI) inicia o agendador mesmo com FLASK_DEBUG=1 no ambiente."""
    from app.scheduler import scheduler
    from scripts import run_scheduler
    _stub_scheduler_startup_io(monkeypatch)
    monkeypatch.setenv("FLASK_DEBUG", "1")
    app = create_app(_NoTestingConfig)
    app.debug = True
    try:
        assert run_scheduler.start(app) is True
        job_ids = {job.id for job in scheduler.get_jobs()}
        assert {"open_meteo_collector", "database_pruning_job", "monthly_climate_consolidator"} <= job_ids
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


def test_run_scheduler_script_shuts_down_on_stop(monkeypatch):
    """Ao receber o pedido de parada (Ctrl+C/SIGTERM), o script encerra o agendador."""
    import signal
    import threading
    from app.scheduler import scheduler
    from scripts import run_scheduler
    _stub_scheduler_startup_io(monkeypatch)
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)
    stop = threading.Event()
    stop.set()
    try:
        assert run_scheduler.main(create_app(_NoTestingConfig), stop_event=stop) == 0
        assert not scheduler.running
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)


def test_run_scheduler_script_respects_disable_flag(monkeypatch):
    """Com DISABLE_SCHEDULER=1 o script não sobe nada e sai com código 1."""
    from app.scheduler import scheduler
    from scripts import run_scheduler
    _stub_scheduler_startup_io(monkeypatch)
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    assert run_scheduler.main(create_app(_NoTestingConfig)) == 1
    assert not scheduler.running


def test_wind_direction_weighted_by_speed():
    """Direção média ponderada pela velocidade: vento fraco quase não puxa a média."""
    from app.collectors.open_meteo import _circular_mean_deg
    # Norte a 40 km/h + leste a 2 km/h: fica praticamente norte (sem peso daria 45°).
    assert _circular_mean_deg([0, 90], [40, 2]) == 2.9
    # Opostos com velocidades bem diferentes: vence o vento forte.
    assert _circular_mean_deg([90, 270], [30, 2]) == 90.0
    assert _circular_mean_deg([90, 270], [2, 30]) == 270.0
    # Velocidade faltando ou tudo calmo: volta à média sem peso.
    assert _circular_mean_deg([0, 90], [40, None]) == 45.0
    assert _circular_mean_deg([0, 90], [0, 0]) == 45.0


def test_wind_direction_near_opposite_keeps_main_model():
    """Direções quase opostas (concordância baixa) mantêm o modelo principal em vez de um ângulo arbitrário."""
    from app.collectors.open_meteo import _circular_mean_deg
    assert _circular_mean_deg([90, 268]) == 90.0
    assert _circular_mean_deg([90, 268], [20, 20]) == 90.0


def test_wind_direction_pairs_speed_with_same_model():
    """Direção e velocidade são casadas pelo modelo, mesmo quando um modelo não traz direção."""
    from app.collectors.open_meteo import _aligned_model_series, _wind_direction_at_index
    hourly = {
        "wind_speed_10m": [10],                 # modelo sem direção correspondente
        "wind_direction_10m_best_match": [0],
        "wind_speed_10m_best_match": [40],
        "wind_direction_10m_ecmwf_ifs025": [90],
        "wind_speed_10m_ecmwf_ifs025": [2],
    }
    dirs = _aligned_model_series(hourly, "wind_direction_10m")
    speeds = _aligned_model_series(hourly, "wind_speed_10m")
    assert _wind_direction_at_index(dirs, speeds, 0) == 2.9
    assert _wind_direction_at_index(dirs, speeds, 5) is None


def test_clear_legacy_wind_direction_only_touches_old_observations(client):
    """Apaga só a direção de observações coletadas antes da correção; simulação não grava nada."""
    from datetime import timedelta
    from scripts.clear_legacy_wind_direction import FIX_CUTOFF_UTC, clear_legacy_wind_direction
    with client.application.app_context():
        reg = Region.query.first()
        before = FIX_CUTOFF_UTC - timedelta(days=1)
        after = FIX_CUTOFF_UTC + timedelta(hours=1)

        def obs(hour, collected, forecast=False):
            o = WeatherObservation(region_id=reg.id, observed_at=datetime(2026, 10, 20, hour),
                                   collected_at=collected, is_forecast=forecast,
                                   wind_direction=180.0, wind_speed=10.0, source="teste")
            db.session.add(o)
            return o

        old = obs(0, before)
        old_no_collected = obs(1, None)
        new = obs(2, after)
        old_forecast = obs(3, before, forecast=True)
        db.session.commit()
        # Registros antigos sem collected_at (o default do modelo preenche, então força NULL).
        WeatherObservation.query.filter_by(id=old_no_collected.id).update({"collected_at": None})
        db.session.commit()

        assert clear_legacy_wind_direction(apply=False) == 2
        db.session.expire_all()
        assert old.wind_direction == 180.0  # simulação não altera

        assert clear_legacy_wind_direction(apply=True) == 2
        db.session.expire_all()
        assert old.wind_direction is None
        assert old_no_collected.wind_direction is None
        assert old.wind_speed == 10.0           # só a direção é apagada
        assert new.wind_direction == 180.0      # coletada após a correção
        assert old_forecast.wind_direction == 180.0  # previsões não são tocadas
        assert clear_legacy_wind_direction(apply=True) == 0  # idempotente


def _file_db_app_with_legacy_obs(tmp_path):
    """App com banco SQLite em arquivo e uma observação antiga (direção 180°)."""
    from datetime import timedelta
    from scripts.clear_legacy_wind_direction import FIX_CUTOFF_UTC

    class FileDbConfig:
        TESTING = True
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{(tmp_path / 'climae.db').as_posix()}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    app = create_app(FileDbConfig)
    with app.app_context():
        db.create_all()
        reg = Region(municipality="Rio Grande", name="Centro", latitude=-32.0, longitude=-52.1)
        db.session.add(reg)
        db.session.commit()
        db.session.add(WeatherObservation(region_id=reg.id, observed_at=datetime(2026, 10, 20, 0),
                                          collected_at=FIX_CUTOFF_UTC - timedelta(days=1),
                                          is_forecast=False, wind_direction=180.0, source="teste"))
        db.session.commit()
    return app


def _stored_directions(db_file):
    import sqlite3
    con = sqlite3.connect(db_file)
    try:
        return [r[0] for r in con.execute("SELECT wind_direction FROM weather_observations")]
    finally:
        con.close()


def test_clear_legacy_wind_direction_apply_makes_backup_first(tmp_path):
    """Com --apply, o banco é copiado antes; a cópia guarda as direções originais."""
    from scripts import clear_legacy_wind_direction as script
    app = _file_db_app_with_legacy_obs(tmp_path)
    with app.app_context():
        db.engine.dispose()

    assert script.main(["--apply"], app=app) == 0
    with app.app_context():
        db.engine.dispose()

    backups = list(tmp_path.glob("climae.backup-*.db"))
    assert len(backups) == 1
    assert _stored_directions(backups[0]) == [180.0]
    assert _stored_directions(tmp_path / "climae.db") == [None]


def test_clear_legacy_wind_direction_backup_failure_changes_nothing(tmp_path, monkeypatch):
    """Se a cópia de segurança falhar, nada é apagado."""
    from scripts import clear_legacy_wind_direction as script
    app = _file_db_app_with_legacy_obs(tmp_path)

    def broken_backup(*a, **k):
        raise OSError("disco cheio")
    monkeypatch.setattr(script, "backup_sqlite_database", broken_backup)

    assert script.main(["--apply"], app=app) == 1
    with app.app_context():
        db.engine.dispose()
    assert _stored_directions(tmp_path / "climae.db") == [180.0]
    assert not list(tmp_path.glob("climae.backup-*.db"))


def test_backup_sqlite_database_refuses_missing_or_existing(tmp_path):
    """Não copia banco inexistente nem sobrescreve uma cópia já existente."""
    from scripts.clear_legacy_wind_direction import backup_sqlite_database
    with pytest.raises(FileNotFoundError):
        backup_sqlite_database(str(tmp_path / "nao-existe.db"))

    import sqlite3
    src = tmp_path / "x.db"
    sqlite3.connect(src).close()
    when = datetime(2026, 11, 18, 12, 0, 0)
    first = backup_sqlite_database(str(src), now=when)
    assert first.endswith("x.backup-20261118-120000.db")
    with pytest.raises(FileExistsError):
        backup_sqlite_database(str(src), now=when)


# ------------------------- Carga histórica (item 4d) -------------------------

def test_seed_end_date_skips_open_month_and_era5_lag():
    """A carga termina no último mês fechado com ERA5 completo, nunca no mês em andamento."""
    from scripts.seed_monthly_climate import get_seed_end_date
    # 24/10: outubro em andamento -> setembro é o último mês completo.
    assert get_seed_end_date(datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)) == "2026-09-30"
    # 03/11: outubro fechado, mas ainda dentro do atraso do ERA5.
    assert get_seed_end_date(datetime(2026, 11, 3, 3, 0, tzinfo=timezone.utc)) == "2026-09-30"
    # 06/11: ERA5 de outubro publicado.
    assert get_seed_end_date(datetime(2026, 11, 6, 3, 0, tzinfo=timezone.utc)) == "2026-10-31"
    # Virada do ano.
    assert get_seed_end_date(datetime(2027, 1, 2, 3, 0, tzinfo=timezone.utc)) == "2026-11-30"


def test_seed_does_not_save_partial_month(client):
    """Mês com dias faltando na série (fim do ERA5 ou mês em andamento) não é gravado."""
    from app.models import Region, MonthlyClimateSummary
    from scripts.seed_monthly_climate import aggregate_and_save_region
    dates = [f"2026-09-{d:02d}" for d in range(1, 31)] + [f"2026-10-{d:02d}" for d in range(1, 22)]
    n = len(dates)
    daily = {
        "time": dates,
        "temperature_2m_mean": [18.0] * n,
        "temperature_2m_max": [24.0] * n,
        "temperature_2m_min": [12.0] * n,
        "precipitation_sum": [2.0] * n,
        "wind_speed_10m_max": [30.0] * n,
    }
    with client.application.app_context():
        region = Region.query.first()
        aggregate_and_save_region(region, daily)
        saved = {(r.year, r.month) for r in MonthlyClimateSummary.query.filter(
            MonthlyClimateSummary.region_id == region.id, MonthlyClimateSummary.year > 0)}
        assert saved == {(2026, 9)}
