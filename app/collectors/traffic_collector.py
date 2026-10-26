from datetime import datetime, timezone
from app.models import db, Region, WeatherObservation, TrafficEvent

# Definição técnica dos 7 Corredores Estratégicos da Zona Sul do RS
HIGHWAY_CORRIDORS = [
    {
        "highway": "BR-116 (Eixo Sul — Pelotas / São Lourenço / Jaguarão)",
        "jurisdiction": "PRF / Ecosul",
        "municipalities": ["Pelotas", "São Lourenço do Sul", "Jaguarão"],
        "type": "federal",
        "rain_alert_24h": 30.0,
        "rain_critical_24h": 55.0,
        "wind_alert": 50.0,
        "wind_critical": 68.0,
        "normal_desc": "Fluxo normal em pista dupla/simples; sem retenções na Ponte do Canal São Gonçalo.",
        "feature_note": "Pontes sobre Canal São Gonçalo e Arroio Pelotas",
    },
    {
        "highway": "BR-392 (Corredor Portuário & Serra dos Tapes — Rio Grande / Pelotas / Canguçu)",
        "jurisdiction": "PRF / Ecosul",
        "municipalities": ["Rio Grande", "Pelotas", "Canguçu"],
        "type": "serra_porto",
        "rain_alert_24h": 25.0,
        "rain_critical_24h": 45.0,
        "wind_alert": 48.0,
        "wind_critical": 65.0,
        "normal_desc": "Tráfego de carga fluindo normalmente entre o Distrito Industrial, Pelotas e Serra dos Tapes.",
        "feature_note": "Curvas sinuosas na Serra dos Tapes e acesso ao Porto",
    },
    {
        "highway": "BR-471 (Trecho Taim / Fronteira — Rio Grande / Santa Vitória / Chuí)",
        "jurisdiction": "PRF / DNIT",
        "municipalities": ["Rio Grande", "Santa Vitória do Palmar", "Chuí"],
        "type": "planicie_taim",
        "rain_alert_24h": 28.0,
        "rain_critical_24h": 50.0,
        "wind_alert": 45.0,
        "wind_critical": 62.0,
        "normal_desc": "Pista livre ao longo da Estação Ecológica do Taim até a fronteira no Chuí.",
        "feature_note": "Vento cruzado em campo aberto e visibilidade no Taim",
    },
    {
        "highway": "BR-293 (Corredor da Campanha — Pelotas / Capão do Leão / Piratini)",
        "jurisdiction": "PRF / Ecosul / DNIT",
        "municipalities": ["Pelotas", "Capão do Leão", "Piratini"],
        "type": "federal",
        "rain_alert_24h": 28.0,
        "rain_critical_24h": 50.0,
        "wind_alert": 50.0,
        "wind_critical": 65.0,
        "normal_desc": "Trânsito fluindo sem intercorrências entre o trevo de Capão do Leão e o acesso a Piratini.",
        "feature_note": "Travessia sobre a bacia do Rio Piratini",
    },
    {
        "highway": "ERS-734 (Corredor Urbano-Litorâneo — Rio Grande ↔ Balneário Cassino)",
        "jurisdiction": "DAER-RS / CRBM",
        "municipalities": ["Rio Grande"],
        "type": "estadual_urbana",
        "rain_alert_24h": 18.0,      # Cota baixa: alaga com acumulados menores
        "rain_critical_24h": 35.0,
        "wind_alert": 45.0,
        "wind_critical": 60.0,
        "normal_desc": "Fluxo urbano e acesso ao Balneário Cassino operando sem pontos de retenção ou lâmina d'água.",
        "feature_note": "Drenagem de planície costeira (pórtico ao Cassino)",
    },
    {
        "highway": "ERS-265 (Eixo Interiorano — São Lourenço do Sul ↔ Canguçu ↔ Piratini)",
        "jurisdiction": "DAER-RS / CRBM",
        "municipalities": ["São Lourenço do Sul", "Canguçu", "Piratini"],
        "type": "estadual_interior",
        "rain_alert_24h": 20.0,      # Trechos de serra/pontes baixas
        "rain_critical_24h": 40.0,
        "wind_alert": 45.0,
        "wind_critical": 60.0,
        "normal_desc": "Trafegabilidade normal nas pontes rurais e trechos de ligação entre São Lourenço e Canguçu.",
        "feature_note": "Pontes baixas sobre arroios e declives de serra",
    },
    {
        "highway": "BR-101 / Travessia Hidroviária (Balsa Rio Grande ↔ São José do Norte)",
        "jurisdiction": "Hidroviário / Marinha / Metroplan",
        "municipalities": ["Rio Grande", "São José do Norte"],
        "type": "hidroviario",
        "rain_alert_24h": 30.0,
        "rain_critical_24h": 55.0,
        "wind_alert": 35.0,          # Navegação no canal sente ventos a partir de 35 km/h
        "wind_critical": 48.0,       # Acima de 48 km/h há risco real de suspensão de balsa/lanchas
        "normal_desc": "Travessia de balsas e lanchas operando dentro dos parâmetros regulares de vento e visibilidade no canal.",
        "feature_note": "Canal de acesso da Lagoa dos Patos (sensível a vento e nevoeiro)",
    },
]


