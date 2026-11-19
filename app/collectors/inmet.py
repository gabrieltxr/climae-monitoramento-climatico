"""
Coletor das estações meteorológicas automáticas do INMET (medição real).

A API de dados horários exige token, pedido por e-mail a cadastro.act@inmet.gov.br.
Sem INMET_TOKEN no .env o coletor não faz nada e o painel segue só com os modelos.

Dados brutos: o INMET não aplica controle de consistência às estações automáticas.
Horários em UTC; a API guarda só os últimos 90 dias.
"""
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.models import db, Region, StationObservation

logger = logging.getLogger(__name__)

# Rota com token. Pode ser ajustada pelo .env (INMET_URL_TEMPLATE) se o INMET mudar o formato.
DEFAULT_URL_TEMPLATE = "https://apitempo.inmet.gov.br/token/estacao/{start}/{end}/{station}/{token}"
REQUEST_TIMEOUT = (10, 30)
# O servidor do INMET derruba conexões sem User-Agent de navegador.
USER_AGENT = "Mozilla/5.0 (compatible; CLIMAE/1.0)"

DEFAULT_MAX_DISTANCE_KM = 30.0
# Janela das últimas 12h só vale como medida se tiver ao menos 9 horas lidas.
MIN_HOURS_FOR_12H_WINDOW = 9
# Leitura mais recente com mais de 3h = estação considerada fora do ar.
STALE_AFTER_HOURS = 3


@dataclass(frozen=True)
class InmetStation:
    code: str
    name: str
    latitude: float
    longitude: float


# Estações automáticas da Zona Sul e vizinhança (lista pública /estacoes/T, 19/11/2026).
# B829 (Taim) estava em pane: fica na lista e entra sozinha quando voltar a publicar.
INMET_STATIONS = (
    InmetStation("A802", "Rio Grande", -32.07888888, -52.16777777),
    InmetStation("A887", "Capão do Leão (Pelotas)", -31.8025, -52.40722222),
    InmetStation("A836", "Jaguarão", -32.53472221, -53.37583332),
    InmetStation("A811", "Canguçu", -31.40333333, -52.70083333),
    InmetStation("B831", "São Lourenço do Sul", -31.34055555, -52.01083333),
    InmetStation("A899", "Santa Vitória do Palmar - Barra do Chuí", -33.74222221, -53.37222221),
    InmetStation("B830", "Santa Vitória do Palmar (Centro)", -33.53166666, -53.35027777),
    InmetStation("B829", "Santa Vitória do Palmar (Reserva do Taim)", -32.83583333, -52.6436111),
    InmetStation("B826", "Herval", -32.01277777, -53.40305555),
    InmetStation("B823", "Pinheiro Machado", -31.57472221, -53.38833333),
)
STATIONS_BY_CODE = {s.code: s for s in INMET_STATIONS}


def get_inmet_token() -> str:
    return os.getenv("INMET_TOKEN", "").strip()


