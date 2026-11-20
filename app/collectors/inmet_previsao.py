"""
Previsão oficial do INMET por município (apiprevmet3.inmet.gov.br/previsao/{geocode IBGE}).
API pública, sem token; como nos avisos, exige User-Agent.

Resposta: {geocode: {"dd/mm/aaaa": ...}} com 5 dias. Os dias 1–2 vêm por turno
({"manha": {...}, "tarde": {...}, "noite": {...}}); os dias 3–5 vêm num bloco só (aqui
period = "dia"). Campos usados: resumo (texto), temp_max/temp_min (do dia, iguais nos turnos),
umidade_max/umidade_min, dir_vento ("NE-E"), int_vento ("Fracos"), cod_icone. Não há chuva em
mm nem probabilidade: é texto complementar, fora do risco e dos alertas.

~250 KB por município, 98% em ícones base64 (descartados); só um geocode por chamada e sem
marcador de versão. Por isso cada município é consultado no máximo a cada
INMET_PREVISAO_HOURS (padrão 3), contado pelo fetched_at gravado (vale após reiniciar).
Cada consulta bem-sucedida substitui a previsão anterior do município; falha não apaga nada.
"""
import logging
import os
from datetime import date, datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.models import db, Region, OfficialForecast
from app.regions_catalog import MUNICIPALITY_GEOCODES

logger = logging.getLogger(__name__)

FORECAST_URL = "https://apiprevmet3.inmet.gov.br/previsao/{geocode}"
REQUEST_TIMEOUT = (10, 30)
USER_AGENT = "Mozilla/5.0 (compatible; CLIMAE/1.0)"
BRT = timezone(timedelta(hours=-3))
DEFAULT_INTERVAL_HOURS = 3.0
PERIODS = ("manha", "tarde", "noite")
PERIOD_ORDER = {"manha": 0, "tarde": 1, "noite": 2, "dia": 3}


def _create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


http_session = _create_session()


def get_interval_hours() -> float:
    try:
        value = float(os.getenv("INMET_PREVISAO_HOURS", DEFAULT_INTERVAL_HOURS))
    except ValueError:
        return DEFAULT_INTERVAL_HOURS
    return value if value > 0 else DEFAULT_INTERVAL_HOURS


def _to_float(value) -> float | None:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _text(value, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] or None


def _parse_block(block: dict) -> dict | None:
    summary = _text(block.get("resumo"), 200)
    if not summary:
        return None
    return {
        "summary": summary,
        "temp_min": _to_float(block.get("temp_min")),
        "temp_max": _to_float(block.get("temp_max")),
        "humidity_min": _to_float(block.get("umidade_min")),
        "humidity_max": _to_float(block.get("umidade_max")),
        "wind_direction": _text(block.get("dir_vento"), 10),
        "wind_intensity": _text(block.get("int_vento"), 30),
        "icon_code": _text(block.get("cod_icone"), 10),
    }


def parse_forecast(payload, geocode: str) -> list[dict] | None:
    """Linhas (forecast_date, period, campos) do município; None se o formato não for o esperado."""
    if not isinstance(payload, dict) or not isinstance(payload.get(geocode), dict):
        return None
    rows = []
    for day_text, day in payload[geocode].items():
        try:
            forecast_date = datetime.strptime(str(day_text).strip(), "%d/%m/%Y").date()
        except ValueError:
            continue
        if not isinstance(day, dict):
            continue
        if any(isinstance(day.get(p), dict) for p in PERIODS):
            blocks = [(p, day[p]) for p in PERIODS if isinstance(day.get(p), dict)]
        else:
            blocks = [("dia", day)]
        for period, block in blocks:
            fields = _parse_block(block)
            if fields:
                rows.append({"forecast_date": forecast_date, "period": period, **fields})
    return rows or None


def fetch_forecast(geocode: str) -> list[dict] | None:
    try:
        res = http_session.get(FORECAST_URL.format(geocode=geocode), timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("Previsão INMET %s: falha de conexão (%s)", geocode, type(exc).__name__)
        return None
    if res.status_code != 200:
        logger.warning("Previsão INMET %s: HTTP %s", geocode, res.status_code)
        return None
    try:
        payload = res.json()
    except ValueError:
        logger.warning("Previsão INMET %s: resposta não é JSON", geocode)
        return None
    rows = parse_forecast(payload, geocode)
    if rows is None:
        logger.warning("Previsão INMET %s: formato inesperado", geocode)
    return rows


def _last_fetch_by_geocode() -> dict:
    rows = (
        db.session.query(OfficialForecast.geocode, db.func.max(OfficialForecast.fetched_at))
        .group_by(OfficialForecast.geocode)
        .all()
    )
    return dict(rows)


def sync_official_forecasts(now: datetime | None = None) -> dict:
    """Consulta os municípios cuja previsão tem mais de INMET_PREVISAO_HOURS e substitui as linhas."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)
    interval = timedelta(hours=get_interval_hours())
    last = _last_fetch_by_geocode()
    result = {"updated": 0, "skipped": 0, "failed": 0}

    for geocode in MUNICIPALITY_GEOCODES.values():
        fetched_at = last.get(geocode)
        if fetched_at is not None and now - fetched_at < interval:
            result["skipped"] += 1
            continue
        rows = fetch_forecast(geocode)
        if rows is None:
            result["failed"] += 1
            continue
        OfficialForecast.query.filter_by(geocode=geocode).delete()
        db.session.add_all(OfficialForecast(geocode=geocode, fetched_at=now, **row) for row in rows)
        db.session.commit()
        result["updated"] += 1

    if result["updated"] or result["failed"]:
        logger.info("Previsão INMET: %d municípios atualizados, %d sem mudança, %d com falha.",
                    result["updated"], result["skipped"], result["failed"])
    return result


def get_region_forecast(region: Region, now: datetime | None = None) -> dict | None:
    """Previsão oficial do município da região a partir de hoje (Brasília); None se não houver."""
    geocode = MUNICIPALITY_GEOCODES.get(region.municipality)
    if not geocode:
        return None
    today = (now or datetime.now(timezone.utc)).astimezone(BRT).date()
    rows = (
        OfficialForecast.query
        .filter(OfficialForecast.geocode == geocode, OfficialForecast.forecast_date >= today)
        .all()
    )
    if not rows:
        return None
    rows.sort(key=lambda r: (r.forecast_date, PERIOD_ORDER.get(r.period, 9)))

    days: dict[date, dict] = {}
    for r in rows:
        day = days.setdefault(r.forecast_date, {
            "date": r.forecast_date.isoformat(),
            "temp_min": r.temp_min,
            "temp_max": r.temp_max,
            "periods": [],
        })
        day["periods"].append({
            "period": r.period,
            "summary": r.summary,
            "humidity_min": r.humidity_min,
            "humidity_max": r.humidity_max,
            "wind_direction": r.wind_direction,
            "wind_intensity": r.wind_intensity,
            "icon_code": r.icon_code,
        })
    fetched_at = max(r.fetched_at for r in rows)
    return {
        "municipality": region.municipality,
        "fetched_at": fetched_at.replace(tzinfo=timezone.utc).isoformat(),
        "source": "INMET (previsão oficial)",
        "days": list(days.values()),
    }