def _get_corridor_weather_metrics(municipalities: list[str]) -> dict:
    """
    Reúne as métricas meteorológicas atuais e previstas (24h) de todos os municípios
    atravessados pelo corredor rodoviário/hidroviário.
    """
    now_utc = datetime.now(timezone.utc)
    current_hour_floor = now_utc.replace(minute=0, second=0, microsecond=0)

    regions = Region.query.filter(Region.municipality.in_(municipalities)).all()
    if not regions:
        return {
            "max_cur_rain": 0.0,
            "max_rain_24h": 0.0,
            "max_cur_wind": 0.0,
            "max_wind_24h": 0.0,
            "max_humidity": 0.0,
            "critical_city": municipalities[0] if municipalities else "Zona Sul",
        }

    max_cur_rain = 0.0
    max_rain_24h = 0.0
    max_cur_wind = 0.0
    max_wind_24h = 0.0
    max_humidity = 0.0
    critical_city = regions[0].municipality
    highest_stress = -1.0

    for r in regions:
        cur = (
            WeatherObservation.query
            .filter_by(region_id=r.id, is_forecast=False)
            .order_by(WeatherObservation.observed_at.desc())
            .first()
        )
        fcs = (
            WeatherObservation.query
            .filter(
                WeatherObservation.region_id == r.id,
                WeatherObservation.is_forecast == True,
                WeatherObservation.observed_at >= current_hour_floor,
            )
            .order_by(WeatherObservation.observed_at.asc())
            .limit(24)
            .all()
        )

        c_rain = (cur.precipitation_mm or 0.0) if cur else 0.0
        c_wind = (cur.wind_speed or 0.0) if cur else 0.0
        c_hum = (cur.humidity_percent or 0.0) if cur else 0.0
        r_24h = sum((f.precipitation_mm or 0.0) for f in fcs)
        w_24h = max([f.wind_speed or 0.0 for f in fcs], default=0.0)

        max_cur_rain = max(max_cur_rain, c_rain)
        max_rain_24h = max(max_rain_24h, r_24h)
        max_cur_wind = max(max_cur_wind, c_wind)
        max_wind_24h = max(max_wind_24h, w_24h, c_wind)
        max_humidity = max(max_humidity, c_hum)

        stress = (c_rain * 4.0) + r_24h + (max(c_wind, w_24h) * 0.6)
        if stress > highest_stress:
            highest_stress = stress
            critical_city = r.municipality

    return {
        "max_cur_rain": round(max_cur_rain, 1),
        "max_rain_24h": round(max_rain_24h, 1),
        "max_cur_wind": round(max_cur_wind, 1),
        "max_wind_24h": round(max_wind_24h, 1),
        "max_humidity": round(max_humidity, 0),
        "critical_city": critical_city,
    }


