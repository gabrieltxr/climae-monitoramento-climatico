import hmac
import os
from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, jsonify, request
from app.models import db, Region, WeatherObservation, Occurrence, RiskAssessment, Alert, TrafficEvent, MonthlyClimateSummary
from app.services.risk_engine import calculate_region_risk
from app.collectors.traffic_collector import get_traffic_panel_data, update_highway_conditions
from app.collectors.inmet import get_region_station_summary

main_bp = Blueprint('main', __name__)

MAX_PER_PAGE = 50

@main_bp.route('/')
def index():
    regions = Region.query.order_by(Region.municipality, Region.name).all()
    preferred_id = request.cookies.get('climae_preferred_region_id')
    return render_template('index.html', regions=regions, preferred_id=preferred_id)

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

BRT_TZ = timezone(timedelta(hours=-3))

def to_brt_str(dt):
    """Converte datetime UTC para string formatada no Horário de Brasília (BRT, UTC-3)."""
    if not dt:
        return "--"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BRT_TZ).strftime("%d/%m/%Y %H:%M")

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
    # Sem fallback para previsões vencidas: se não houver horários futuros,
    # a previsão retorna vazia em vez de exibir dados passados como "próximas 24h".

    # Medição da estação INMET mais próxima (None sem token, fora do raio ou estação parada).
    station = get_region_station_summary(region)
    if station:
        station = {**station, "observed_at": to_utc_iso(station["observed_at"])}

    return jsonify({
        "region": region.to_dict(),
        "station": station,
        "current": {
            "observed_at": to_utc_iso(current_obs.observed_at) if current_obs else None,
            "collected_at": to_utc_iso(current_obs.collected_at) if current_obs and current_obs.collected_at else None,
            "temperature_c": current_obs.temperature_c if current_obs else None,
            "humidity_percent": current_obs.humidity_percent if current_obs else None,
            "precipitation_mm": current_obs.precipitation_mm if current_obs else None,
            "wind_speed_kmh": current_obs.wind_speed if current_obs else None,
            "wind_direction": current_obs.wind_direction if current_obs else None,
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
                "wind_speed_kmh": f.wind_speed,
                "wind_direction": f.wind_direction
            }
            for f in forecasts
        ]
    })

def _latest_per_region(model, time_column, *conditions) -> dict:
    """{region_id: linha mais recente} de todas as regiões em uma consulta."""
    latest = (
        db.session.query(model.region_id.label("rid"), db.func.max(time_column).label("t"))
        .filter(*conditions)
        .group_by(model.region_id)
        .subquery()
    )
    rows = (
        model.query
        .join(latest, db.and_(model.region_id == latest.c.rid, time_column == latest.c.t))
        .filter(*conditions)
        .order_by(model.id.desc())
        .all()
    )
    result = {}
    for row in rows:
        result.setdefault(row.region_id, row)  # empate no horário: fica a linha mais nova
    return result


