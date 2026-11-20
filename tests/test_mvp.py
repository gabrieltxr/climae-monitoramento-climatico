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
    """Valida cálculo de risco elevado e garante que o alerta não se repete na janela de 12h."""
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

        # 2ª execução logo em seguida: deve retornar 0 novos alertas (deduplicação funcionando)
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

        # 2. Primeira confirmação ("A Luz Voltou"): é só um voto, o relato continua valendo
        res_rest = client.post("/api/power/restore", json={"region_id": reg.id, "comment": "A luz retornou estável"})
        assert res_rest.status_code == 200
        rest_data = res_rest.get_json()
        assert rest_data["success"] is True
        assert rest_data["restoration_confirmed"] is False
        assert rest_data["status"] == "investigating"
        assert rest_data["reports_30m"] == 1
        assert "outro morador" in rest_data["message"]

        # 3. Confirmação repetida do mesmo IP -> rate limited (429)
        res_dup_rest = client.post("/api/power/restore", json={"region_id": reg.id})
        assert res_dup_rest.status_code == 429
        assert "cooldown_remaining" in res_dup_rest.get_json()

        # 4. Segunda confirmação, de outro IP -> consenso: energia restabelecida
        res_rest_2 = client.post("/api/power/restore", json={"region_id": reg.id},
                                 environ_base={"REMOTE_ADDR": "10.0.0.40"})
        assert res_rest_2.status_code == 200
        rest_2 = res_rest_2.get_json()
        assert rest_2["restoration_confirmed"] is True
        assert rest_2["status"] == "restored"
        assert "Energia Restabelecida" in rest_2["label"]
        assert rest_2["reports_30m"] == 0
        assert rest_2["restored_time"] is not None

        # 5. Se houver nova queda de energia posterior (de outro IP), o status deve voltar para investigando/outage
        res_new_outage = client.post(
            "/api/power/report",
            json={"region_id": reg.id, "comment": "Caiu novamente agora"},
            environ_base={"REMOTE_ADDR": "10.0.0.50"}
        )
        assert res_new_outage.status_code == 200
        assert res_new_outage.get_json()["status"] == "investigating"


def test_power_status_ignores_legacy_probes(client):
    """Sondas antigas (DNS públicos testados do servidor) não influenciam mais o status."""
    from app.models import PowerGridProbe
    from app.services.power_service import get_power_status

    with client.application.app_context():
        reg = Region.query.first()
        # Linhas que ficaram no banco de instalações antigas, todas "offline".
        for provider, host in (("Claro", "1.1.1.1"), ("Vivo", "8.8.8.8"), ("RNP / Regional", "9.9.9.9")):
            db.session.add(PowerGridProbe(region_id=reg.id, name=provider, target_host=host,
                                          target_port=53, provider_name=provider, is_up=False))
        db.session.commit()

        status = get_power_status(reg.id)
        assert status["status"] == "stable"
        assert "telemetry" not in status

        # 1 relato isolado continua sendo só "investigando" (antes, com sondas offline, virava "confirmada").
        res = client.post("/api/power/report", json={"region_id": reg.id},
                          environ_base={"REMOTE_ADDR": "172.16.0.99"})
        assert res.status_code == 200
        assert res.get_json()["status"] == "investigating"

    # As rotas das sondas não existem mais.
    assert client.get(f"/api/power/probes/{reg.id}").status_code == 404
    assert client.post(f"/api/power/probes/check/{reg.id}").status_code in (404, 405)


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


def test_collector_current_rain_blends_models_and_labels_estimates(client):
    """Chuva 'atual' usa a mistura entre modelos (não o maior valor) e os rótulos dizem que é modelo."""
    from datetime import timedelta
    from app.collectors.open_meteo import save_region_weather
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    past = (hour - timedelta(hours=2)).strftime("%Y-%m-%dT%H:00")
    future = (hour + timedelta(hours=2)).strftime("%Y-%m-%dT%H:00")
    hourly = {"time": [past, future], "precipitation_best_match": [0.0, 0.0]}
    # Só um modelo vê 10 mm agora: antes gravava 10 (máximo); a mistura dá 10 × 0,8 = 8.
    current = {
        "time": hour.strftime("%Y-%m-%dT%H:00"),
        "precipitation_best_match": 10.0,
        "precipitation_ecmwf_ifs025": 0.0,
        "precipitation_gfs_seamless": 0.0,
        "rain_best_match": 10.0,
        "showers_gfs_seamless": 12.0,
    }
    with client.application.app_context():
        reg = Region.query.first()
        save_region_weather(reg, {"current": current, "hourly": hourly})
        now_obs = WeatherObservation.query.filter_by(region_id=reg.id, is_forecast=False, observed_at=hour).one()
        assert now_obs.precipitation_mm == 8.0
        assert now_obs.source.startswith("Estimativa atual do modelo")
        past_obs = WeatherObservation.query.filter(
            WeatherObservation.region_id == reg.id, WeatherObservation.is_forecast.is_(False),
            WeatherObservation.observed_at < hour).one()
        assert past_obs.source.startswith("Estimativa do modelo para horas passadas")
        fc = WeatherObservation.query.filter_by(region_id=reg.id, is_forecast=True).one()
        assert fc.source.startswith("Previsão multi-modelo")


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


def test_secret_key_check_blocks_public_key_when_exposed():
    """Chave vazia/pública: exposto na rede não sobe; local sobe com aviso; chave própria sobe."""
    from run import check_secret_key
    for weak in (None, "", "dev-secret-key", "chave-secreta-desenvolvimento-mvp"):
        can_start, msg = check_secret_key(weak, "0.0.0.0")
        assert can_start is False and "NÃO iniciado" in msg
        can_start, msg = check_secret_key(weak, "127.0.0.1")
        assert can_start is True and msg
    assert check_secret_key("a" * 64, "0.0.0.0") == (True, None)


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


