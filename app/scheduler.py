import logging
import os
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from app.collectors.open_meteo import collect_all_regions
from app.collectors.traffic_collector import update_highway_conditions
from app.services.alert_engine import run_alert_engine, evaluate_and_record_alerts
from app.models import WeatherObservation

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def run_scheduled_cycle(app):
    """
    Executa o ciclo periódico completo:
    1. Coleta meteorológica Open-Meteo
    2. Avaliação de risco e geração de alertas
    3. Atualização de telemetria dos corredores de tráfego
    """
    with app.app_context():
        try:
            print("[SCHEDULER] 1. Executando coleta meteorológica das 56 regiões...")
            logger.info("[SCHEDULER] 1. Executando coleta meteorológica das 56 regiões...")
            collect_all_regions()

            print("[SCHEDULER] 2. Avaliando índices de risco e gerando alertas...")
            logger.info("[SCHEDULER] 2. Avaliando índices de risco e gerando alertas...")
            run_alert_engine()

            print("[SCHEDULER] 3. Atualizando telemetria dos 7 corredores de tráfego...")
            logger.info("[SCHEDULER] 3. Atualizando telemetria dos 7 corredores de tráfego...")
            update_highway_conditions()

            print("[SCHEDULER] Ciclo concluído com sucesso!")
            logger.info("[SCHEDULER] Ciclo concluído com sucesso!")
        except Exception as e:
            print(f"[SCHEDULER ERROR] Falha no ciclo agendado: {e}")
            logger.error("[SCHEDULER ERROR] Falha no ciclo agendado: %s", e)


def init_scheduler(app):
    """
    Inicia o agendador em segundo plano a cada 15 minutos (padrão)
    e avalia alertas automaticamente após cada coleta.
    """
    if app.config.get("TESTING"):
        return

    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        interval_minutes = int(os.getenv("COLLECT_INTERVAL_MINUTES", "15"))

        def scheduled_job():
            run_scheduled_cycle(app)

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
