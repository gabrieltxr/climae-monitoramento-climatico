from datetime import datetime, timezone
from app.models import db, Region, WeatherObservation, Occurrence, TrafficEvent, RiskAssessment

METHOD_VERSION = "v0.2-dinamico"


def classify_score(score: float, is_provisional: bool = False) -> str:
    """Classifica a pontuação de 0 a 100 conforme tabela da Seção 6 do manual."""
    if score >= 75:
        level = "Muito alto"
    elif score >= 50:
        level = "Alto"
    elif score >= 25:
        level = "Moderado"
    else:
        level = "Baixo"

    if is_provisional:
        return f"{level} (Provisório)"
    return level


def calculate_region_risk(region: Region) -> RiskAssessment:
    """
    Calcula o Índice de Risco Dinâmico (0 a 100) - Modelo Abordagem A:
    A vulnerabilidade histórica modula a ameaça meteorológica real,
    evitando scores inflados em dias de tempo calmo/seco.

    Categorias:
    - 40% Precipitação (Volume acumulado + probabilidade ponderada pelo volume)
    - 35% Vulnerabilidade e histórico (Suscetibilidade ativada dinamicamente pela ameaça)
    - 25% Condições complementares (Vento efetivo > 25km/h, saturação atmosférica, trânsito)
    """
    now_utc = datetime.now(timezone.utc)

    # 1. Buscar dados meteorológicos atuais e previsão de 24h
    current_obs = (
        WeatherObservation.query
        .filter_by(region_id=region.id, is_forecast=False)
        .order_by(WeatherObservation.observed_at.desc())
        .first()
    )
    forecasts_24h = (
        WeatherObservation.query
        .filter_by(region_id=region.id, is_forecast=True)
        .order_by(WeatherObservation.observed_at.asc())
        .limit(24)
        .all()
    )

    # Se não houver dado meteorológico algum, não assume que é seguro: marca como Inconclusivo
    if not current_obs and not forecasts_24h:
        assessment = RiskAssessment(
            region_id=region.id,
            assessed_at=now_utc,
            risk_level="Inconclusivo (Sem dados meteorológicos)",
            score=None,
            factors={"warning": "Dados meteorológicos ausentes para esta região."},
            method_version=METHOD_VERSION,
        )
        db.session.add(assessment)
        db.session.commit()
        return assessment

    # --- CATEGORIA 1: Precipitação prevista e acumulada (Máx: 40 pontos) ---
    current_precip = (current_obs.precipitation_mm or 0.0) if current_obs else 0.0
    forecast_precip_sum = sum((f.precipitation_mm or 0.0) for f in forecasts_24h)
    max_rain_prob = max([f.rain_probability or 0.0 for f in forecasts_24h], default=0.0)
    total_expected_rain = current_precip + forecast_precip_sum

    # Escala de volume: 50mm em 24h atinge os 30 pts máximos de volume
    rain_volume_pts = min(30.0, (total_expected_rain / 50.0) * 30.0)
    # A probabilidade de chuva é ponderada pelo volume esperado (não pontua sem chuva prevista)
    volume_weight = min(1.0, max(0.05, total_expected_rain / 10.0))
    rain_prob_pts = (max_rain_prob / 100.0) * 10.0 * volume_weight
    precip_score = round(min(40.0, rain_volume_pts + rain_prob_pts), 1)

    # --- CATEGORIA 2: Vulnerabilidade e histórico local (Máx: 35 pontos) ---
    occurrences = Occurrence.query.filter_by(region_id=region.id).all()
    has_history = len(occurrences) > 0

    if not has_history:
        intrinsic_vuln = 15.0
        history_note = "Sem histórico cadastrado na sub-região (aplicado piso cautelar provisório)"
    else:
        confirmed_high = sum(1 for o in occurrences if o.severity == "Alta" and o.reliability != "anomalia meteorológica")
        other_events = len(occurrences) - confirmed_high
        raw_hist = 10.0 + (confirmed_high * 6.0) + (other_events * 1.5)
        intrinsic_vuln = min(35.0, raw_hist)
        history_note = f"{len(occurrences)} ocorrências registradas ({confirmed_high} de alta severidade documentadas)"

    # --- CATEGORIA 3: Condições complementares (Máx: 25 pontos) ---
    current_wind = (current_obs.wind_speed or 0.0) if current_obs else 0.0
    max_forecast_wind = max([f.wind_speed or 0.0 for f in forecasts_24h], default=0.0)
    peak_wind = max(current_wind, max_forecast_wind)
    humidity = (current_obs.humidity_percent or 0.0) if current_obs else 0.0

    # Vento: brisa suave (<= 25 km/h) não pontua risco. Escala de 25 km/h a 70 km/h até 18 pts
    if peak_wind <= 25.0:
        wind_pts = 0.0
    else:
        wind_pts = min(18.0, ((peak_wind - 25.0) / 45.0) * 18.0)

    # Umidade: apenas saturação atmosférica com potencial de neblina severa ou chuva em curso pontua
    if humidity >= 95.0 and peak_wind <= 18.0:
        hum_pts = 4.0  # Propício a bancos densos de nevoeiro
    elif humidity >= 90.0 and total_expected_rain >= 5.0:
        hum_pts = 3.0  # Solo e atmosfera encharcados sob chuva ativa
    elif humidity >= 85.0 and total_expected_rain >= 1.0:
        hum_pts = 1.5
    else:
        hum_pts = 0.0

    # Telemetria Viária: intercorrências ativas nos corredores viários do município
    from app.collectors.traffic_collector import get_active_traffic_events_for_municipality
    active_corridor_events = get_active_traffic_events_for_municipality(region.municipality)
    legacy_events = TrafficEvent.query.filter_by(region_id=region.id).filter(
        TrafficEvent.status.in_(["Ativo", "Alerta", "Crítico", "Atenção"])
    ).all()
    all_adverse_events = active_corridor_events + legacy_events
    active_traffic_count = len(all_adverse_events)

    if any(any(k in (ev.status or "") for k in ["Alerta", "Crítico", "Crítica", "Suspensão"]) for ev in all_adverse_events):
        traffic_pts = 3.0
    elif active_traffic_count > 0:
        traffic_pts = 1.5
    else:
        traffic_pts = 0.0

    comp_score = round(min(25.0, wind_pts + hum_pts + traffic_pts), 1)

    # --- ATIVAÇÃO DINÂMICA DA VULNERABILIDADE HISTÓRICA ---
    # A vulnerabilidade só se materializa em risco efetivo proporcionalmente à ameaça em curso
    act_rain = min(1.0, total_expected_rain / 40.0)
    act_wind = min(1.0, max(0.0, (peak_wind - 25.0) / 45.0))
    act_traffic = 0.35 if active_traffic_count > 0 else 0.0

    # Piso residual cautelar de 10% (mantém ~3.5 pts se vuln=35 em dias de céu limpo)
    threat_factor = min(1.0, max(0.10, act_rain + (0.50 * act_wind) + act_traffic))
    history_score = round(min(35.0, intrinsic_vuln * threat_factor), 1)

    # --- SOMA FINAL EQUILIBRADA (0 a 100) ---
    total_score = round(min(100.0, precip_score + history_score + comp_score), 1)
    risk_level = classify_score(total_score, is_provisional=not has_history)

    factors = {
        "precipitacao_40pct": {
            "pontos": precip_score,
            "maximo": 40,
            "acumulado_previsto_24h_mm": round(total_expected_rain, 1),
            "probabilidade_max_pct": max_rain_prob,
            "peso_volume_probabilidade": round(volume_weight, 2),
        },
        "vulnerabilidade_historico_35pct": {
            "pontos": history_score,
            "maximo": 35,
            "vulnerabilidade_intrinseca": round(intrinsic_vuln, 1),
            "fator_ativacao_ameaca_pct": round(threat_factor * 100.0, 1),
            "detalhe": history_note,
        },
        "condicoes_complementares_25pct": {
            "pontos": comp_score,
            "maximo": 25,
            "vento_pico_kmh": round(peak_wind, 1),
            "umidade_atual_pct": humidity,
            "eventos_transito_ativos": active_traffic_count,
            "impacto_transito_pontos": traffic_pts,
        },
    }

    assessment = RiskAssessment(
        region_id=region.id,
        assessed_at=now_utc,
        risk_level=risk_level,
        score=total_score,
        factors=factors,
        method_version=METHOD_VERSION,
    )
    db.session.add(assessment)
    db.session.commit()
    return assessment


def evaluate_all_regions() -> list[RiskAssessment]:
    regions = Region.query.all()
    return [calculate_region_risk(r) for r in regions]