def test_single_restore_cannot_hide_outage(client):
    """Um único 'A Luz Voltou' (mesmo trocando User-Agent) não esconde uma falta de luz relatada."""
    from app.models import PowerOutageReport
    with client.application.app_context():
        reg_id = Region.query.first().id
    for ip in ("10.1.0.1", "10.1.0.2", "10.1.0.3"):
        client.post("/api/power/report", json={"region_id": reg_id}, environ_base={"REMOTE_ADDR": ip})
    assert client.get(f"/api/power/status/{reg_id}").get_json()["status"] == "outage_reported"

    codes = [
        client.post("/api/power/restore", json={"region_id": reg_id},
                    headers={"User-Agent": f"agente-{i}"},
                    environ_base={"REMOTE_ADDR": "10.9.9.9"}).status_code
        for i in range(3)
    ]
    assert codes == [200, 429, 429]
    status = client.get(f"/api/power/status/{reg_id}").get_json()
    assert status["status"] == "outage_reported"
    assert status["restore_confirmations"] == 1
    with client.application.app_context():
        # A confirmação não mexe mais nos relatos gravados.
        assert PowerOutageReport.query.filter_by(region_id=reg_id, is_active=True).count() == 3


def test_late_single_restore_does_not_hide_new_reports(client):
    """Depois do consenso, uma confirmação isolada não esconde relatos de uma nova queda."""
    from datetime import timedelta
    from app.models import PowerOutageReport, PowerRestorationReport
    from app.services.power_service import get_power_status
    with client.application.app_context():
        reg_id = Region.query.first().id
        now = datetime.now(timezone.utc)
        for minutes_ago, h in ((30, "a"), (25, "b")):
            db.session.add(PowerRestorationReport(region_id=reg_id, reporter_hash=h,
                                                  restored_at=now - timedelta(minutes=minutes_ago)))
        for minutes_ago, h in ((10, "x"), (9, "y"), (8, "z")):
            db.session.add(PowerOutageReport(region_id=reg_id, reporter_hash=h, is_active=True,
                                             reported_at=now - timedelta(minutes=minutes_ago)))
        db.session.add(PowerRestorationReport(region_id=reg_id, reporter_hash="c",
                                              restored_at=now - timedelta(minutes=1)))
        db.session.commit()
        status = get_power_status(reg_id)
        assert status["restoration_confirmed"] is True
        assert status["reports_30m"] == 3
        assert status["status"] == "outage_reported"


def test_power_posts_have_global_ip_limit(client):
    """Mais de 30 POSTs em /api/power/* por IP em 10 min recebem 429; GETs e boletins não contam."""
    from app.services import power_service
    with client.application.app_context():
        reg_id = Region.query.first().id
    # Requisições inválidas (400) também contam no limite geral.
    for _ in range(power_service.POWER_POST_LIMIT):
        assert client.post("/api/power/report", json={}).status_code == 400
    blocked = client.post("/api/power/report", json={"region_id": reg_id})
    assert blocked.status_code == 429
    assert "Muitas requisições" in blocked.get_json()["error"]
    assert client.post("/api/power/restore", json={"region_id": reg_id}).status_code == 429
    assert client.get(f"/api/power/status/{reg_id}").status_code == 200
    # Outro IP não é afetado; boletins (com token de admin) ficam fora do limite.
    assert client.post("/api/power/report", json={"region_id": reg_id},
                       environ_base={"REMOTE_ADDR": "10.2.0.1"}).status_code == 200
    assert client.post("/api/power/bulletins", json={}).status_code == 403


def test_power_vote_region_id_is_normalized(client):
    """'1', '01', 1.0 etc. não furam o teto por IP; tipos inválidos dão 400 e região inexistente 404."""
    with client.application.app_context():
        reg_id = Region.query.first().id
    ip = {"REMOTE_ADDR": "9.9.9.9"}
    codes = [
        client.post("/api/power/report", json={"region_id": rid},
                    headers={"User-Agent": repr(rid)}, environ_base=ip).status_code
        for rid in (reg_id, str(reg_id), f"0{reg_id}", f" {reg_id} ")
    ]
    assert codes == [200, 200, 429, 429]
    assert client.get(f"/api/power/status/{reg_id}").get_json()["reports_30m"] == 2

    for bad in (1.0, True, "abc", [reg_id], {"a": 1}, -1, 0):
        for route in ("/api/power/report", "/api/power/restore"):
            res = client.post(route, json={"region_id": bad}, environ_base={"REMOTE_ADDR": "9.9.9.8"})
            assert res.status_code == 400, (route, bad)
    for route in ("/api/power/report", "/api/power/restore"):
        assert client.post(route, json={}).status_code == 400
        assert client.post(route, json=[1]).status_code == 400
        assert client.post(route, json={"region_id": reg_id, "comment": ["x"]}).status_code == 400
        assert client.post(route, json={"region_id": 99999}).status_code == 404


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
    monkeypatch.delenv("DISABLE_SCHEDULER", raising=False)
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    monkeypatch.setattr(sched_mod, "run_scheduled_cycle", lambda app: None)
    monkeypatch.setattr(sched_mod, "consolidate_closed_month", lambda *a, **k: None)


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


# --- Estações INMET (medição real) ---

def _inmet_row(dt, chuva="0.0", vel="2.0", tem="20.1"):
    return {
        "DT_MEDICAO": dt.strftime("%Y-%m-%d"), "HR_MEDICAO": dt.strftime("%H00"),
        "CHUVA": chuva, "TEM_INS": tem, "UMD_INS": "80", "VEN_VEL": vel,
        "VEN_RAJ": "5.0", "VEN_DIR": "180", "PRE_INS": "1012.3", "CD_ESTACAO": "A802",
    }


def test_inmet_parse_row_converts_units_and_skips_empty():
    from app.collectors.inmet import parse_inmet_row
    row = parse_inmet_row({"DT_MEDICAO": "2026-11-19", "HR_MEDICAO": "1300", "CHUVA": "1.2",
                           "TEM_INS": "25.4", "VEN_VEL": "10", "VEN_RAJ": None, "UMD_INS": "-9999"})
    assert row["observed_at"] == datetime(2026, 11, 19, 13, tzinfo=timezone.utc)
    assert row["precipitation_mm"] == 1.2
    assert row["wind_speed_kmh"] == 36.0
    assert row["wind_gust_kmh"] is None
    assert row["humidity_percent"] is None
    assert parse_inmet_row({"DT_MEDICAO": "2026-11-19", "HR_MEDICAO": "1300"}) is None
    assert parse_inmet_row({"DT_MEDICAO": "", "HR_MEDICAO": "1300", "CHUVA": "1"}) is None


def test_inmet_collect_without_token_does_nothing(client, monkeypatch):
    from app.collectors import inmet
    monkeypatch.delenv("INMET_TOKEN", raising=False)

    def boom(*a, **k):
        raise AssertionError("não deveria chamar a API sem token")
    monkeypatch.setattr(inmet.http_session, "get", boom)
    assert inmet.collect_inmet_stations()["status"] == "sem_token"


