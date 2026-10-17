from datetime import datetime, timezone
from flask import Blueprint, jsonify, render_template, request
from app.models import Region, WeatherObservation, Occurrence, RiskAssessment, Alert
from app.services.risk_engine import calculate_region_risk
from app.collectors.traffic import get_traffic_panel_data

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    regions = Region.query.order_by(Region.municipality, Region.name).all()
    return render_template('index.html', regions=regions)

@main_bp.route('/api/regions')
def api_regions():
    regions = Region.query.order_by(Region.municipality, Region.name).all()
    return jsonify([r.to_dict() for r in regions])

def to_utc_iso(dt):
    """Garante que a string ISO contenha explicitamente o indicador de timezone UTC (+00:00)."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()

@main_bp.route('/api/weather/<int:region_id>')
def api_region_weather(region_id):
    """Retorna dados atuais e previsão de 24h de uma região específica para o Dashboard e Gráficos."""
    region = Region.query.get_or_404(region_id)

    # Última observação real (is_forecast=False)
    current_obs = (
        WeatherObservation.query
        .filter_by(region_id=region.id, is_forecast=False)
        .order_by(WeatherObservation.observed_at.desc())
        .first()
    )

    # Previsão das próximas 24h (is_forecast=True) a partir da hora atual
    now_utc_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    forecasts = (
        WeatherObservation.query
        .filter_by(region_id=region.id, is_forecast=True)
        .filter(WeatherObservation.observed_at >= now_utc_hour.replace(tzinfo=None))
        .order_by(WeatherObservation.observed_at.asc())
        .limit(24)
        .all()
    )
    if not forecasts:
        forecasts = (
            WeatherObservation.query
            .filter_by(region_id=region.id, is_forecast=True)
            .order_by(WeatherObservation.observed_at.asc())
            .limit(24)
            .all()
        )

    return jsonify({
        "region": region.to_dict(),
        "current": {
            "observed_at": to_utc_iso(current_obs.observed_at) if current_obs else None,
            "collected_at": to_utc_iso(current_obs.collected_at) if current_obs and current_obs.collected_at else None,
            "temperature_c": current_obs.temperature_c if current_obs else None,
            "humidity_percent": current_obs.humidity_percent if current_obs else None,
            "precipitation_mm": current_obs.precipitation_mm if current_obs else None,
            "wind_speed_kmh": current_obs.wind_speed if current_obs else None,
            "rain_probability": forecasts[0].rain_probability if forecasts else None,
            "source": current_obs.source if current_obs else "Indisponível"
        } if current_obs else None,
        "forecast_24h": [
            {
                "time": to_utc_iso(f.observed_at),
                "temperature_c": f.temperature_c,
                "humidity_percent": f.humidity_percent,
                "precipitation_mm": f.precipitation_mm,
                "rain_probability": f.rain_probability,
                "wind_speed_kmh": f.wind_speed
            }
            for f in forecasts
        ]
    })

@main_bp.route('/api/weather-summary')
def api_weather_summary():
    """Resumo rápido de todas as regiões para renderizar a camada visual no Mapa Leaflet."""
    regions = Region.query.all()
    summary = []
    for r in regions:
        obs = (
            WeatherObservation.query
            .filter_by(region_id=r.id, is_forecast=False)
            .order_by(WeatherObservation.observed_at.desc())
            .first()
        )
        fc = (
            WeatherObservation.query
            .filter_by(region_id=r.id, is_forecast=True)
            .order_by(WeatherObservation.observed_at.asc())
            .first()
        )
        risk = (
            RiskAssessment.query
            .filter_by(region_id=r.id)
            .order_by(RiskAssessment.assessed_at.desc())
            .first()
        )
        summary.append({
            "region": r.to_dict(),
            "temperature_c": obs.temperature_c if obs else None,
            "precipitation_mm": obs.precipitation_mm if obs else 0,
            "wind_speed_kmh": obs.wind_speed if obs else 0,
            "rain_probability": fc.rain_probability if fc else 0,
            "risk_level": risk.risk_level if risk else "Inconclusivo",
            "risk_score": risk.score if risk else None,
        })
    return jsonify(summary)

@main_bp.route('/api/occurrences')
def api_occurrences():
    """Lista o histórico de ocorrências com suporte a filtros por região e período."""
    region_id = request.args.get('region_id', type=int)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    query = Occurrence.query.join(Region)

    if region_id:
        query = query.filter(Occurrence.region_id == region_id)
    if start_date:
        dt_start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        query = query.filter(Occurrence.occurred_at >= dt_start)
    if end_date:
        dt_end = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        query = query.filter(Occurrence.occurred_at <= dt_end)

    items = query.order_by(Occurrence.occurred_at.desc()).all()

    result = []
    for item in items:
        reg = Region.query.get(item.region_id)
        result.append({
            "id": item.id,
            "region_id": item.region_id,
            "municipality": reg.municipality if reg else "",
            "region_name": reg.name if reg else "",
            "latitude": reg.latitude if reg else None,
            "longitude": reg.longitude if reg else None,
            "event_type": item.event_type,
            "occurred_at": to_utc_iso(item.occurred_at),
            "severity": item.severity,
            "description": item.description,
            "source": item.source,
            "reliability": item.reliability
        })
    return jsonify(result)

@main_bp.route('/api/risk/<int:region_id>')
def api_region_risk(region_id):
    """Retorna a avaliação de risco calculada mais recente para a região especificada."""
    region = Region.query.get_or_404(region_id)
    assessment = (
        RiskAssessment.query
        .filter_by(region_id=region.id)
        .order_by(RiskAssessment.assessed_at.desc())
        .first()
    )
    if not assessment:
        assessment = calculate_region_risk(region)

    return jsonify({
        "region": region.to_dict(),
        "assessed_at": to_utc_iso(assessment.assessed_at),
        "risk_level": assessment.risk_level,
        "score": assessment.score,
        "factors": assessment.factors,
        "method_version": assessment.method_version
    })

@main_bp.route('/api/traffic')
def api_traffic():
    """Retorna os dados do painel de trânsito das rodovias federais da Zona Sul."""
    return jsonify(get_traffic_panel_data())

@main_bp.route('/api/traffic/check')
@main_bp.route('/api/traffic/check-update')
def api_traffic_check():
    """Endpoint leve de checagem que retorna o timestamp da última atualização das rodovias."""
    from app.collectors.institutional_traffic import get_latest_traffic_timestamp
    return jsonify({
        "status": "ok",
        "last_updated_at": get_latest_traffic_timestamp()
    })

@main_bp.route('/api/alerts')
def api_alerts():
    """Retorna os alertas gerados recentemente (últimas 24h ou do dia)."""
    alerts = Alert.query.order_by(Alert.created_at.desc()).limit(50).all()
    result = []
    for a in alerts:
        reg = Region.query.get(a.region_id)
        result.append({
            "id": a.id,
            "region_id": a.region_id,
            "municipality": reg.municipality if reg else "",
            "region_name": reg.name if reg else "",
            "triggered_rule": a.triggered_rule,
            "channel": a.channel,
            "status": a.status,
            "dedup_key": a.dedup_key,
            "created_at": to_utc_iso(a.created_at),
        })
    return jsonify(result)

@main_bp.route('/api/health')
def health():
    return jsonify({"status": "ok", "phase": "Fase 4 - Risco e Transito ativos"})