def get_max_distance_km() -> float:
    try:
        return float(os.getenv("INMET_MAX_DISTANCE_KM", DEFAULT_MAX_DISTANCE_KM))
    except ValueError:
        return DEFAULT_MAX_DISTANCE_KM


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.radians
    a = (math.sin(p(lat2 - lat1) / 2) ** 2
         + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(p(lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def stations_near(latitude: float, longitude: float, max_km: float | None = None) -> list[tuple[InmetStation, float]]:
    """Estações dentro do raio, da mais próxima para a mais distante."""
    limit = get_max_distance_km() if max_km is None else max_km
    ranked = sorted(
        ((s, haversine_km(latitude, longitude, s.latitude, s.longitude)) for s in INMET_STATIONS),
        key=lambda item: item[1],
    )
    return [(s, d) for s, d in ranked if d <= limit]


def _create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


http_session = _create_session()


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except ValueError:
        return None
    if math.isnan(number) or number <= -9999:
        return None
    return number


def _ms_to_kmh(value) -> float | None:
    number = _to_float(value)
    return round(number * 3.6, 1) if number is not None else None


def parse_inmet_row(row: dict) -> dict | None:
    """Converte uma linha horária do INMET; None se faltar data/hora ou se não houver nenhuma medida."""
    if not isinstance(row, dict):
        return None
    date_str = str(row.get("DT_MEDICAO") or "").strip()
    hour_str = str(row.get("HR_MEDICAO") or "").strip().zfill(4)
    try:
        observed_at = datetime.strptime(f"{date_str} {hour_str[:2]}", "%Y-%m-%d %H").replace(tzinfo=timezone.utc)
    except ValueError:
        return None

    precipitation = _to_float(row.get("CHUVA"))
    if precipitation is not None and precipitation < 0:
        precipitation = None
    parsed = {
        "observed_at": observed_at,
        "temperature_c": _to_float(row.get("TEM_INS")),
        "humidity_percent": _to_float(row.get("UMD_INS")),
        "precipitation_mm": precipitation,
        "wind_speed_kmh": _ms_to_kmh(row.get("VEN_VEL")),
        "wind_gust_kmh": _ms_to_kmh(row.get("VEN_RAJ")),
        "wind_direction": _to_float(row.get("VEN_DIR")),
        "pressure_hpa": _to_float(row.get("PRE_INS")),
    }
    if all(v is None for k, v in parsed.items() if k != "observed_at"):
        return None
    return parsed


def fetch_station_rows(code: str, start: datetime, end: datetime, token: str) -> list[dict] | None:
    """Linhas horárias da estação; [] se a API não tem dados (204), None em erro."""
    template = os.getenv("INMET_URL_TEMPLATE", "").strip() or DEFAULT_URL_TEMPLATE
    url = template.format(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), station=code, token=token)
    try:
        res = http_session.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        # Não loga a URL: ela contém o token.
        logger.warning("INMET %s: falha de conexão (%s)", code, type(exc).__name__)
        return None
    if res.status_code == 204:
        return []
    if res.status_code != 200:
        logger.warning("INMET %s: HTTP %s", code, res.status_code)
        return None
    try:
        data = res.json()
    except ValueError:
        logger.warning("INMET %s: resposta não é JSON", code)
        return None
    if not isinstance(data, list):
        logger.warning("INMET %s: formato inesperado (%s)", code, type(data).__name__)
        return None
    return data


def save_station_rows(code: str, rows: list[dict]) -> tuple[int, int]:
    inserted = updated = 0
    for row in rows:
        parsed = parse_inmet_row(row)
        if parsed is None:
            continue
        observed_naive = parsed["observed_at"].replace(tzinfo=None)
        existing = StationObservation.query.filter_by(station_code=code, observed_at=observed_naive).first()
        if existing is None:
            existing = StationObservation(station_code=code, observed_at=observed_naive)
            db.session.add(existing)
            inserted += 1
        else:
            updated += 1
        for field, value in parsed.items():
            if field != "observed_at":
                setattr(existing, field, value)
        existing.collected_at = datetime.now(timezone.utc)
    db.session.commit()
    return inserted, updated


def collect_inmet_stations(now: datetime | None = None) -> dict:
    """Busca ontem e hoje (UTC) de cada estação do catálogo. Sem token, não faz nada."""
    token = get_inmet_token()
    if not token:
        return {"status": "sem_token", "stations": 0, "inserted": 0, "updated": 0, "failed": []}

    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    result = {"status": "ok", "stations": 0, "inserted": 0, "updated": 0, "failed": []}
    for station in INMET_STATIONS:
        rows = fetch_station_rows(station.code, start, now, token)
        if rows is None:
            result["failed"].append(station.code)
            continue
        try:
            ins, upd = save_station_rows(station.code, rows)
        except Exception as exc:
            db.session.rollback()
            logger.error("INMET %s: erro ao gravar (%s)", station.code, exc)
            result["failed"].append(station.code)
            continue
        result["stations"] += 1
        result["inserted"] += ins
        result["updated"] += upd
    logger.info("INMET: %d estações lidas, %d novas horas, %d atualizadas, falhas: %s",
                result["stations"], result["inserted"], result["updated"], result["failed"] or "nenhuma")
    return result


def get_region_station_summary(region: Region, now: datetime | None = None) -> dict | None:
    """
    Medição da estação mais próxima da região (dentro do raio) que esteja publicando.
    Pula estações sem leitura nas últimas STALE_AFTER_HOURS horas. None se não houver.
    """
    now = now or datetime.now(timezone.utc)
    now_naive = now.astimezone(timezone.utc).replace(tzinfo=None)
    for station, distance in stations_near(region.latitude, region.longitude):
        recent = (
            StationObservation.query
            .filter(
                StationObservation.station_code == station.code,
                StationObservation.observed_at > now_naive - timedelta(hours=12),
                StationObservation.observed_at <= now_naive,
            )
            .order_by(StationObservation.observed_at.desc())
            .all()
        )
        if not recent or recent[0].observed_at < now_naive - timedelta(hours=STALE_AFTER_HOURS):
            continue

        latest = recent[0]
        rain_hours = [o.precipitation_mm for o in recent if o.precipitation_mm is not None]
        wind_hours = [o.wind_speed_kmh for o in recent if o.wind_speed_kmh is not None]
        gust_hours = [o.wind_gust_kmh for o in recent if o.wind_gust_kmh is not None]
        return {
            "code": station.code,
            "name": station.name,
            "distance_km": round(distance, 1),
            "observed_at": latest.observed_at.replace(tzinfo=timezone.utc),
            "temperature_c": latest.temperature_c,
            "humidity_percent": latest.humidity_percent,
            "precipitation_1h_mm": latest.precipitation_mm,
            "wind_speed_kmh": latest.wind_speed_kmh,
            "wind_gust_kmh": latest.wind_gust_kmh,
            "wind_direction": latest.wind_direction,
            "hours_12h": len(recent),
            "rain_12h_mm": round(sum(rain_hours), 1) if len(rain_hours) >= MIN_HOURS_FOR_12H_WINDOW else None,
            "peak_wind_12h_kmh": max(wind_hours) if len(wind_hours) >= MIN_HOURS_FOR_12H_WINDOW else None,
            "peak_gust_12h_kmh": max(gust_hours) if gust_hours else None,
        }
    return None