def test_inmet_collect_saves_rows_and_upserts(client, monkeypatch):
    from app.collectors import inmet
    from app.models import StationObservation
    monkeypatch.setenv("INMET_TOKEN", "tok-teste")
    monkeypatch.delenv("INMET_URL_TEMPLATE", raising=False)
    now = datetime(2026, 11, 19, 15, tzinfo=timezone.utc)
    urls = []

    class Resp:
        def __init__(self, code, payload=None):
            self.status_code, self._payload = code, payload
        def json(self):
            return self._payload

    def fake_get(url, timeout=None):
        urls.append(url)
        if "/A802/" in url:
            return Resp(200, [_inmet_row(datetime(2026, 11, 19, 14)), _inmet_row(datetime(2026, 11, 19, 15))])
        if "/A887/" in url:
            return Resp(500)
        return Resp(204)
    monkeypatch.setattr(inmet.http_session, "get", fake_get)

    first = inmet.collect_inmet_stations(now=now)
    assert first["inserted"] == 2 and first["failed"] == ["A887"]
    assert "/2026-11-18/2026-11-19/A802/tok-teste" in urls[0]
    second = inmet.collect_inmet_stations(now=now)
    assert second["inserted"] == 0 and second["updated"] == 2
    assert StationObservation.query.count() == 2


def test_inmet_region_summary_uses_nearest_publishing_station(client):
    from datetime import timedelta
    from app.collectors.inmet import get_region_station_summary, save_station_rows
    now = datetime(2026, 11, 19, 15, 20, tzinfo=timezone.utc)
    base = now.replace(minute=0)
    region = Region.query.first()  # Rio Grande Centro: A802 a ~6 km
    assert get_region_station_summary(region, now=now) is None

    # Leitura velha (5h atrás) na A802 = estação parada; sem outra no raio de 30 km.
    save_station_rows("A802", [_inmet_row(base - timedelta(hours=5))])
    assert get_region_station_summary(region, now=now) is None

    save_station_rows("A802", [_inmet_row(base - timedelta(hours=h), chuva="2.5", vel="5") for h in range(10)])
    summary = get_region_station_summary(region, now=now)
    assert summary["code"] == "A802" and summary["distance_km"] < 10
    assert summary["hours_12h"] == 10
    assert summary["rain_12h_mm"] == 25.0
    assert summary["peak_wind_12h_kmh"] == 18.0

    far = Region(municipality="Chuí", name="Teste longe", latitude=-33.20, longitude=-52.40)
    db.session.add(far)
    db.session.commit()
    assert get_region_station_summary(far, now=now) is None


def test_inmet_window_needs_nine_hours(client):
    from datetime import timedelta
    from app.collectors.inmet import get_region_station_summary, save_station_rows
    now = datetime(2026, 11, 19, 15, 20, tzinfo=timezone.utc)
    base = now.replace(minute=0)
    save_station_rows("A802", [_inmet_row(base - timedelta(hours=h), chuva="9") for h in range(4)])
    summary = get_region_station_summary(Region.query.first(), now=now)
    assert summary["temperature_c"] == 20.1
    assert summary["rain_12h_mm"] is None and summary["peak_wind_12h_kmh"] is None


def test_alert_uses_measured_station_rain(client):
    from datetime import timedelta
    from app.collectors.inmet import save_station_rows
    now = datetime.now(timezone.utc)
    base = now.replace(minute=0, second=0, microsecond=0)
    region = Region.query.first()
    # Modelo estima 0 mm no passado; a estação mediu 3 mm/h por 10 horas = 30 mm.
    db.session.add(WeatherObservation(region_id=region.id, observed_at=base - timedelta(hours=1),
                                      is_forecast=False, precipitation_mm=0.0, wind_speed=5.0, source="modelo"))
    db.session.commit()
    save_station_rows("A802", [_inmet_row(base - timedelta(hours=h), chuva="3.0") for h in range(10)])

    alerts = evaluate_region_alerts(region)
    rain = [a for a in alerts if "CHUVA_INTENSA_24H" in a.dedup_key]
    assert rain, "alerta de chuva deveria disparar com a medição da estação"
    assert "medida na estação INMET A802" in rain[0].triggered_rule
    assert len(rain[0].triggered_rule) <= 100


def test_weather_api_exposes_station(client):
    from datetime import timedelta
    from app.collectors.inmet import save_station_rows
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    region_id = Region.query.first().id
    assert client.get(f"/api/weather/{region_id}").get_json()["station"] is None
    save_station_rows("A802", [_inmet_row(base - timedelta(hours=1))])
    station = client.get(f"/api/weather/{region_id}").get_json()["station"]
    assert station["code"] == "A802"
    assert station["observed_at"].endswith("+00:00")
    assert station["rain_12h_mm"] is None


# --- Region.city e manutenção de regiões ---

def test_region_city_synonym_works_in_queries(client):
    assert Region.query.filter_by(city="Rio Grande").count() == 1
    assert Region.query.filter(Region.city == "Rio Grande").count() == 1
    reg = Region.query.first()
    reg.city = "Pelotas"
    db.session.commit()
    assert Region.query.filter_by(municipality="Pelotas").count() == 1
    assert Region(city="Chuí", name="X", latitude=0, longitude=0).municipality == "Chuí"


def _seed_region_rows(region_id, hour):
    from app.models import RiskAssessment, MonthlyClimateSummary, PowerOutageReport
    db.session.add_all([
        WeatherObservation(region_id=region_id, observed_at=hour, is_forecast=False,
                           precipitation_mm=1.0, source="modelo"),
        Alert(region_id=region_id, triggered_rule="teste", channel="painel", status="ok",
              dedup_key=f"reg{region_id}_TESTE"),
        RiskAssessment(region_id=region_id, risk_level="Baixo", score=10, method_version="t"),
        Occurrence(region_id=region_id, event_type="Alagamento", occurred_at=hour, source="teste"),
        MonthlyClimateSummary(region_id=region_id, year=2026, month=10, total_rain_mm=80),
        PowerOutageReport(region_id=region_id, reporter_hash="h" * 64),
    ])
    db.session.commit()


def test_region_dependent_columns_cover_all_region_tables():
    from app.services.region_maintenance import region_dependent_columns
    tables = {model.__tablename__ for model, _ in region_dependent_columns()}
    assert {"weather_observations", "alerts", "risk_assessments", "occurrences",
            "monthly_climate_summaries", "power_outage_reports", "power_restoration_reports",
            "power_utility_bulletins", "traffic_events"} <= tables
    assert "station_observations" not in tables


