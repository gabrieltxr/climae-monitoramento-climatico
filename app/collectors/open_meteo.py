import logging
import math
import time
from datetime import datetime, timezone
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from app.models import db, Region, WeatherObservation

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = (10, 25)

WMO_RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}


def _create_resilient_session() -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


http_session = _create_resilient_session()


def fetch_multi_model_weather(latitude: float, longitude: float) -> dict | None:
    """
    Consulta simultaneamente os modelos globais (Best Match/ICON, ECMWF Europeu e GFS/NOAA),
    incluindo velocidade e direção do vento (wind_direction_10m) para as camadas sinóticas v0.2.
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "models": "best_match,ecmwf_ifs025,gfs_seamless",
        "current": (
            "temperature_2m,relative_humidity_2m,precipitation,rain,showers,"
            "weather_code,wind_speed_10m,wind_direction_10m"
        ),
        "hourly": (
            "temperature_2m,relative_humidity_2m,precipitation_probability,"
            "precipitation,wind_speed_10m,wind_direction_10m"
        ),
        "past_hours": 12,
        "forecast_days": 2,
        "timezone": "UTC",
    }
    try:
        res = http_session.get(OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT)
        res.raise_for_status()
        data = res.json()
        return data if ("current" in data and "hourly" in data) else None
    except requests.RequestException as exc:
        logger.warning("Falha na coleta multi-modelo para (%s, %s): %s", latitude, longitude, exc)
        return None


MODEL_SUFFIXES = ["", "_best_match", "_ecmwf_ifs025", "_gfs_seamless"]


def _extract_model_series(container: dict, base_key: str) -> list[list]:
    series = []
    for suffix in MODEL_SUFFIXES:
        k = f"{base_key}{suffix}"
        if k in container and isinstance(container[k], list):
            series.append(container[k])
    return series


def _avg_model_at_index(series_list: list[list], idx: int) -> float | None:
    vals = [
        s[idx] for s in series_list
        if idx < len(s) and s[idx] is not None
    ]
    return round(sum(vals) / len(vals), 1) if vals else None


# Abaixo desta concordância (tamanho do vetor resultante / soma dos pesos), os modelos
# divergem demais e a direção média não representa nenhum deles: mantém o modelo principal.
WIND_DIR_MIN_CONSISTENCY = 0.10


def _circular_mean_deg(values: list[float], weights: list[float] | None = None) -> float | None:
    """
    Média circular de ângulos em graus (0-360), via soma de vetores.
    A média aritmética simples falha perto do norte: (350° + 10°) / 2 = 180° (sul),
    quando o correto é 0° (norte).

    Com `weights` (velocidade do vento de cada modelo, alinhada a `values`), cada
    direção vira um vetor com o tamanho da sua velocidade: vento fraco, cuja direção
    é quase ruído, pesa pouco. Se faltar alguma velocidade, ou todas forem zero,
    usa vetores unitários (todos com o mesmo peso).

    Se os modelos divergem demais (concordância abaixo de WIND_DIR_MIN_CONSISTENCY,
    ex.: direções quase opostas), a direção é indefinida e o primeiro valor
    disponível (modelo principal) é mantido.
    """
    if weights is None:
        weights = [None] * len(values)
    pairs = [(float(v), w) for v, w in zip(values, weights) if v is not None]
    if not pairs:
        return None
    vals = [v for v, _ in pairs]
    ws = [w for _, w in pairs]
    if any(w is None for w in ws) or sum(max(float(w), 0.0) for w in ws) <= 0:
        ws = [1.0] * len(vals)
    else:
        ws = [max(float(w), 0.0) for w in ws]

    sin_sum = sum(w * math.sin(math.radians(v)) for v, w in zip(vals, ws))
    cos_sum = sum(w * math.cos(math.radians(v)) for v, w in zip(vals, ws))
    if math.hypot(sin_sum, cos_sum) / sum(ws) < WIND_DIR_MIN_CONSISTENCY:
        return round(vals[0] % 360.0, 1)
    mean = math.degrees(math.atan2(sin_sum, cos_sum)) % 360.0
    return round(mean, 1) % 360.0


def _aligned_model_series(container: dict, base_key: str) -> list[list | None]:
    """Séries por modelo na ordem de MODEL_SUFFIXES (None onde o modelo não veio),
    para casar direção e velocidade do MESMO modelo."""
    out = []
    for suffix in MODEL_SUFFIXES:
        s = container.get(f"{base_key}{suffix}")
        out.append(s if isinstance(s, list) else None)
    return out


def _wind_direction_at_index(dir_series: list[list | None], speed_series: list[list | None], idx: int) -> float | None:
    def at(s):
        return s[idx] if s is not None and idx < len(s) else None
    return _circular_mean_deg([at(s) for s in dir_series], [at(s) for s in speed_series])


def _extract_current_direction(current: dict, dir_key: str, speed_key: str) -> float | None:
    return _circular_mean_deg(
        [current.get(f"{dir_key}{suffix}") for suffix in MODEL_SUFFIXES],
        [current.get(f"{speed_key}{suffix}") for suffix in MODEL_SUFFIXES],
    )


def _consensus_rain_at_index(series_list: list[list], idx: int, wmo_code: int | None = None) -> float:
    vals = [
        float(s[idx]) for s in series_list
        if idx < len(s) and s[idx] is not None
    ]
    positive_vals = [v for v in vals if v > 0.0]

    if len(positive_vals) >= 2:
        blended = round(sum(positive_vals) / len(positive_vals), 2)
    elif len(positive_vals) == 1:
        blended = round(positive_vals[0] * 0.8, 2)
    else:
        blended = 0.0

    if blended == 0.0 and wmo_code in WMO_RAIN_CODES:
        blended = 0.2

    return blended


def _extract_current_value(current: dict, base_key: str, mode: str = "avg") -> float | None:
    vals = []
    for suffix in ["", "_best_match", "_ecmwf_ifs025", "_gfs_seamless"]:
        v = current.get(f"{base_key}{suffix}")
        if v is not None:
            vals.append(float(v))
    if not vals:
        return None
    if mode == "max":
        return max(vals)
    return round(sum(vals) / len(vals), 1)


def _parse_utc_iso(time_str: str) -> datetime:
    dt = datetime.fromisoformat(time_str)
    return dt.replace(tzinfo=timezone.utc)


def save_region_weather(region: Region, data: dict) -> dict:
    inserted = 0
    updated = 0
    now_utc = datetime.now(timezone.utc)
    current_hour_floor = now_utc.replace(minute=0, second=0, microsecond=0)
    current_hour_key = now_utc.strftime("%Y-%m-%dT%H:00")

    source_label = "Consenso Multi-Modelo (ECMWF + GFS + ICON)"

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temp_series = _extract_model_series(hourly, "temperature_2m")
    hum_series = _extract_model_series(hourly, "relative_humidity_2m")
    wind_series = _extract_model_series(hourly, "wind_speed_10m")
    wind_dir_series = _aligned_model_series(hourly, "wind_direction_10m")
    wind_speed_aligned = _aligned_model_series(hourly, "wind_speed_10m")
    precip_series = _extract_model_series(hourly, "precipitation")
    prob_series = _extract_model_series(hourly, "precipitation_probability")

    # 1. Processar dado OBSERVADO atual (is_forecast = False)
    current = data.get("current", {})
    if current and "time" in current:
        obs_time = _parse_utc_iso(current["time"])

        temp_final = _extract_current_value(current, "temperature_2m", "avg")
        hum_final = _extract_current_value(current, "relative_humidity_2m", "avg")
        wind_final = _extract_current_value(current, "wind_speed_10m", "avg")
        wind_dir_final = _extract_current_direction(current, "wind_direction_10m", "wind_speed_10m")
        wmo_code = _extract_current_value(current, "weather_code", "max")

        cur_precip = _extract_current_value(current, "precipitation", "max") or 0.0
        cur_rain = _extract_current_value(current, "rain", "max") or 0.0
        cur_showers = _extract_current_value(current, "showers", "max") or 0.0
        rain_final = max(cur_precip, cur_rain, cur_showers)

        if current_hour_key in times:
            idx_now = times.index(current_hour_key)
            hourly_consensus_rain = _consensus_rain_at_index(
                precip_series, idx_now, int(wmo_code) if wmo_code is not None else None
            )
            rain_final = max(rain_final, hourly_consensus_rain)
            if wind_dir_final is None:
                wind_dir_final = _wind_direction_at_index(wind_dir_series, wind_speed_aligned, idx_now)

        if rain_final == 0.0 and wmo_code is not None and int(wmo_code) in WMO_RAIN_CODES:
            rain_final = 0.2

        existing_obs = WeatherObservation.query.filter_by(
            region_id=region.id,
            observed_at=obs_time,
            is_forecast=False,
        ).first()

        if existing_obs:
            existing_obs.temperature_c = temp_final
            existing_obs.humidity_percent = hum_final
            existing_obs.precipitation_mm = round(rain_final, 2)
            existing_obs.wind_speed = wind_final
            existing_obs.wind_direction = wind_dir_final
            existing_obs.source = source_label
            existing_obs.collected_at = now_utc
            updated += 1
        else:
            new_obs = WeatherObservation(
                region_id=region.id,
                observed_at=obs_time,
                collected_at=now_utc,
                is_forecast=False,
                temperature_c=temp_final,
                humidity_percent=hum_final,
                precipitation_mm=round(rain_final, 2),
                rain_probability=None,
                wind_speed=wind_final,
                wind_direction=wind_dir_final,
                source=source_label,
            )
            db.session.add(new_obs)
            inserted += 1

    # 2. Processar série horária
    count_future_hours = 0
    for i, t_str in enumerate(times):
        slot_time = _parse_utc_iso(t_str)

        fc_temp = _avg_model_at_index(temp_series, i)
        fc_hum = _avg_model_at_index(hum_series, i)
        fc_wind = _avg_model_at_index(wind_series, i)
        fc_wind_dir = _wind_direction_at_index(wind_dir_series, wind_speed_aligned, i)
        fc_rain = _consensus_rain_at_index(precip_series, i)
        fc_prob = _avg_model_at_index(prob_series, i) or 0.0

        if slot_time < current_hour_floor:
            existing_past = WeatherObservation.query.filter_by(
                region_id=region.id,
                observed_at=slot_time,
                is_forecast=False,
            ).first()
            if not existing_past:
                past_obs = WeatherObservation(
                    region_id=region.id,
                    observed_at=slot_time,
                    collected_at=now_utc,
                    is_forecast=False,
                    temperature_c=fc_temp,
                    humidity_percent=fc_hum,
                    precipitation_mm=fc_rain,
                    rain_probability=None,
                    wind_speed=fc_wind,
                    wind_direction=fc_wind_dir,
                    source=source_label,
                )
                db.session.add(past_obs)
                inserted += 1
            continue

        if count_future_hours >= 24:
            break
        count_future_hours += 1

        if fc_rain >= 0.3 and fc_prob < 65.0:
            fc_prob = round((fc_prob + 75.0) / 2.0, 0)

        existing_fc = WeatherObservation.query.filter_by(
            region_id=region.id,
            observed_at=slot_time,
            is_forecast=True,
        ).first()

        if existing_fc:
            existing_fc.temperature_c = fc_temp
            existing_fc.humidity_percent = fc_hum
            existing_fc.rain_probability = fc_prob
            existing_fc.precipitation_mm = fc_rain
            existing_fc.wind_speed = fc_wind
            existing_fc.wind_direction = fc_wind_dir
            existing_fc.source = source_label
            existing_fc.collected_at = now_utc
            updated += 1
        else:
            new_fc = WeatherObservation(
                region_id=region.id,
                observed_at=slot_time,
                collected_at=now_utc,
                is_forecast=True,
                temperature_c=fc_temp,
                humidity_percent=fc_hum,
                rain_probability=fc_prob,
                precipitation_mm=fc_rain,
                wind_speed=fc_wind,
                wind_direction=fc_wind_dir,
                source=source_label,
            )
            db.session.add(new_fc)
            inserted += 1

    db.session.commit()
    return {"inserted": inserted, "updated": updated}


def collect_all_regions() -> dict:
    regions = Region.query.all()
    summary = {"total_regions": len(regions), "success": 0, "failed": 0, "inserted": 0, "updated": 0}

    for region in regions:
        data = fetch_multi_model_weather(region.latitude, region.longitude)
        if data:
            res = save_region_weather(region, data)
            summary["success"] += 1
            summary["inserted"] += res["inserted"]
            summary["updated"] += res["updated"]
        else:
            summary["failed"] += 1

        time.sleep(0.15)

    return summary
