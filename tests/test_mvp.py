import pytest
from datetime import datetime, timezone
from app import create_app
from app.models import db, Region, WeatherObservation, Occurrence, Alert
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
