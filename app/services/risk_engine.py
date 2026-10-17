from datetime import datetime, timezone
from app.models import db, Region, WeatherObservation, Occurrence, TrafficEvent, RiskAssessment

METHOD_VERSION = "v0.1-mvp"


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
    Calcula o Índice de Risco Experimental (0 a 100) com pesos:
    - 40% Precipitação prevista e acumulada
    - 35% Vulnerabilidade e histórico local
    - 25% Condições complementares (vento, umidade, evidência em trânsito)
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
    # Escala: 50mm em 24h atinge 30 pts + até 10 pts pela probabilidade máxima
    rain_volume_pts = min(30.0, (total_expected_rain / 50.0) * 30.0)
    rain_prob_pts = (max_rain_prob / 100.0) * 10.0
    precip_score = round(min(40.0, rain_volume_pts + rain_prob_pts), 1)

    # --- CATEGORIA 2: Vulnerabilidade e histórico local (Máx: 35 pontos) ---
    # Eventos confirmados/técnicos têm peso maior que anomalias de reanálise
    occurrences = Occurrence.query.filter_by(region_id=region.id).all()
    has_history = len(occurrences) > 0

    if not has_history:
        # Regra da Seção 6: Se não houver histórico da região, não assumir que é segura (piso cautelar)
        history_score = 15.0
        history_note = "Sem histórico cadastrado na sub-região (aplicado piso cautelar provisório)"
    else:
        confirmed_high = sum(1 for o in occurrences if o.severity == "Alta" and o.reliability != "anomalia meteorológica")
        other_events = len(occurrences) - confirmed_high
        # Cada evento de alta gravidade documentado soma 9 pts; demais somam 2.5 pts (teto de 35 pts)
        raw_hist = 8.0 + (confirmed_high * 9.0) + (other_events * 2.5)
        history_score = round(min(35.0, raw_hist), 1)
        history_note = f"{len(occurrences)} ocorrências registradas ({confirmed_high} de alta severidade documentadas)"

    # --- CATEGORIA 3: Condições complementares (Máx: 25 pontos) ---
    current_wind = (current_obs.wind_speed or 0.0) if current_obs else 0.0
    max_forecast_wind = max([f.wind_speed or 0.0 for f in forecasts_24h], default=0.0)
    peak_wind = max(current_wind, max_forecast_wind)
    humidity = (current_obs.humidity_percent or 0.0) if current_obs else 0.0

    # Vento até 70 km/h contribui com até 18 pts; Umidade alta (>85%) soma até 4 pts
    wind_pts = min(18.0, (peak_wind / 70.0) * 18.0)
    hum_pts = 4.0 if humidity >= 85 else (2.0 if humidity >= 75 else 0.0)

    # Trânsito afetado ativo serve como evidência de impacto em curso (+3 pts)
    active_traffic = TrafficEvent.query.filter_by(region_id=region.id, status="Ativo").count()
    traffic_pts = 3.0 if active_traffic > 0 else 0.0

    comp_score = round(min(25.0, wind_pts + hum_pts + traffic_pts), 1)

    # --- SOMA FINAL (0 a 100) ---
    total_score = round(min(100.0, precip_score + history_score + comp_score), 1)
    risk_level = classify_score(total_score, is_provisional=not has_history)

    factors = {
        "precipitacao_40pct": {
            "pontos": precip_score,
            "maximo": 40,
            "acumulado_previsto_24h_mm": round(total_expected_rain, 1),
            "probabilidade_max_pct": max_rain_prob,
        },
        "vulnerabilidade_historico_35pct": {
            "pontos": history_score,
            "maximo": 35,
            "detalhe": history_note,
        },
        "condicoes_complementares_25pct": {
            "pontos": comp_score,
            "maximo": 25,
            "vento_pico_kmh": round(peak_wind, 1),
            "umidade_atual_pct": humidity,
            "eventos_transito_ativos": active_traffic,
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
