import logging
import os
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from app.collectors.open_meteo import collect_all_regions
from app.models import WeatherObservation

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def init_scheduler(app):
    """
    Inicia o agendador em segundo plano a cada 15 minutos (padrão)
    e avalia alertas automaticamente após cada coleta.
    """
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        interval_minutes = int(os.getenv("COLLECT_INTERVAL_MINUTES", "15"))

        def scheduled_job():
            with app.app_context():
                logger.info("Iniciando ciclo automático de 15 min (Open-Meteo + Rodovias + Alertas)...")
                result = collect_all_regions()
                try:
                    from app.collectors.institutional_traffic import update_institutional_traffic
                    from app.services.alert_engine import evaluate_alerts_all_regions
                    update_institutional_traffic()
                    evaluate_alerts_all_regions()
                except Exception as exc:
                    logger.error("Erro ao processar trânsito/alertas: %s", exc)
                logger.info("Ciclo automático concluído: %s", result)

        # Verifica ao ligar o servidor se os dados têm mais de 15 min; se sim, atualiza
        with app.app_context():
            try:
                last_obs = (
                    WeatherObservation.query
                    .filter_by(is_forecast=False)
                    .order_by(WeatherObservation.collected_at.desc())
                    .first()
                )
                now_utc = datetime.now(timezone.utc)
                should_collect = False
                if not last_obs or not last_obs.collected_at:
                    should_collect = True
                else:
                    col_time = last_obs.collected_at
                    if col_time.tzinfo is None:
                        col_time = col_time.replace(tzinfo=timezone.utc)
                    if (now_utc - col_time) > timedelta(minutes=interval_minutes):
                        should_collect = True

                if should_collect:
                    logger.info("Dados ausentes ou > %d min. Executando coleta inicial...", interval_minutes)
                    scheduled_job()
            except Exception:
                pass

        if not scheduler.running:
            scheduler.add_job(
                func=scheduled_job,
                trigger="interval",
                minutes=interval_minutes,
                id="open_meteo_collector",
                replace_existing=True,
            )
            scheduler.start()
            logger.info("APScheduler iniciado (ciclo: %d min).", interval_minutes)