@main_bp.route('/api/weather-summary', methods=['GET'])
def get_weather_summary():
    """Resumo rápido de todas as regiões para renderizar a camada visual no Mapa Leaflet e leitura de alertas."""
    # Rota só de leitura. Busca tudo em 4 consultas (regiões, última estimativa, previsões
    # e último risco de todas as regiões) em vez de 3 consultas por região (N+1).
    regions = Region.query.all()
    now_utc_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    latest_obs = _latest_per_region(WeatherObservation, WeatherObservation.observed_at,
                                    WeatherObservation.is_forecast == False)
    latest_risk = _latest_per_region(RiskAssessment, RiskAssessment.assessed_at)

    forecasts_by_region = {}
    upcoming = (
        WeatherObservation.query
        .filter(
            WeatherObservation.is_forecast == True,
            WeatherObservation.observed_at >= now_utc_hour.replace(tzinfo=None),
        )
        .order_by(WeatherObservation.region_id, WeatherObservation.observed_at.asc(), WeatherObservation.id.asc())
        .all()
    )
    for f in upcoming:
        bucket = forecasts_by_region.setdefault(f.region_id, [])
        if len(bucket) < 24:
            bucket.append(f)

    summary = []
    for r in regions:
        current_obs = latest_obs.get(r.id)
        forecasts = forecasts_by_region.get(r.id, [])
        rain_24h = sum((f.precipitation_mm or 0.0) for f in forecasts)
        fc = forecasts[0] if forecasts else None
        risk = latest_risk.get(r.id)
        summary.append({
            "region_id": r.id,
            "municipality": r.municipality,
            "name": r.name,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "region": r.to_dict(),
            "temperature_c": current_obs.temperature_c if current_obs else None,
            "humidity_percent": current_obs.humidity_percent if current_obs else None,
            "wind_speed": current_obs.wind_speed if current_obs else None,
            "wind_speed_kmh": current_obs.wind_speed if current_obs else 0,
            "wind_direction": current_obs.wind_direction if current_obs else None,  # <-- NOVO (v0.2)
            "precipitation_mm": current_obs.precipitation_mm if current_obs else 0.0,
            "rain_probability": fc.rain_probability if fc else 0,
            "rain_next_24h_mm": round(rain_24h, 1),
            "risk_score": risk.score if risk else 0.0,
            "risk_level": risk.risk_level if risk else "Baixo",
            "observed_at": (
                current_obs.observed_at.replace(tzinfo=timezone.utc).isoformat()
                if current_obs and current_obs.observed_at else None
            ),
        })

    if request.args.get('format') == 'list':
        return jsonify(summary)

    # Retorna estritamente o resumo meteorológico e as regiões para o mapa
    return jsonify({
        "success": True,
        "summary": summary,
        "regions": summary,
    })


api_weather_summary = get_weather_summary

@main_bp.route('/api/occurrences')
def api_occurrences():
    """Lista o histórico de ocorrências com suporte a filtros por região e período."""
    region_id = request.args.get('region_id', type=int)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    query = Occurrence.query.join(Region)

    try:
        dt_start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc) if start_date else None
        dt_end = (
            datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
            if end_date else None
        )
    except ValueError:
        return jsonify({"error": "Datas inválidas. Use o formato AAAA-MM-DD."}), 400

    if region_id:
        query = query.filter(Occurrence.region_id == region_id)
    if dt_start:
        query = query.filter(Occurrence.occurred_at >= dt_start)
    if dt_end:
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

# Dois ciclos de coleta (15 min): avaliação mais velha que isso indica agendador parado.
RISK_MAX_AGE_MINUTES = int(os.getenv("RISK_MAX_AGE_MINUTES", "30"))


def _is_older_than(dt, minutes: int) -> bool:
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - dt > timedelta(minutes=minutes)


@main_bp.route('/api/risk/<int:region_id>')
def api_region_risk(region_id):
    """
    Avaliação de risco da região. Usa a gravada pelo agendador se tiver até
    RISK_MAX_AGE_MINUTES; se estiver mais velha ou não existir, calcula na hora SEM gravar
    (rota GET não escreve no banco) e marca calculado_agora=true.
    """
    region = Region.query.get_or_404(region_id)
    assessment = (
        RiskAssessment.query
        .filter_by(region_id=region.id)
        .order_by(RiskAssessment.assessed_at.desc())
        .first()
    )
    computed_now = False
    if assessment is None or _is_older_than(assessment.assessed_at, RISK_MAX_AGE_MINUTES):
        assessment = calculate_region_risk(region, persist=False)
        computed_now = True

    return jsonify({
        "calculado_agora": computed_now,
        "region": region.to_dict(),
        "assessed_at": to_utc_iso(assessment.assessed_at),
        "risk_level": assessment.risk_level,
        "score": assessment.score,
        "factors": assessment.factors,
        "method_version": assessment.method_version
    })

@main_bp.route('/api/traffic')
def api_traffic():
    """Retorna os dados do painel de trânsito das rodovias e corredores da Zona Sul."""
    return jsonify(get_traffic_panel_data())