def test_merge_region_moves_every_table_and_drops_overlaps(client):
    from app.models import RiskAssessment, MonthlyClimateSummary, PowerOutageReport
    from app.services.region_maintenance import merge_region_into
    hour = datetime(2026, 11, 19, 12)
    target = Region.query.first()
    old = Region(municipality="Rio Grande", name="Centro", latitude=-32.03, longitude=-52.09)
    db.session.add(old)
    db.session.commit()
    _seed_region_rows(old.id, hour)
    # O destino já tem a mesma hora e o mesmo mês: os da antiga são descartados.
    db.session.add_all([
        WeatherObservation(region_id=target.id, observed_at=hour, is_forecast=False,
                           precipitation_mm=2.0, source="modelo"),
        MonthlyClimateSummary(region_id=target.id, year=2026, month=10, total_rain_mm=90),
    ])
    db.session.commit()
    old_id = old.id

    result = merge_region_into(old, target)

    assert db.session.get(Region, old_id) is None
    assert result["dropped_duplicates"] == {"monthly_climate_summaries": 1, "weather_observations": 1}
    for model in (Alert, RiskAssessment, Occurrence, PowerOutageReport):
        assert model.query.filter_by(region_id=old_id).count() == 0
        assert model.query.filter_by(region_id=target.id).count() == 1
    assert WeatherObservation.query.filter_by(region_id=target.id).one().precipitation_mm == 2.0
    assert MonthlyClimateSummary.query.filter_by(region_id=target.id).one().total_rain_mm == 90


def test_delete_region_removes_dependents(client):
    from app.models import RiskAssessment, MonthlyClimateSummary, PowerOutageReport
    from app.services.region_maintenance import delete_region_with_dependents
    reg = Region(municipality="Chuí", name="Espúria", latitude=-33.6, longitude=-53.4)
    db.session.add(reg)
    db.session.commit()
    reg_id = reg.id
    _seed_region_rows(reg_id, datetime(2026, 11, 19, 12))

    deleted = delete_region_with_dependents(reg)

    assert db.session.get(Region, reg_id) is None
    assert deleted["alerts"] == 1 and deleted["power_outage_reports"] == 1
    for model in (WeatherObservation, Alert, RiskAssessment, Occurrence, MonthlyClimateSummary, PowerOutageReport):
        assert model.query.filter_by(region_id=reg_id).count() == 0
    assert Region.query.count() == 1


# --- Deduplicação de alertas por janela de 12h ---

def test_alert_repeat_window_ignores_utc_day_turnover(client):
    """Alerta de ontem (formato antigo de chave) 1h atrás segura a regra; após 12h volta a disparar."""
    from datetime import timedelta
    reg = Region.query.first()
    now = datetime.now(timezone.utc)
    db.session.add(WeatherObservation(region_id=reg.id, observed_at=now + timedelta(hours=1),
                                      is_forecast=True, precipitation_mm=40.0, source="modelo"))
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    previous = Alert(region_id=reg.id, triggered_rule="chuva", channel="painel", status="ok",
                     created_at=(now - timedelta(hours=1)).replace(tzinfo=None),
                     dedup_key=f"reg{reg.id}_CHUVA_INTENSA_24H_{yesterday}")
    db.session.add(previous)
    db.session.commit()

    def rain_alerts(alerts):
        return [a for a in alerts if "_CHUVA_INTENSA_24H_" in a.dedup_key]

    assert rain_alerts(evaluate_region_alerts(reg)) == []

    previous.created_at = (now - timedelta(hours=13)).replace(tzinfo=None)
    db.session.commit()
    created = rain_alerts(evaluate_region_alerts(reg))
    assert len(created) == 1
    assert created[0].dedup_key.startswith(f"reg{reg.id}_CHUVA_INTENSA_24H_")
    assert created[0].dedup_key != previous.dedup_key

    # Logo em seguida (mesmo ciclo ou o próximo): não repete.
    assert rain_alerts(evaluate_region_alerts(reg)) == []


# --- /api/weather-summary sem N+1 ---

def test_weather_summary_batches_queries_and_picks_latest(client):
    from datetime import timedelta
    from sqlalchemy import event
    from app.models import RiskAssessment
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).replace(tzinfo=None)
    first = Region.query.first()
    regions = [first]
    for i in range(5):
        r = Region(municipality="Pelotas", name=f"R{i}", latitude=-31.7, longitude=-52.3)
        db.session.add(r)
        regions.append(r)
    db.session.commit()
    empty = Region(municipality="Chuí", name="Sem dados", latitude=-33.7, longitude=-53.4)
    db.session.add(empty)
    db.session.commit()

    for idx, r in enumerate(regions):
        db.session.add_all([
            WeatherObservation(region_id=r.id, observed_at=hour - timedelta(hours=2), is_forecast=False,
                               temperature_c=10.0, source="m"),
            WeatherObservation(region_id=r.id, observed_at=hour - timedelta(hours=1), is_forecast=False,
                               temperature_c=20.0 + idx, wind_direction=90.0, source="m"),
            # Previsão vencida não entra; 30 horas futuras, só as 24 primeiras contam.
            WeatherObservation(region_id=r.id, observed_at=hour - timedelta(hours=1), is_forecast=True,
                               precipitation_mm=99.0, source="m"),
        ])
        for h in range(30):
            db.session.add(WeatherObservation(region_id=r.id, observed_at=hour + timedelta(hours=h),
                                              is_forecast=True, precipitation_mm=1.0,
                                              rain_probability=40.0 + h, source="m"))
        db.session.add_all([
            RiskAssessment(region_id=r.id, assessed_at=hour - timedelta(hours=3), risk_level="Alto",
                           score=60, method_version="t"),
            RiskAssessment(region_id=r.id,
                           assessed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10),
                           risk_level="Moderado",
                           score=30 + idx, method_version="t"),
        ])
    db.session.commit()

    engine = db.engine
    count = {"n": 0}

    def on_exec(*a, **k):
        count["n"] += 1
    event.listen(engine, "before_cursor_execute", on_exec)
    try:
        data = client.get("/api/weather-summary?format=list").get_json()
    finally:
        event.remove(engine, "before_cursor_execute", on_exec)

    assert count["n"] <= 4
    by_id = {row["region_id"]: row for row in data}
    assert len(by_id) == 7
    for idx, r in enumerate(regions):
        row = by_id[r.id]
        assert row["temperature_c"] == 20.0 + idx
        assert row["wind_direction"] == 90.0
        assert row["rain_next_24h_mm"] == 24.0
        assert row["rain_probability"] == 40.0
        assert row["risk_level"] == "Moderado" and row["risk_score"] == 30 + idx
        assert row["observed_at"].endswith("+00:00")
    blank = by_id[empty.id]
    assert blank["temperature_c"] is None and blank["observed_at"] is None
    assert blank["rain_next_24h_mm"] == 0
    # Sem avaliação = sem dado, nunca "Baixo"/0.
    assert blank["risk_level"] is None and blank["risk_score"] is None
    assert blank["risk_assessed_at"] is None


