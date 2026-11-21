"""
Normais climatológicas oficiais do INMET 1991–2020 (estações convencionais da Zona Sul),
lidas de app/data/inmet_normals_1991_2020.json (gerado por scripts/build_inmet_normals.py).

São referência ao lado da normal ERA5 do CLIMAE (2000–presente, uma por região), que segue
sendo a usada nas anomalias. Cada região recebe a estação mais próxima até
INMET_NORMALS_MAX_DISTANCE_KM (padrão 60 km); além disso não há normal oficial representativa
(ex.: Jaguarão, a mais de 100 km de qualquer uma das 3 estações).
"""
import json
import os
from functools import lru_cache

from app.collectors.inmet import haversine_km
from app.models import Region

DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "inmet_normals_1991_2020.json")
DEFAULT_MAX_DISTANCE_KM = 60.0


@lru_cache(maxsize=1)
def load_normals() -> dict:
    with open(DATA_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def get_max_distance_km() -> float:
    try:
        value = float(os.getenv("INMET_NORMALS_MAX_DISTANCE_KM", DEFAULT_MAX_DISTANCE_KM))
    except ValueError:
        return DEFAULT_MAX_DISTANCE_KM
    return value if value > 0 else DEFAULT_MAX_DISTANCE_KM


def get_region_inmet_normal(region: Region, month: int) -> dict | None:
    """Normal oficial do mês na estação mais próxima da região; None se nenhuma estiver no raio."""
    data = load_normals()
    nearest = min(
        ((haversine_km(region.latitude, region.longitude, s["latitude"], s["longitude"]), s) for s in data["stations"]),
        key=lambda pair: pair[0],
        default=None,
    )
    if nearest is None or nearest[0] > get_max_distance_km():
        return None
    distance, station = nearest
    values = next((m for m in station["months"] if m["month"] == month), None)
    if values is None:
        return None
    return {
        "station_code": station["code"],
        "station_name": station["name"],
        "distance_km": round(distance, 1),
        "period": data["period"],
        "source": data["source"],
        **values,
    }