def _evaluate_corridor_status(corridor: dict, metrics: dict) -> tuple[str, str]:
    """
    Determina o status operacional e a descrição técnica do corredor
    com base no tipo de via (Federal, Serra, Estadual Urbana/Interior ou Travessia Hidroviária).
    """
    c_type = corridor["type"]
    cur_rain = metrics["max_cur_rain"]
    rain_24h = metrics["max_rain_24h"]
    peak_wind = metrics["max_wind_24h"]
    cur_wind = metrics["max_cur_wind"]
    hum = metrics["max_humidity"]
    city = metrics["critical_city"]

    # 1. Lógica específica para a Travessia Hidroviária (Balsa Rio Grande <-> São José do Norte)
    if c_type == "hidroviario":
        if cur_wind >= corridor["wind_critical"] or peak_wind >= corridor["wind_critical"]:
            return (
                "Risco de Suspensão (Vento Forte no Canal)",
                f"Rajadas de até {peak_wind:.0f} km/h no canal de acesso ({city}). Manobras de balsa e lanchas sujeitas a paralisação temporária por segurança da navegação."
            )
        if hum >= 96 and cur_wind <= 18.0:
            return (
                "Atenção (Restrição de Visibilidade / Nevoeiro)",
                f"Umidade em {hum:.0f}% com formação de nevoeiro no canal da Lagoa dos Patos. Travessia sujeita a atrasos operacionais."
            )
        if peak_wind >= corridor["wind_alert"] or rain_24h >= corridor["rain_alert_24h"]:
            return (
                "Operação em Atenção (Marola / Vento Moderado)",
                f"Vento de {peak_wind:.0f} km/h e acumulado de {rain_24h:.1f} mm exigem cautela no embarque entre Rio Grande e São José do Norte."
            )
        return ("Operação Normal", f"{corridor['normal_desc']} (Vento máx: {peak_wind:.0f} km/h).")

    # 2. Lógica para Rodovias (Federais e Estaduais)
    # Condição Crítica (Bloqueio Parcial / Alerta Máximo)
    if cur_rain >= 8.0 or rain_24h >= corridor["rain_critical_24h"] or peak_wind >= corridor["wind_critical"]:
        if c_type == "estadual_urbana":
            return (
                "Alerta de Alagamento na Pista",
                f"Chuva intensa ({rain_24h:.1f} mm/24h | {cur_rain:.1f} mm agora) em {city}. Risco elevado de lâmina d'água nas faixas laterais e rotatórias de acesso ao Cassino."
            )
        if c_type == "estadual_interior":
            return (
                "Risco Crítico em Pontes e Trechos Rurais",
                f"Volume elevado ({rain_24h:.1f} mm/24h) e vento de {peak_wind:.0f} km/h na altura de {city}. Risco de submersão de pontes baixas e barro na pista."
            )
        if c_type == "serra_porto":
            return (
                "Alerta Severo na Serra / Acesso Portuário",
                f"Precipitação de {rain_24h:.1f} mm/24h e rajadas de {peak_wind:.0f} km/h ({city}). Risco de aquaplanagem, queda de galhos e lentidão na Serra dos Tapes."
            )
        return (
            "Condição Crítica (Chuva / Vento Severo)",
            f"Acumulado de {rain_24h:.1f} mm/24h e vento de até {peak_wind:.0f} km/h no trecho de {city} ({corridor['feature_note']}). Reduza drasticamente a velocidade."
        )

    # Condição de Atenção (Pista Molhada / Vento Lateral / Neblina)
    if cur_rain >= 0.4 or rain_24h >= corridor["rain_alert_24h"] or peak_wind >= corridor["wind_alert"]:
        reasons = []
        if cur_rain >= 0.4:
            reasons.append(f"pista molhada ({cur_rain:.1f} mm/h agora)")
        elif rain_24h >= corridor["rain_alert_24h"]:
            reasons.append(f"chuva prevista de {rain_24h:.1f} mm/24h")
        if peak_wind >= corridor["wind_alert"]:
            reasons.append(f"vento lateral de {peak_wind:.0f} km/h")

        reason_str = " e ".join(reasons)
        return (
            "Atenção na Pista",
            f"Trecho próximo a {city} com {reason_str}. Atenção redobrada em: {corridor['feature_note']}."
        )

    # Condição de Neblina densa em rodovias de várzea/planície (BR-471 Taim / BR-116)
    if hum >= 96 and cur_wind <= 15.0:
        return (
            "Atenção (Visibilidade Reduzida / Neblina)",
            f"Umidade superficial de {hum:.0f}% favorece bancos de neblina no trecho de {city} ({corridor['feature_note']}). Mantenha farol baixo aceso."
        )

    return (
        "Fluxo Normal",
        f"{corridor['normal_desc']} (Vento máx: {peak_wind:.0f} km/h | Chuva 24h: {rain_24h:.1f} mm)."
    )