def test_weather_summary_hides_stale_risk(client):
    from datetime import timedelta
    from app.models import RiskAssessment
    from app.routes.main import RISK_MAX_AGE_MINUTES
    region = Region.query.first()
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=RISK_MAX_AGE_MINUTES + 5)
    db.session.add(RiskAssessment(region_id=region.id, assessed_at=old, risk_level="Alto",
                                  score=61, method_version="t"))
    db.session.commit()

    row = next(r for r in client.get("/api/weather-summary?format=list").get_json()
               if r["region_id"] == region.id)
    # Avaliação velha não aparece como atual; a data fica para quem quiser mostrar.
    assert row["risk_level"] is None and row["risk_score"] is None
    assert row["risk_assessed_at"].startswith(old.isoformat()[:16])


def test_map_risk_colors_cover_engine_levels():
    # O mapa já esperou "Médio" (o motor gera "Moderado") e comparava o texto exato,
    # deixando Moderado, "(Provisório)" e Inconclusivo verdes.
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "'Médio'" not in html
    assert "function riskBaseLevel" in html
    assert "startsWith('moderado')" in html


# --- GET /api/risk não grava e não devolve avaliação velha ---

def test_risk_get_never_writes_and_recomputes_stale(client):
    from datetime import timedelta
    from app.models import RiskAssessment
    reg = Region.query.first()
    now = datetime.now(timezone.utc)
    db.session.add(WeatherObservation(region_id=reg.id, observed_at=now + timedelta(hours=1),
                                      is_forecast=True, precipitation_mm=1.0, source="m"))
    db.session.commit()

    # Sem avaliação: calcula na hora, sem gravar.
    data = client.get(f"/api/risk/{reg.id}").get_json()
    assert data["calculado_agora"] is True
    assert data["score"] is not None
    assert RiskAssessment.query.count() == 0

    # Avaliação recente do agendador: devolvida como está.
    db.session.add(RiskAssessment(region_id=reg.id, assessed_at=(now - timedelta(minutes=10)).replace(tzinfo=None),
                                  risk_level="Alto", score=61, method_version="agendador"))
    db.session.commit()
    data = client.get(f"/api/risk/{reg.id}").get_json()
    assert data["calculado_agora"] is False
    assert data["score"] == 61 and data["method_version"] == "agendador"

    # Avaliação velha (agendador parado): recalcula sem gravar.
    RiskAssessment.query.update({"assessed_at": (now - timedelta(days=21)).replace(tzinfo=None)})
    db.session.commit()
    data = client.get(f"/api/risk/{reg.id}").get_json()
    assert data["calculado_agora"] is True
    assert data["method_version"] != "agendador"
    assert RiskAssessment.query.count() == 1


def test_risk_engine_persist_flag(client):
    from app.models import RiskAssessment
    from app.services.risk_engine import calculate_region_risk
    reg = Region.query.first()
    calculate_region_risk(reg, persist=False)  # sem dados: Inconclusivo
    assert RiskAssessment.query.count() == 0
    calculate_region_risk(reg)
    assert RiskAssessment.query.count() == 1


# --- Catálogo único de regiões ---

def test_region_catalog_is_56_unique_regions():
    from collections import Counter
    from app.regions_catalog import REGIONS
    assert len(REGIONS) == 56
    assert len({r["name"] for r in REGIONS}) == 56
    assert set(Counter(r["city"] for r in REGIONS).values()) == {7}


def test_scripts_use_the_shared_catalog():
    from pathlib import Path
    from app.regions_catalog import REGIONS
    import scripts.seed_regions as seed_regions
    assert seed_regions.ALL_REGIONS is REGIONS
    # cleanup_all_regions roda ao ser importado: confere pelo texto.
    cleanup = Path("scripts/cleanup_all_regions.py").read_text(encoding="utf-8")
    assert "CANONICAL_REGIONS = REGIONS" in cleanup and "ALIASES = LEGACY_REGION_NAMES" in cleanup
    assert '{"name":' not in cleanup  # nenhuma região escrita à mão no script


def test_legacy_names_point_to_catalog_regions():
    from app.regions_catalog import LEGACY_REGION_NAMES, REGION_NAMES
    for (city, _old), new in LEGACY_REGION_NAMES.items():
        assert (city, new) in REGION_NAMES


def test_historical_occurrences_resolve_to_catalog_regions():
    from scripts.seed_occurrences import HISTORICAL_EVENTS
    from app.regions_catalog import resolve_region_name
    unresolved = [(e["municipality"], e["region_name"]) for e in HISTORICAL_EVENTS
                  if resolve_region_name(e["municipality"], e["region_name"]) is None]
    assert unresolved == []


def test_readme_region_list_matches_catalog():
    import re
    from pathlib import Path
    from app.regions_catalog import REGIONS
    expected = {}
    for r in REGIONS:
        m = re.match(r".*\((.+)\)$", r["name"])
        expected.setdefault(r["city"], set()).add(m.group(1) if m else r["name"])
    readme = Path("README.md").read_text(encoding="utf-8")
    listed = {city: {i.strip() for i in items.split(",")}
              for city, items in re.findall(r"^\d\. \*\*(.+?):\*\* (.+)\.$", readme, re.M)}
    assert listed == expected


# --- Migrations (Flask-Migrate) ---

def _file_db_app(tmp_path, name):
    class FileConfig:
        TESTING = True
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / name).as_posix()
        SQLALCHEMY_TRACK_MODIFICATIONS = False
    return create_app(FileConfig)


def _schema_diffs():
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    with db.engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"render_as_batch": True})
        return compare_metadata(ctx, db.metadata)


