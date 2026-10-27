import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.models import db, Region, MonthlyClimateSummary

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
START_DATE = "2000-01-01"


def fetch_daily_archive(lat: float, lon: float, start_date: str, end_date: str) -> dict | None:
    """
    Busca a serie diaria historica da reanalise ERA5 com tratamento inteligente
    para o limite de requisicoes por minuto (HTTP 429) da Open-Meteo.
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

    for attempt in range(1, 6):
        try:
            res = requests.get(ARCHIVE_URL, params=params, headers=headers, timeout=45)
            if res.status_code == 429:
                wait_sec = 65
                print(f"    [AVISO] Limite por minuto da API atingido (HTTP 429). Aguardando {wait_sec}s...", flush=True)
                time.sleep(wait_sec)
                continue

            res.raise_for_status()
            data = res.json()
            if "daily" in data and "time" in data["daily"]:
                return data["daily"]
        except Exception as exc:
            wait_sec = 10 * attempt
            print(f"    [Tentativa {attempt}/5] Aviso na requisicao ERA5: {exc}. Aguardando {wait_sec}s...", flush=True)
            time.sleep(wait_sec)
    return None


def aggregate_and_save_region(region: Region, daily_data: dict) -> dict:
    """
    Agrupa os dados diarios em resumos mensais (year, month) e calcula
    a Normal Climatologica (year = 0, month = 1..12) para a regiao.
    Evita duplicacoes limpando previamente os registros historicos da regiao.
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

    # Protecao estrita contra duplicacoes: remove qualquer registro anterior desta regiao
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
            source="Normal Climatologica CLIMAE (Media ERA5 2000-2026)",
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
        regions = Region.query.order_by(Region.municipality, Region.name).all()
        end_date = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")

        missing_regions = [r for r in regions if MonthlyClimateSummary.query.filter_by(region_id=r.id).count() < 300]
        
        print("=" * 76, flush=True)
        print(f"CLIMAE -- CARGA HISTORICA MENSAL & CLIMATOLOGIA ({START_DATE} a {end_date})", flush=True)
        print(f"Total de regioes cadastradas: {len(regions)}", flush=True)
        print(f"Regioes ja com historico: {len(regions) - len(missing_regions)}", flush=True)
        print(f"Regioes a processar agora: {len(missing_regions) if not force_reseed else len(regions)}", flush=True)
        print("=" * 76, flush=True)

        processed = 0
        for idx, region in enumerate(regions, start=1):
            existing_count = MonthlyClimateSummary.query.filter_by(region_id=region.id).count()
            if existing_count >= 300 and not force_reseed:
                print(f"[{idx}/{len(regions)}] [PULANDO] {region.municipality} ({region.name}) ja possui {existing_count} registros.", flush=True)
                continue

            processed += 1
            print(f"[{idx}/{len(regions)}] [BAIXANDO] ERA5: {region.municipality} ({region.name}) [{region.latitude}, {region.longitude}]...", flush=True)
            daily = fetch_daily_archive(region.latitude, region.longitude, START_DATE, end_date)
            if not daily:
                print(f"  [ERRO] Falha ao obter dados para {region.municipality} ({region.name})", flush=True)
                continue

            res = aggregate_and_save_region(region, daily)
            print(f"  [OK] Salvos {res['months_saved']} meses historicos + {res['normals_saved']} medias climatologicas.", flush=True)

            # Pausa preventiva de 5 segundos entre requisicoes para respeitar os limites de taxa da ERA5 Open-Meteo
            time.sleep(5.0)

        total_in_db = MonthlyClimateSummary.query.count()
        print("-" * 76, flush=True)
        print(f"Concluido com sucesso! {processed} regioes foram atualizadas.", flush=True)
        print(f"Total acumulado no banco: {total_in_db} registros de historico mensal e climatologia.", flush=True)
        print("=" * 76, flush=True)


if __name__ == "__main__":
    force = "--force" in sys.argv
    run_seed_monthly_climate(force_reseed=force)