def update_highway_conditions() -> dict:
    """
    Atualiza as condições dos 7 corredores rodoviários e hidroviários da Zona Sul do RS.
    """
    now_utc = datetime.now(timezone.utc)
    updated_count = 0

    for corridor in HIGHWAY_CORRIDORS:
        metrics = _get_corridor_weather_metrics(corridor["municipalities"])
        status_label, desc_text = _evaluate_corridor_status(corridor, metrics)
        source_label = f"{corridor['jurisdiction']} + Telemetria CLIMAE"

        existing = TrafficEvent.query.filter_by(highway=corridor["highway"]).first()
        if existing:
            existing.status = status_label
            existing.description = desc_text
            existing.source = source_label
            existing.reported_at = now_utc
        else:
            new_ev = TrafficEvent(
                highway=corridor["highway"],
                status=status_label,
                description=desc_text,
                source=source_label,
                reported_at=now_utc,
            )
            db.session.add(new_ev)

        updated_count += 1

    db.session.commit()
    return {"corridors_updated": updated_count, "timestamp": now_utc.isoformat()}


def get_traffic_panel_data() -> list[dict]:
    """
    Retorna a lista estruturada das ocorrências e condições dos 7 corredores rodoviários.
    Se o banco estiver vazio, aciona update_highway_conditions() para geração inicial.
    """
    corridor_events = TrafficEvent.query.filter(TrafficEvent.highway.isnot(None)).order_by(TrafficEvent.id.asc()).all()
    if not corridor_events:
        update_highway_conditions()
        corridor_events = TrafficEvent.query.filter(TrafficEvent.highway.isnot(None)).order_by(TrafficEvent.id.asc()).all()

    return [
        {
            "id": ev.id,
            "highway": ev.highway,
            "status": ev.status,
            "description": ev.description,
            "source": ev.source,
            "reported_at": (
                ev.reported_at.replace(tzinfo=timezone.utc).isoformat()
                if ev.reported_at and ev.reported_at.tzinfo is None
                else (ev.reported_at.isoformat() if ev.reported_at else None)
            ),
        }
        for ev in corridor_events
    ]


def get_corridors_for_municipality(municipality: str) -> list[dict]:
    """Retorna os metadados dos corredores rodoviários que cruzam determinado município."""
    if not municipality:
        return []
    m_clean = municipality.strip().lower()
    return [
        c for c in HIGHWAY_CORRIDORS
        if any(m_clean == m.strip().lower() for m in c.get("municipalities", []))
    ]


def get_active_traffic_events_for_municipality(municipality: str) -> list[TrafficEvent]:
    """
    Retorna os eventos rodoviários com condições adversas ativas (Atenção, Alerta ou Risco Crítico)
    para os corredores que cortam o município informado.
    """
    corridors = get_corridors_for_municipality(municipality)
    if not corridors:
        return []

    highway_names = [c["highway"] for c in corridors]
    events = TrafficEvent.query.filter(TrafficEvent.highway.in_(highway_names)).all()

    normal_labels = {"Fluxo Normal", "Operação Normal"}
    return [ev for ev in events if ev.status and ev.status not in normal_labels]