def test_migrations_build_exactly_the_models(tmp_path):
    """Falha se algum modelo mudar sem uma migration correspondente (flask db migrate)."""
    from sqlalchemy import inspect
    from app.db_schema import ensure_schema
    app = _file_db_app(tmp_path, "novo.db")
    with app.app_context():
        assert ensure_schema() == "criado"
        assert _schema_diffs() == []
        assert "station_observations" in inspect(db.engine).get_table_names()
        assert ensure_schema() == "atualizado"
        db.engine.dispose()


def test_legacy_create_all_database_is_stamped_and_upgraded(tmp_path):
    """Banco antigo (create_all, sem alembic_version, sem as tabelas do INMET) não é recriado."""
    from sqlalchemy import inspect, text
    from app.db_schema import ensure_schema
    app = _file_db_app(tmp_path, "legado.db")
    with app.app_context():
        later_tables = {"station_observations", "official_warnings", "official_forecasts"}  # migrations 0002–0004
        legacy_tables = [t for name, t in db.metadata.tables.items() if name not in later_tables]
        db.metadata.create_all(db.engine, tables=legacy_tables)
        db.session.add(Region(municipality="Pelotas", name="Pelotas (Centro)", latitude=-31.7, longitude=-52.3))
        db.session.commit()

        assert ensure_schema() == "legado_marcado"
        assert Region.query.count() == 1
        assert later_tables <= set(inspect(db.engine).get_table_names())
        with db.engine.connect() as conn:
            assert conn.execute(text("select version_num from alembic_version")).scalar() == "0004_official_forecasts"
        assert _schema_diffs() == []
        db.session.remove()
        db.engine.dispose()


