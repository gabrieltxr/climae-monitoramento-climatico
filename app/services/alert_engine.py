import logging
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from app.models import db, Region, WeatherObservation, Alert
from app.services.risk_engine import calculate_region_risk

logger = logging.getLogger(__name__)

# Limiares configuráveis via .env (com padrões seguros para o MVP)
RAIN_ALERT_MM = float(os.getenv("ALERT_RAIN_THRESHOLD_MM", "25.0"))       # Acumulado 24h >= 25mm
WIND_ALERT_KMH = float(os.getenv("ALERT_WIND_THRESHOLD_KMH", "55.0"))     # Vento >= 55 km/h
RISK_ALERT_SCORE = float(os.getenv("ALERT_RISK_THRESHOLD_SCORE", "50.0")) # Score >= 50 (Alto / Muito alto)


def send_email_notification(subject: str, body: str) -> str:
    """
    Envia notificação por e-mail se SMTP_HOST e ALERT_EMAIL_TO estiverem no .env;
    caso contrário, registra o disparo em log/painel e marca como 'registrado_painel'.
    """
    smtp_host = os.getenv("SMTP_HOST", "").strip()
    email_to = os.getenv("ALERT_EMAIL_TO", "").strip()

    if not smtp_host or not email_to:
        logger.info("[ALERTA PAINEL] %s — %s", subject, body)
        return "registrado_painel"

    try:
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_pass = os.getenv("SMTP_PASS", "")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = smtp_user or "alertas@climae-rs.local"
        msg["To"] = email_to
        msg.set_content(body)

        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return "enviado_email"
    except Exception as exc:
        logger.error("Falha ao enviar e-mail de alerta: %s", exc)
        return "erro_envio"


def evaluate_region_alerts(region: Region) -> list[Alert]:
    """
    Avalia regras de chuva intensa, vendaval e risco elevado para uma região
    (incluindo as últimas 12h recuperadas caso o servidor estivesse desligado),
    garantindo deduplicação por dia/regra/região (campo dedup_key).
    """
    now_utc = datetime.now(timezone.utc)
    current_hour_floor = now_utc.replace(minute=0, second=0, microsecond=0)
    past_12h_cutoff = now_utc - timedelta(hours=12)
    day_key = now_utc.strftime("%Y-%m-%d")

    # 1. Previsões futuras (próximas 24h)
    forecasts = (
        WeatherObservation.query
        .filter(
            WeatherObservation.region_id == region.id,
            WeatherObservation.is_forecast == True,
            WeatherObservation.observed_at >= current_hour_floor,
        )
        .order_by(WeatherObservation.observed_at.asc())
        .limit(24)
        .all()
    )

    # 2. Observações reais das últimas 12h (inclui horas recuperadas pós-desligamento)
    recent_observed = (
        WeatherObservation.query
        .filter(
            WeatherObservation.region_id == region.id,
            WeatherObservation.is_forecast == False,
            WeatherObservation.observed_at >= past_12h_cutoff,
        )
        .order_by(WeatherObservation.observed_at.desc())
        .all()
    )

    rain_forecast_24h = sum((f.precipitation_mm or 0.0) for f in forecasts)
    rain_past_12h = sum((o.precipitation_mm or 0.0) for o in recent_observed)
    effective_rain = max(rain_forecast_24h, rain_past_12h)

    peak_wind_forecast = max([f.wind_speed or 0.0 for f in forecasts], default=0.0)
    peak_wind_observed = max([o.wind_speed or 0.0 for o in recent_observed], default=0.0)
    peak_wind = max(peak_wind_forecast, peak_wind_observed)

    risk_assessment = calculate_region_risk(region)

    triggered_rules = []

    if effective_rain >= RAIN_ALERT_MM:
        origem_chuva = "prevista (24h)" if rain_forecast_24h >= rain_past_12h else "observada (últimas 12h)"
        triggered_rules.append((
            "CHUVA_INTENSA_24H",
            f"Chuva acumulada {origem_chuva} de {effective_rain:.1f} mm (Limiar: {RAIN_ALERT_MM} mm)"
        ))

    if peak_wind >= WIND_ALERT_KMH:
        origem_vento = "previsto" if peak_wind_forecast >= peak_wind_observed else "observado nas últimas 12h"
        triggered_rules.append((
            "VENDAVAL_PREVISTO",
            f"Vento {origem_vento} de {peak_wind:.1f} km/h (Limiar: {WIND_ALERT_KMH} km/h)"
        ))

    if risk_assessment.score is not None and risk_assessment.score >= RISK_ALERT_SCORE:
        triggered_rules.append((
            "RISCO_ELEVADO",
            f"Índice de Risco atingiu nível '{risk_assessment.risk_level}' (Score: {risk_assessment.score}/100)"
        ))

    created_alerts = []
    for rule_code, rule_desc in triggered_rules:
        dedup_key = f"reg{region.id}_{rule_code}_{day_key}"
        exists = Alert.query.filter_by(dedup_key=dedup_key).first()
        if exists:
            continue

        subject = f"[CLIMAE Alerta] {region.municipality} ({region.name}) — {rule_code}"
        status = send_email_notification(subject, rule_desc)
        channel = "email + painel" if status == "enviado_email" else "painel / log"

        new_alert = Alert(
            region_id=region.id,
            triggered_rule=rule_desc,
            created_at=now_utc,
            channel=channel,
            status=status,
            dedup_key=dedup_key,
        )
        db.session.add(new_alert)
        created_alerts.append(new_alert)

    if created_alerts:
        db.session.commit()

    return created_alerts


def evaluate_alerts_all_regions() -> int:
    regions = Region.query.all()
    total = 0
    for r in regions:
        total += len(evaluate_region_alerts(r))
    return total

# Aliases oficiais do serviço de alertas periódicos
evaluate_and_record_alerts = evaluate_alerts_all_regions
run_alert_engine = evaluate_alerts_all_regions
