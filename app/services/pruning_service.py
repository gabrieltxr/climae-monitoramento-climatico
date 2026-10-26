import logging
from datetime import datetime, timedelta, timezone
from app.models import db, WeatherObservation, RiskAssessment

logger = logging.getLogger(__name__)


def prune_expired_forecasts(retention_hours: int = 24) -> int:
    """
    Remove previsões cujo horário previsto já ficou para trás há mais de retention_hours.
    Essas previsões não são mais consultadas pelo painel e são substituídas pelas observações reais.
    """
    cutoff_utc = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
    cutoff_naive = cutoff_utc.replace(tzinfo=None)

    deleted = (
        WeatherObservation.query
        .filter(
            WeatherObservation.is_forecast == True,
            WeatherObservation.observed_at < cutoff_naive
        )
        .delete(synchronize_session=False)
    )
    db.session.commit()
    logger.info("Purga de previsões expiradas (> %dh): %d registros removidos.", retention_hours, deleted)
    return deleted


def prune_old_risk_assessments(retention_days: int = 30) -> int:
    """
    Remove avaliações de risco com mais de retention_days.
    O sistema mantém apenas os registros recentes para o painel em tempo real.
    """
    cutoff_utc = datetime.now(timezone.utc) - timedelta(days=retention_days)
    cutoff_naive = cutoff_utc.replace(tzinfo=None)

    deleted = (
        RiskAssessment.query
        .filter(RiskAssessment.assessed_at < cutoff_naive)
        .delete(synchronize_session=False)
    )
    db.session.commit()
    logger.info("Purga de avaliações de risco antigas (> %dd): %d registros removidos.", retention_days, deleted)
    return deleted


def prune_expired_data(forecast_retention_hours: int = 24, risk_retention_days: int = 30) -> dict:
    """Executa a limpeza geral de registros temporários expirados."""
    try:
        deleted_fc = prune_expired_forecasts(forecast_retention_hours)
        deleted_risk = prune_old_risk_assessments(risk_retention_days)
        return {
            "deleted_forecasts": deleted_fc,
            "deleted_risk_assessments": deleted_risk,
            "status": "success"
        }
    except Exception as e:
        db.session.rollback()
        logger.error("Erro durante purga de dados: %s", e)
        return {"error": str(e), "status": "failed"}