def test_carto_key_comes_from_config_not_from_template():
    """A chave da CARTO sai do .env (CARTO_BASEMAPS_KEY); o template não traz chave fixa."""
    from pathlib import Path
    template = (Path(__file__).resolve().parent.parent / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "png?key=" not in template

    class KeyConfig(TestConfig):
        CARTO_BASEMAPS_KEY = "chave-de-teste_123"

    class NoKeyConfig(TestConfig):
        CARTO_BASEMAPS_KEY = ""

    for cfg, expected in ((KeyConfig, 'const cartoKey = "chave-de-teste_123";'), (NoKeyConfig, 'const cartoKey = "";')):
        app = create_app(cfg)
        with app.app_context():
            db.create_all()
            html = app.test_client().get("/").get_data(as_text=True)
            db.drop_all()
        assert expected in html


# --- Avisos oficiais do INMET ---

def _aviso(inmet_id="56455", geocodes="4314407,4315602,5200050", inicio="2026-11-20 09:06",
           fim="2026-11-20 23:59", seq="1", **extra):
    item = {"id": inmet_id, "id_aviso": "28733", "id_sequencia": seq, "descricao": "Tempestade",
            "severidade": "Perigo Potencial", "id_severidade": 6, "aviso_cor": "#FFFE00",
            "inicio": inicio, "fim": fim, "geocodes": geocodes, "encerrado": "False",
            "riscos": ["Chuva entre 20 e 30 mm/h."], "instrucoes": ["Evite áreas alagadas. ", "Ligue 199."]}
    item.update(extra)
    return item


class _AvisosResp:
    def __init__(self, payload, headers=None):
        import json
        self.status_code = payload if isinstance(payload, int) else 200
        self._payload = payload
        self.content = b"" if isinstance(payload, int) else json.dumps(payload).encode()
        self.headers = headers or {}

    def json(self):
        return self._payload


def _fake_avisos(monkeypatch, payloads):
    """
    Cada GET devolve o próximo payload (dict = 200 JSON; int = status HTTP). O HEAD responde
    405 (sem tamanho), então todo ciclo baixa a lista completa.
    """
    from app.collectors import inmet_avisos
    inmet_avisos.reset_download_state()
    queue = list(payloads)
    monkeypatch.setattr(inmet_avisos.http_session, "get", lambda url, timeout=None: _AvisosResp(queue.pop(0)))
    monkeypatch.setattr(inmet_avisos.http_session, "head", lambda url, **kw: _AvisosResp(405))
    sent = []
    monkeypatch.setattr(inmet_avisos, "send_email_notification", lambda s, b: sent.append((s, b)) or "enviado_email")
    return sent


def test_parse_warning_matches_by_geocode_and_converts_brasilia_time():
    from app.collectors.inmet_avisos import parse_warning
    w = parse_warning(_aviso())
    assert w["geocodes"] == "4315602,4314407"  # só os monitorados, na ordem do catálogo
    assert w["starts_at"] == datetime(2026, 11, 20, 12, 6)  # 09:06 BRT = 12:06 UTC
    assert w["ends_at"] == datetime(2026, 11, 21, 2, 59)
    assert w["instructions"] == "Evite áreas alagadas.\nLigue 199."
    assert w["sequence"] == 1 and w["severity_level"] == 6
    assert parse_warning(_aviso(geocodes="5200050,3100104")) is None   # fora da Zona Sul
    assert parse_warning(_aviso(encerrado="True")) is None
    assert parse_warning(_aviso(inicio="")) is None
    assert parse_warning(_aviso(inmet_id="1/../x")) is None


def test_every_catalog_municipality_has_geocode():
    from app.regions_catalog import REGIONS, MUNICIPALITY_GEOCODES
    assert {r["city"] for r in REGIONS} == set(MUNICIPALITY_GEOCODES)


def test_sync_official_warnings_upserts_withdraws_and_notifies_once(client, monkeypatch):
    from app.collectors.inmet_avisos import sync_official_warnings
    from app.models import OfficialWarning
    now = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
    v2 = _aviso(inmet_id="56470", seq="2")
    sent = _fake_avisos(monkeypatch, [
        {"hoje": [], "futuro": [_aviso(), _aviso(inmet_id="99", geocodes="5200050")]},
        {"hoje": [], "futuro": [_aviso()]},
        500,                                   # falha: não retira nada
        {"hoje": [v2], "futuro": []},          # nova versão substitui a anterior
    ])
    first = sync_official_warnings(now=now)
    assert first == {"status": "ok", "downloaded": True, "active": 1, "new": 1, "withdrawn": 0, "notified": 1}
    assert "Tempestade" in sent[0][0] and "Rio Grande, Pelotas" in sent[0][1]
    assert "20/11/2026 09:06" in sent[0][1]

    second = sync_official_warnings(now=now)
    assert second["new"] == 0 and second["notified"] == 0 and len(sent) == 1

    assert sync_official_warnings(now=now)["status"] == "falha"
    assert OfficialWarning.query.filter(OfficialWarning.withdrawn_at.is_(None)).count() == 1

    fourth = sync_official_warnings(now=now)
    assert fourth["new"] == 1 and fourth["withdrawn"] == 1 and len(sent) == 2
    assert "versão 2" in sent[1][0]
    assert OfficialWarning.query.filter_by(inmet_id="56455").one().withdrawn_at is not None


def test_sync_retries_email_after_smtp_error(client, monkeypatch):
    from app.collectors import inmet_avisos
    now = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
    _fake_avisos(monkeypatch, [{"hoje": [_aviso()], "futuro": []}] * 2)
    statuses = iter(["erro_envio", "enviado_email"])
    monkeypatch.setattr(inmet_avisos, "send_email_notification", lambda s, b: next(statuses))
    assert inmet_avisos.sync_official_warnings(now=now)["notified"] == 0
    assert inmet_avisos.sync_official_warnings(now=now)["notified"] == 1


def test_sync_rejects_unexpected_format(client, monkeypatch):
    from app.collectors.inmet_avisos import sync_official_warnings
    _fake_avisos(monkeypatch, [{"avisos": []}, ["x"]])
    assert sync_official_warnings()["status"] == "falha"
    assert sync_official_warnings()["status"] == "falha"


def test_official_warnings_api_and_region_weather(client, monkeypatch):
    from datetime import timedelta
    from app.collectors.inmet_avisos import sync_official_warnings
    brt = timezone(timedelta(hours=-3))
    now_brt = datetime.now(timezone.utc).astimezone(brt)
    fmt = "%Y-%m-%d %H:%M"
    em_vigor = _aviso(inicio=(now_brt - timedelta(hours=1)).strftime(fmt), fim=(now_brt + timedelta(hours=5)).strftime(fmt))
    futuro = _aviso(inmet_id="56456", geocodes="4314407",
                    inicio=(now_brt + timedelta(hours=10)).strftime(fmt), fim=(now_brt + timedelta(hours=20)).strftime(fmt))
    vencido = _aviso(inmet_id="56457", inicio=(now_brt - timedelta(hours=9)).strftime(fmt),
                     fim=(now_brt - timedelta(hours=1)).strftime(fmt))
    _fake_avisos(monkeypatch, [{"hoje": [em_vigor, vencido], "futuro": [futuro]}])
    sync_official_warnings()

    listed = client.get("/api/official-warnings").get_json()["warnings"]
    assert [w["id"] for w in listed] == ["56455", "56456"]
    assert listed[0]["in_effect"] is True and listed[1]["in_effect"] is False
    assert listed[0]["municipalities"] == ["Rio Grande", "Pelotas"]
    assert listed[0]["link"] == "https://avisos.inmet.gov.br/56455"
    assert listed[0]["starts_at"].endswith("+00:00")

    region_id = Region.query.first().id  # região de Rio Grande: o aviso só de Pelotas não entra
    weather = client.get(f"/api/weather/{region_id}").get_json()
    assert [w["id"] for w in weather["official_warnings"]] == ["56455"]


def test_prune_removes_old_official_warnings(client):
    from datetime import timedelta
    from app.models import OfficialWarning
    from app.services.pruning_service import prune_old_official_warnings
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for inmet_id, ended_days_ago in (("1", 10), ("2", 2)):
        db.session.add(OfficialWarning(
            inmet_id=inmet_id, warning_number=inmet_id, event="Tempestade", severity="Perigo",
            starts_at=now - timedelta(days=ended_days_ago, hours=5), ends_at=now - timedelta(days=ended_days_ago),
            geocodes="4314407", first_seen_at=now, last_seen_at=now))
    db.session.commit()
    assert prune_old_official_warnings() == 1
    assert [w.inmet_id for w in OfficialWarning.query.all()] == ["2"]


def test_sync_uses_head_and_downloads_only_on_change_or_hourly(client, monkeypatch):
    import requests
    from datetime import timedelta
    from app.collectors import inmet_avisos
    monkeypatch.delenv("INMET_AVISOS_FULL_REFRESH_MINUTES", raising=False)
    _fake_avisos(monkeypatch, [])
    payload = {"hoje": [_aviso()], "futuro": []}
    size = len(_AvisosResp(payload).content)
    gets, heads = [], []
    head_answers = []

    def fake_get(url, timeout=None):
        gets.append(url)
        return _AvisosResp(payload)

    def fake_head(url, **kw):
        heads.append(url)
        answer = head_answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return _AvisosResp(200, headers={"Content-Length": str(answer)})

    monkeypatch.setattr(inmet_avisos.http_session, "get", fake_get)
    monkeypatch.setattr(inmet_avisos.http_session, "head", fake_head)
    t0 = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)

    assert inmet_avisos.sync_official_warnings(now=t0)["downloaded"] is True       # 1º ciclo: sem HEAD
    assert (len(gets), len(heads)) == (1, 0)

    head_answers.append(size)                                                       # tamanho igual
    same = inmet_avisos.sync_official_warnings(now=t0 + timedelta(minutes=15))
    assert same["status"] == "sem_mudanca" and same["downloaded"] is False
    assert (len(gets), len(heads)) == (1, 1)

    head_answers.append(size + 7)                                                   # tamanho mudou
    assert inmet_avisos.sync_official_warnings(now=t0 + timedelta(minutes=30))["downloaded"] is True
    assert len(gets) == 2

    head_answers.append(requests.ConnectionError("x"))                              # HEAD falhou
    assert inmet_avisos.sync_official_warnings(now=t0 + timedelta(minutes=45))["downloaded"] is True
    assert len(gets) == 3

    head_answers.append(size)                                                       # igual, < 60 min
    assert inmet_avisos.sync_official_warnings(now=t0 + timedelta(minutes=90))["downloaded"] is False
    assert inmet_avisos.sync_official_warnings(now=t0 + timedelta(minutes=105))["downloaded"] is True  # 60 min: sem HEAD
    assert (len(gets), len(heads)) == (4, 4)


def test_sync_does_not_remember_failed_download(client, monkeypatch):
    from datetime import timedelta
    from app.collectors import inmet_avisos
    _fake_avisos(monkeypatch, [500, {"hoje": [_aviso()], "futuro": []}])
    monkeypatch.setattr(inmet_avisos.http_session, "head",
                        lambda url, **kw: _AvisosResp(200, headers={"Content-Length": "123"}))
    t0 = datetime(2026, 11, 20, 10, tzinfo=timezone.utc)
    assert inmet_avisos.sync_official_warnings(now=t0)["status"] == "falha"
    assert inmet_avisos.sync_official_warnings(now=t0 + timedelta(minutes=15))["downloaded"] is True