@main_bp.route('/api/traffic/check')
@main_bp.route('/api/traffic/check-update')
def api_traffic_check():
    """Endpoint leve de checagem que retorna o timestamp da última atualização das rodovias."""
    latest = TrafficEvent.query.order_by(
        TrafficEvent.reported_at.desc(),
        TrafficEvent.source_updated_at.desc()
    ).first()
    ts = None
    if latest:
        dt = latest.reported_at or latest.source_updated_at or latest.occurred_at
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.isoformat()
    return jsonify({
        "status": "ok",
        "last_updated_at": ts
    })

@main_bp.route("/api/alerts", methods=["GET"])
def get_alerts():
    # Limites evitam que um único pedido (ex.: per_page=1000000) carregue a tabela inteira
    page = max(1, request.args.get("page", 1, type=int) or 1)
    per_page = min(MAX_PER_PAGE, max(1, request.args.get("per_page", 5, type=int) or 5))

    # Consulta paginada, ordenada do mais recente para o mais antigo
    pagination = Alert.query.order_by(Alert.id.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    alerts_list = []
    for a in pagination.items:
        # Suporte a triggered_at ou created_at formatado no Horário de Brasília (BRT, UTC-3)
        dt = getattr(a, "triggered_at", getattr(a, "created_at", None))
        dt_str = to_brt_str(dt)
        reg_name = a.region.name if getattr(a, "region", None) else f"Região #{a.region_id}"

        alerts_list.append({
            "id": a.id,
            "triggered_at": dt_str,
            "region_name": reg_name,
            "city": a.region.city if getattr(a, "region", None) else "",
            "rule_triggered": getattr(a, "rule_triggered", getattr(a, "message", "--")),
            "channel": getattr(a, "channel", "PAINEL"),
            "status": getattr(a, "status", "SENT"),
            "dedup_key": getattr(a, "dedup_key", "")
        })

    return jsonify({
        "alerts": alerts_list,
        "total": pagination.total,
        "pages": pagination.pages or 1,
        "current_page": pagination.page,
        "has_prev": pagination.has_prev,
        "has_next": pagination.has_next
    })

@main_bp.route('/api/health')
def health():
    return jsonify({"status": "ok", "phase": "Fase 4 - Risco e Transito ativos"})

@main_bp.route('/api/climate-history/<int:region_id>')
def api_climate_history(region_id: int):
    """
    Retorna o histórico mensal (2000-presente), as normais climatológicas (year=0),
    os recordes do mês e a projeção sazonal para a região selecionada.
    """
    region = Region.query.get_or_404(region_id)
    now_utc = datetime.now(timezone.utc)
    selected_month = request.args.get('month', default=now_utc.month, type=int)
    selected_year = request.args.get('year', default=now_utc.year, type=int)

    # 1. Busca as 12 Normais Climatológicas (year == 0)
    normals_q = (
        MonthlyClimateSummary.query
        .filter_by(region_id=region.id, year=0)
        .order_by(MonthlyClimateSummary.month.asc())
        .all()
    )
    normals_list = [
        {
            "month": n.month,
            "avg_temp_c": n.avg_temp_c,
            "max_temp_c": n.max_temp_c,
            "min_temp_c": n.min_temp_c,
            "total_rain_mm": n.total_rain_mm,
            "rainy_days": n.rainy_days,
            "max_wind_kmh": n.max_wind_kmh,
        }
        for n in normals_q
    ]

    month_normal = next((n for n in normals_list if n["month"] == selected_month), None)

    # 2. Série histórica de todos os anos (2000-2026) para o mês selecionado
    history_for_month = (
        MonthlyClimateSummary.query
        .filter(
            MonthlyClimateSummary.region_id == region.id,
            MonthlyClimateSummary.month == selected_month,
            MonthlyClimateSummary.year > 0,
        )
        .order_by(MonthlyClimateSummary.year.asc())
        .all()
    )

    yearly_series = [
        {
            "year": h.year,
            "avg_temp_c": h.avg_temp_c,
            "max_temp_c": h.max_temp_c,
            "min_temp_c": h.min_temp_c,
            "total_rain_mm": h.total_rain_mm,
            "rainy_days": h.rainy_days,
            "max_wind_kmh": h.max_wind_kmh,
        }
        for h in history_for_month
    ]

    selected_record = next((y for y in yearly_series if y["year"] == selected_year), None)

    # 3. Recordes históricos do mês
    wettest = max(yearly_series, key=lambda x: x["total_rain_mm"] or 0.0) if yearly_series else None
    driest = min(yearly_series, key=lambda x: x["total_rain_mm"] if x["total_rain_mm"] is not None else 9999.0) if yearly_series else None
    hottest = max(yearly_series, key=lambda x: x["max_temp_c"] or -99.0) if yearly_series else None
    coldest = min(yearly_series, key=lambda x: x["min_temp_c"] if x["min_temp_c"] is not None else 99.0) if yearly_series else None

    # 4. Cálculo de Anomalia e Texto de Diagnóstico
    rain_anomaly_pct = None
    temp_anomaly_c = None
    outlook_text = "Sem dados suficientes para projeção."

    if month_normal and selected_record:
        norm_rain = month_normal["total_rain_mm"] or 1.0
        rec_rain = selected_record["total_rain_mm"] or 0.0
        rain_anomaly_pct = round(((rec_rain - norm_rain) / norm_rain) * 100.0, 1)

        if selected_record["avg_temp_c"] is not None and month_normal["avg_temp_c"] is not None:
            temp_anomaly_c = round(selected_record["avg_temp_c"] - month_normal["avg_temp_c"], 1)

        if rain_anomaly_pct > 20:
            rain_status = f"Acima da média histórica (+{rain_anomaly_pct}%)"
        elif rain_anomaly_pct < -20:
            rain_status = f"Abaixo da média histórica ({rain_anomaly_pct}%)"
        else:
            rain_status = f"Dentro da normalidade climatológica ({rain_anomaly_pct:+.1f}%)"

        outlook_text = (
            f"No período {selected_month:02d}/{selected_year}, o acumulado registrado é de "
            f"{rec_rain:.1f} mm frente à média histórica de {norm_rain:.1f} mm ({rain_status})."
        )
    elif month_normal:
        outlook_text = (
            f"Projeção Climatológica para o mês {selected_month:02d}: espera-se chuva média em torno de "
            f"{month_normal['total_rain_mm']:.1f} mm (~{month_normal['rainy_days']} dias chuvosos) e "
            f"temperatura média de {month_normal['avg_temp_c']:.1f} °C "
            f"(Mín: {month_normal['min_temp_c']:.1f} °C / Máx: {month_normal['max_temp_c']:.1f} °C)."
        )

    return jsonify({
        "region_id": region.id,
        "municipality": region.municipality,
        "region_name": region.name,
        "selected_month": selected_month,
        "selected_year": selected_year,
        "month_normal": month_normal,
        "selected_record": selected_record,
        "rain_anomaly_pct": rain_anomaly_pct,
        "temp_anomaly_c": temp_anomaly_c,
        "outlook_text": outlook_text,
        "extremes": {
            "wettest": wettest,
            "driest": driest,
            "hottest": hottest,
            "coldest": coldest,
        },
        "normals_annual": normals_list,
        "yearly_series_for_month": yearly_series,
    })


# =========================================================================
# MONITORAMENTO DE ENERGIA ELÉTRICA (CROWDSOURCING & SITUAÇÃO DA REDE)
# =========================================================================

@main_bp.before_request
def limit_public_power_posts():
    """
    Limite geral por IP nos POSTs públicos de /api/power/* (relatos e confirmações).
    A publicação de boletins fica de fora: exige token de admin.
    """
    if request.method != "POST" or not request.path.startswith("/api/power/"):
        return None
    if request.path == "/api/power/bulletins":
        return None
    from app.services.power_service import generate_ip_key, get_power_post_limiter
    allowed, remaining = get_power_post_limiter().try_acquire(generate_ip_key(request), "power-post")
    if allowed:
        return None
    return jsonify({
        "success": False,
        "error": f"Muitas requisições da sua rede. Aguarde mais {remaining} minuto(s).",
        "cooldown_remaining": remaining,
    }), 429


@main_bp.route('/api/power/status/<int:region_id>')
def api_power_status(region_id: int):
    """Retorna o status atual de abastecimento elétrico da região e cooldown do usuário."""
    from app.services.power_service import generate_reporter_hash, get_power_status
    reporter_hash = generate_reporter_hash(request)
    status_data = get_power_status(region_id, reporter_hash)
    return jsonify(status_data)


def parse_region_id(value) -> int | None:
    """
    Normaliza o region_id do JSON para inteiro positivo. Aceita só int ou texto de dígitos;
    recusa bool, float, listas e objetos. Os limitadores contam por (IP, região): sem essa
    normalização, "1", "01" e 1.0 virariam regiões diferentes para eles e iguais para o banco.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isascii() and text.isdigit() and int(text) > 0:
            return int(text)
    return None


def _parse_power_vote():
    """
    Valida o corpo de /api/power/report e /api/power/restore.
    Retorna (region_id, comentário, None) ou (None, None, resposta de erro).
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, None, (jsonify({"success": False, "error": "Corpo da requisição deve ser um objeto JSON."}), 400)

    if data.get('region_id') in (None, ""):
        return None, None, (jsonify({"success": False, "error": "ID da região é obrigatório."}), 400)
    region_id = parse_region_id(data.get('region_id'))
    if region_id is None:
        return None, None, (jsonify({"success": False, "error": "ID da região inválido."}), 400)

    comment = data.get('comment')
    if comment is not None and not isinstance(comment, str):
        return None, None, (jsonify({"success": False, "error": "Comentário deve ser texto."}), 400)

    if db.session.get(Region, region_id) is None:
        return None, None, (jsonify({"success": False, "error": "Região não encontrada."}), 404)
    return region_id, comment, None


@main_bp.route('/api/power/report', methods=['POST'])
def api_power_report():
    """Registra um relato colaborativo de falta de energia com proteção anti-spam."""
    from app.services.power_service import generate_ip_key, generate_reporter_hash, record_power_outage_report
    region_id, comment, error = _parse_power_vote()
    if error:
        return error

    reporter_hash = generate_reporter_hash(request)
    result = record_power_outage_report(region_id, reporter_hash, comment, ip_key=generate_ip_key(request))
    status_code = 200 if result.get("success") else 429
    return jsonify(result), status_code


@main_bp.route('/api/power/restore', methods=['POST'])
def api_power_restore():
    """Registra uma confirmação colaborativa de retorno da luz ("A Luz Voltou") com proteção anti-spam."""
    from app.services.power_service import generate_ip_key, generate_reporter_hash, record_power_restoration
    region_id, comment, error = _parse_power_vote()
    if error:
        return error

    reporter_hash = generate_reporter_hash(request)
    result = record_power_restoration(region_id, reporter_hash, comment, ip_key=generate_ip_key(request))
    status_code = 200 if result.get("success") else 429
    return jsonify(result), status_code


@main_bp.route('/api/power/bulletins', methods=['GET'])
def api_power_bulletins():
    """Retorna os comunicados e notas oficiais da concessionária e Defesa Civil."""
    from app.services.power_service import get_utility_bulletins
    municipality = request.args.get('municipality')
    status = request.args.get('status')
    limit = min(MAX_PER_PAGE, max(1, request.args.get('limit', 10, type=int) or 10))
    bulletins = get_utility_bulletins(municipality=municipality, status=status, limit=limit)
    return jsonify(bulletins)


def _is_admin_request() -> bool:
    """
    Valida o token administrativo (header X-Admin-Token) contra ADMIN_API_TOKEN.
    Sem token configurado no ambiente, nenhuma requisição é aceita.
    """
    expected = os.getenv("ADMIN_API_TOKEN", "").strip()
    provided = request.headers.get("X-Admin-Token", "").strip()
    return bool(expected) and hmac.compare_digest(provided.encode(), expected.encode())


@main_bp.route('/api/power/bulletins', methods=['POST'])
def api_power_create_bulletin():
    """Registra uma nova nota ou comunicado oficial de contingência (somente administradores)."""
    from app.services.power_service import record_utility_bulletin
    if not _is_admin_request():
        return jsonify({"success": False, "error": "Não autorizado."}), 403
    data = request.get_json() or {}
    res = record_utility_bulletin(data)
    status_code = 201 if res.get("success") else 400
    return jsonify(res), status_code




