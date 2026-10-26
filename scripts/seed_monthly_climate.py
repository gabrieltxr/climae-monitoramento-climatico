import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.models import db, Region, MonthlyClimateSummary

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
START_DATE = "2000-01-01"


def fetch_daily_archive(lat: float, lon: float, start_date: str, end_date: str) -> dict | None:
    """
    Busca a série diária histórica da reanálise ERA5 com tratamento inteligente
    para o limite de requisições por minuto (HTTP 429) da Open-Meteo.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": (
            "temperature_2m_mean,temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,wind_speed_10m_max"
        ),
        "timezone": "UTC",
    }
    headers = {"User-Agent": "CLIMAE-HistoricalSeeder/1.0"}

    for attempt in range(1, 5):
        try:
            res = requests.get(ARCHIVE_URL, params=params, headers=headers, timeout=45)
            if res.status_code == 429:
                wait_sec = 62
                print(f"    ⏳ Limite por minuto da API atingido (HTTP 429). Aguardando {wait_sec}s para liberar o próximo lote...")
                time.sleep(wait_sec)
                continue

            res.raise_for_status()
            data = res.json()
            if "daily" in data and "time" in data["daily"]:
                return data["daily"]
        except Exception as exc:
            wait_sec = 10 * attempt
            print(f"    [Tentativa {attempt}/4] Aviso na requisição ERA5: {exc}. Aguardando {wait_sec}s...")
            time.sleep(wait_sec)
    return None


def aggregate_and_save_region(region: Region, daily_data: dict) -> dict:
    """
    Agrupa os dados diários em resumos mensais (year, month) e calcula
    a Normal Climatológica (year = 0, month = 1..12) para a região.
    """
    dates = daily_data.get("time", [])
    t_mean = daily_data.get("temperature_2m_mean", [])
    t_max = daily_data.get("temperature_2m_max", [])
    t_min = daily_data.get("temperature_2m_min", [])
    precip = daily_data.get("precipitation_sum", [])
    wind_max = daily_data.get("wind_speed_10m_max", [])

    buckets = defaultdict(lambda: {
        "t_mean": [],
        "t_max": [],
        "t_min": [],
        "rain": [],
        "wind": [],
    })

    for i, d_str in enumerate(dates):
        year = int(d_str[0:4])
        month = int(d_str[5:7])
        b = buckets[(year, month)]

        if i < len(t_mean) and t_mean[i] is not None:
            b["t_mean"].append(float(t_mean[i]))
        if i < len(t_max) and t_max[i] is not None:
            b["t_max"].append(float(t_max[i]))
        if i < len(t_min) and t_min[i] is not None:
            b["t_min"].append(float(t_min[i]))
        if i < len(precip) and precip[i] is not None:
            b["rain"].append(float(precip[i]))
        if i < len(wind_max) and wind_max[i] is not None:
            b["wind"].append(float(wind_max[i]))

    now_utc = datetime.now(timezone.utc)
    monthly_records_count = 0

    climatology_accum = defaultdict(lambda: {
        "avg_temps": [],
        "max_temps": [],
        "min_temps": [],
        "total_rains": [],
        "rainy_days": [],
        "max_winds": [],
    })

    MonthlyClimateSummary.query.filter_by(region_id=region.id).delete()

    for (year, month), vals in sorted(buckets.items()):
        if not vals["t_mean"] and not vals["rain"]:
            continue

        avg_t = round(sum(vals["t_mean"]) / len(vals["t_mean"]), 1) if vals["t_mean"] else None
        max_t = round(max(vals["t_max"]), 1) if vals["t_max"] else None
        min_t = round(min(vals["t_min"]), 1) if vals["t_min"] else None
        tot_rain = round(sum(vals["rain"]), 1) if vals["rain"] else 0.0
        r_days = sum(1 for r in vals["rain"] if r >= 1.0)
        pk_wind = round(max(vals["wind"]), 1) if vals["wind"] else None

        summary = MonthlyClimateSummary(
            region_id=region.id,
            year=year,
            month=month,
            avg_temp_c=avg_t,
            max_temp_c=max_t,
            min_temp_c=min_t,
            total_rain_mm=tot_rain,
            rainy_days=r_days,
            max_wind_kmh=pk_wind,
            source="Open-Meteo ERA5 Reanalysis (2000-Presente)",
            updated_at=now_utc,
        )
        db.session.add(summary)
        monthly_records_count += 1

        if len(vals["rain"]) >= 25:
            c_acc = climatology_accum[month]
            if avg_t is not None:
                c_acc["avg_temps"].append(avg_t)
            if max_t is not None:
                c_acc["max_temps"].append(max_t)
            if min_t is not None:
                c_acc["min_temps"].append(min_t)
            c_acc["total_rains"].append(tot_rain)
            c_acc["rainy_days"].append(r_days)
            if pk_wind is not None:
                c_acc["max_winds"].append(pk_wind)

    normals_count = 0
    for month in range(1, 13):
        c_acc = climatology_accum[month]
        if not c_acc["total_rains"]:
            continue

        norm_summary = MonthlyClimateSummary(
            region_id=region.id,
            year=0,
            month=month,
            avg_temp_c=round(sum(c_acc["avg_temps"]) / len(c_acc["avg_temps"]), 1) if c_acc["avg_temps"] else None,
            max_temp_c=round(sum(c_acc["max_temps"]) / len(c_acc["max_temps"]), 1) if c_acc["max_temps"] else None,
            min_temp_c=round(sum(c_acc["min_temps"]) / len(c_acc["min_temps"]), 1) if c_acc["min_temps"] else None,
            total_rain_mm=round(sum(c_acc["total_rains"]) / len(c_acc["total_rains"]), 1),
            rainy_days=int(round(sum(c_acc["rainy_days"]) / len(c_acc["rainy_days"]))),
            max_wind_kmh=round(sum(c_acc["max_winds"]) / len(c_acc["max_winds"]), 1) if c_acc["max_winds"] else None,
            source="Normal Climatológica CLIMAE (Média ERA5 2000-2026)",
            updated_at=now_utc,
        )
        db.session.add(norm_summary)
        normals_count += 1

    db.session.commit()
    return {"months_saved": monthly_records_count, "normals_saved": normals_count}


def run_seed_monthly_climate(force_reseed: bool = False):
    app = create_app()
    with app.app_context():
        db.create_all()
        regions = Region.query.all()
        end_date = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")

        print("=" * 72)
        print(f"📊 CLIMAE — CARGA HISTÓRICA MENSAL & CLIMATOLOGIA ({START_DATE} a {end_date})")
        print("=" * 72)

        for idx, region in enumerate(regions, start=1):
            existing_count = MonthlyClimateSummary.query.filter_by(region_id=region.id).count()
            if existing_count >= 300 and not force_reseed:
                print(f"[{idx}/{len(regions)}] ⏭️  {region.municipality} ({region.name}) já possui {existing_count} registros no banco. Pulando...")
                continue

            print(f"[{idx}/{len(regions)}] Baixando série histórica ERA5: {region.municipality} ({region.name})...")
            daily = fetch_daily_archive(region.latitude, region.longitude, START_DATE, end_date)
            if not daily:
                print(f"  ❌ Falha ao obter dados para {region.municipality}")
                continue

            res = aggregate_and_save_region(region, daily)
            print(f"  ✅ Salvos {res['months_saved']} meses históricos + {res['normals_saved']} médias climatológicas.")

            # Pausa preventiva de 8 segundos entre regiões pesadas (26 anos de dados cada)
            time.sleep(8.0)

        total_in_db = MonthlyClimateSummary.query.count()
        print("-" * 72)
        print(f"🎉 Concluído! Total acumulado no banco: {total_in_db} registros de climatologia e histórico mensal.")
        print("=" * 72)


if __name__ == "__main__":
    force = "--force" in sys.argv
    run_seed_monthly_climate(force_reseed=force)