def test_full_refresh_minutes_from_env(monkeypatch):
    from app.collectors.inmet_avisos import get_full_refresh_minutes
    monkeypatch.setenv("INMET_AVISOS_FULL_REFRESH_MINUTES", "30")
    assert get_full_refresh_minutes() == 30
    for bad in ("abc", "0", "-5"):
        monkeypatch.setenv("INMET_AVISOS_FULL_REFRESH_MINUTES", bad)
        assert get_full_refresh_minutes() == 60


# --- Previsão oficial do INMET por município ---

def _turno(resumo, tmin=18, tmax=31, **extra):
    return {"uf": "RS", "entidade": "Rio Grande", "resumo": resumo, "temp_max": tmax, "temp_min": tmin,
            "dir_vento": "NE-E", "int_vento": "Fracos", "cod_icone": "34",
            "icone": "data:image/png;base64,AAAA", "umidade_max": 100, "umidade_min": 50, **extra}


def _previsao(geocode="4315602", resumo="Muitas nuvens"):
    return {geocode: {
        "20/11/2026": {"manha": _turno(resumo), "tarde": _turno("Chuva isolada"), "noite": _turno("Nublado")},
        "22/11/2026": _turno("Muitas nuvens com possibilidade de chuva isolada", tmin=17, tmax=32),
        "xx/11/2026": _turno("data inválida"),
    }}


def _fake_previsao(monkeypatch, responder):
    """responder(geocode) -> dict (200 JSON) ou int (status HTTP)."""
    from app.collectors import inmet_previsao
    calls = []

    def fake_get(url, timeout=None):
        geocode = url.rsplit("/", 1)[-1]
        calls.append(geocode)
        return _AvisosResp(responder(geocode))
    monkeypatch.setattr(inmet_previsao.http_session, "get", fake_get)
    return calls


def test_parse_forecast_periods_flat_days_and_drops_icons():
    from datetime import date
    from app.collectors.inmet_previsao import parse_forecast
    rows = parse_forecast(_previsao(), "4315602")
    assert [(r["forecast_date"], r["period"]) for r in rows] == [
        (date(2026, 11, 20), "manha"), (date(2026, 11, 20), "tarde"), (date(2026, 11, 20), "noite"),
        (date(2026, 11, 22), "dia"),
    ]
    assert rows[0]["summary"] == "Muitas nuvens" and rows[0]["temp_max"] == 31.0
    assert rows[3]["temp_min"] == 17.0 and rows[0]["icon_code"] == "34"
    assert all("icone" not in r for r in rows)
    assert parse_forecast(_previsao(), "4314407") is None       # geocode de outro município
    assert parse_forecast({"4315602": {}}, "4315602") is None
    assert parse_forecast(["x"], "4315602") is None


def test_sync_official_forecasts_replaces_respects_interval_and_keeps_on_failure(client, monkeypatch):
    from datetime import timedelta
    from app.collectors.inmet_previsao import sync_official_forecasts
    from app.models import OfficialForecast
    monkeypatch.delenv("INMET_PREVISAO_HOURS", raising=False)
    now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    calls = _fake_previsao(monkeypatch, lambda g: _previsao(g))

    assert sync_official_forecasts(now=now) == {"updated": 8, "skipped": 0, "failed": 0}
    assert OfficialForecast.query.count() == 8 * 4

    # Dentro do intervalo de 3 h: nenhuma consulta.
    assert sync_official_forecasts(now=now + timedelta(hours=2)) == {"updated": 0, "skipped": 8, "failed": 0}
    assert len(calls) == 8

    # Depois do intervalo: substitui (não acumula); falha de um município mantém a previsão dele.
    _fake_previsao(monkeypatch, lambda g: 500 if g == "4314407" else _previsao(g, resumo="Céu claro"))
    assert sync_official_forecasts(now=now + timedelta(hours=4)) == {"updated": 7, "skipped": 0, "failed": 1}
    assert OfficialForecast.query.count() == 8 * 4
    pelotas = OfficialForecast.query.filter_by(geocode="4314407", period="manha").one()
    assert pelotas.summary == "Muitas nuvens" and pelotas.fetched_at == datetime(2026, 11, 20, 12)
    rio_grande = OfficialForecast.query.filter_by(geocode="4315602", period="manha").one()
    assert rio_grande.summary == "Céu claro"


def test_region_weather_includes_official_forecast_from_today(client, monkeypatch):
    from datetime import timedelta
    from app.collectors.inmet_previsao import sync_official_forecasts
    brt = timezone(timedelta(hours=-3))
    today = datetime.now(timezone.utc).astimezone(brt).date()
    fmt = lambda d: d.strftime("%d/%m/%Y")
    payload = lambda g: {g: {
        fmt(today - timedelta(days=1)): _turno("Ontem"),
        fmt(today): {"manha": _turno("Manhã nublada"), "noite": _turno("Noite limpa")},
        fmt(today + timedelta(days=2)): _turno("Depois de amanhã", tmin=15, tmax=24),
    }}
    _fake_previsao(monkeypatch, payload)
    sync_official_forecasts()

    region_id = Region.query.first().id  # Rio Grande
    fc = client.get(f"/api/weather/{region_id}").get_json()["official_forecast"]
    assert fc["municipality"] == "Rio Grande" and fc["fetched_at"].endswith("+00:00")
    assert [d["date"] for d in fc["days"]] == [today.isoformat(), (today + timedelta(days=2)).isoformat()]
    assert [p["period"] for p in fc["days"][0]["periods"]] == ["manha", "noite"]
    assert fc["days"][1]["temp_min"] == 15 and fc["days"][1]["periods"][0]["period"] == "dia"


def test_region_weather_without_official_forecast(client):
    region_id = Region.query.first().id
    assert client.get(f"/api/weather/{region_id}").get_json()["official_forecast"] is None


def test_forecast_interval_env(monkeypatch):
    from app.collectors.inmet_previsao import get_interval_hours
    monkeypatch.setenv("INMET_PREVISAO_HOURS", "6")
    assert get_interval_hours() == 6
    for bad in ("abc", "0", "-1"):
        monkeypatch.setenv("INMET_PREVISAO_HOURS", bad)
        assert get_interval_hours() == 3
