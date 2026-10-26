from datetime import datetime, timedelta, timezone
from app.models import db, Region, TrafficEvent, WeatherObservation

ROAD_CORRIDORS = [
    {
        "municipality": "Pelotas",
        "region_name": "Centro",
        "corridor": "BR-116",
        "segment": "Km 510 ao 525 (Polo Pelotas)",
        "source": "PRF / Ecosul",
    },
    {
        "municipality": "Rio Grande",
        "region_name": "Centro / Zona Portuária",
        "corridor": "BR-392",
        "segment": "Acesso ao Porto do Rio Grande",
        "source": "PRF / Ecosul",
    },
    {
        "municipality": "Santa Vitória do Palmar",
        "region_name": "Sede Urbana",
        "corridor": "BR-471",
        "segment": "Trecho Est. Ecológica do Taim",
        "source": "PRF",
    },
]


def update_institutional_traffic() -> dict:
    """
    Atualiza os boletins das 3 rodovias federais no banco de dados,
    garantindo apenas 1 registro ativo e atualizado por rodovia.
    """
    now_utc = datetime.now(timezone.utc)
    updated = 0

    for item in ROAD_CORRIDORS:
        region = Region.query.filter_by(
            municipality=item["municipality"],
            name=item["region_name"]
        ).first()
        if not region:
            continue

        obs = (
            WeatherObservation.query
            .filter_by(region_id=region.id, is_forecast=False)
            .order_by(WeatherObservation.observed_at.desc())
            .first()
        )

        rain = (obs.precipitation_mm or 0.0) if obs else 0.0
        wind = (obs.wind_speed or 0.0) if obs else 0.0

        if rain >= 5.0 or wind >= 55.0:
            desc = f"{item['corridor']}: Alerta de água na pista / vento severo ({item['segment']})"
            status = "Alerta"
        elif rain > 0.2 or wind >= 38.0:
            desc = f"{item['corridor']}: Atenção — Pista úmida / vento moderado ({item['segment']})"
            status = "Atenção"
        else:
            desc = f"{item['corridor']}: Pista livre e fluxo normal ({item['segment']})"
            status = "Normal"

        # Busca registro existente da mesma rodovia
        existing = (
            TrafficEvent.query
            .filter(
                TrafficEvent.region_id == region.id,
                TrafficEvent.event_type.like(f"{item['corridor']}%")
            )
            .order_by(TrafficEvent.occurred_at.desc())
            .first()
        )

        if existing:
            existing.event_type = desc
            existing.status = status
            existing.source = item["source"]
            existing.source_updated_at = now_utc
            updated += 1
        else:
            ev = TrafficEvent(
                region_id=region.id,
                event_type=desc,
                status=status,
                occurred_at=now_utc,
                source=item["source"],
                source_updated_at=now_utc,
            )
            db.session.add(ev)
            updated += 1

    db.session.commit()
    return {"status": "ok", "updated_bulletins": updated, "updated_at": now_utc.isoformat()}


def get_latest_traffic_timestamp() -> str | None:
    latest = TrafficEvent.query.order_by(TrafficEvent.source_updated_at.desc()).first()
    if not latest:
        return None
    dt = latest.source_updated_at or latest.occurred_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def get_traffic_panel_data(region_id: int | None = None) -> dict:
    """
    Lê os registros de rodovias. Se os dados no banco tiverem mais de 15 minutos,
    sincroniza automaticamente para não exibir registros antigos de dias anteriores.
    """
    now_utc = datetime.now(timezone.utc)
    latest_record = TrafficEvent.query.order_by(TrafficEvent.source_updated_at.desc()).first()

    should_refresh = False
    if not latest_record:
        should_refresh = True
    else:
        last_dt = latest_record.source_updated_at or latest_record.occurred_at
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        if (now_utc - last_dt) > timedelta(minutes=15):
            should_refresh = True

    if should_refresh:
        update_institutional_traffic()

    stale_limit = now_utc - timedelta(hours=2)
    query = TrafficEvent.query
    if region_id:
        query = query.filter_by(region_id=region_id)

    events = query.order_by(TrafficEvent.id.asc()).limit(10).all()
    items = []
    latest_ts = None

    for ev in events:
        reg = Region.query.get(ev.region_id)
        upd_time = ev.source_updated_at or ev.occurred_at
        if upd_time.tzinfo is None:
            upd_time = upd_time.replace(tzinfo=timezone.utc)

        if latest_ts is None or upd_time > latest_ts:
            latest_ts = upd_time

        is_stale = upd_time < stale_limit

        items.append({
            "id": ev.id,
            "municipality": reg.municipality if reg else "",
            "region_name": reg.name if reg else "",
            "event_type": ev.event_type,
            "status": ev.status,
            "is_stale": is_stale,
            "updated_at": upd_time.isoformat(),
            "source": ev.source,
        })

    return {
        "api_configured": True,
        "source_status": "PRF & Ecosul Ativas",
        "corridors_monitored": ["BR-116", "BR-392", "BR-471"],
        "last_updated_at": latest_ts.isoformat() if latest_ts else None,
        "events": items,
    }
