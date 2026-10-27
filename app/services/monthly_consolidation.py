"""
CLIMAE - Serviço de Consolidação Mensal Incremental (Histórico ERA5)
-------------------------------------------------------------------
Executado mensalmente na virada do mês ou na inicialização do servidor.
Verifica se o último mês recém-encerrado já foi consolidado no banco (MonthlyClimateSummary).
Caso ainda não esteja, busca apenas a série diária desse mês específico (30-31 dias),
calcula os indicadores mensais, atualiza a Normal Climatológica correspondente
e grava os registros no banco para todas as regiões sem sobrecarga de rede.
"""

import calendar
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import requests

from app.models import db, Region, MonthlyClimateSummary

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def get_closed_month(reference_date: datetime = None) -> tuple[int, int]:
    """Retorna o ano e mês do último mês completamente encerrado."""
    if reference_date is None:
        reference_date = datetime.now(timezone.utc)
    first_of_current = datetime(reference_date.year, reference_date.month, 1, tzinfo=timezone.utc)
    last_of_previous = first_of_current - timedelta(days=1)
    return last_of_previous.year, last_of_previous.month


def is_month_consolidated(year: int, month: int) -> bool:
    """Verifica se o mês/ano especificado já está consolidado nas regiões cadastradas."""
    total_regions = Region.query.count()
    if total_regions == 0:
        return True

    existing_count = MonthlyClimateSummary.query.filter_by(year=year, month=month).count()
    # Considera consolidado se a vasta maioria das regiões já possui registro para aquele mês/ano
    return existing_count >= int(total_regions * 0.9)


def fetch_month_daily(lat: float, lon: float, start_date: str, end_date: str) -> dict | None:
    """Busca a série diária de um único mês fechado (30-31 dias) na API ERA5."""
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
    headers = {"User-Agent": "CLIMAE-MonthlyConsolidator/1.0"}

    try:
        res = requests.get(ARCHIVE_URL, params=params, headers=headers, timeout=25)
        if res.status_code == 200:
            data = res.json()
            return data.get("daily")
        logger.warning("[CONSOLIDATOR] Resposta inesperada ERA5 (%s): %s", res.status_code, res.text[:120])
    except Exception as exc:
        logger.error("[CONSOLIDATOR] Falha na requisição ERA5 para mês fechado: %s", exc)
    return None


def recalculate_normal_for_month(region_id: int, month: int, now_utc: datetime):
    """Recalcula a Normal Climatológica (year = 0) do mês específico incluindo os novos dados."""
    all_years = MonthlyClimateSummary.query.filter(
        MonthlyClimateSummary.region_id == region_id,
        MonthlyClimateSummary.month == month,
        MonthlyClimateSummary.year > 0
    ).all()

    if not all_years:
        return

    valid_temps = [y.avg_temp_c for y in all_years if y.avg_temp_c is not None]
    max_temps = [y.max_temp_c for y in all_years if y.max_temp_c is not None]
    min_temps = [y.min_temp_c for y in all_years if y.min_temp_c is not None]
    rains = [y.total_rain_mm for y in all_years if y.total_rain_mm is not None]
    rainy_days = [y.rainy_days for y in all_years if y.rainy_days is not None]
    winds = [y.max_wind_kmh for y in all_years if y.max_wind_kmh is not None]

    normal = MonthlyClimateSummary.query.filter_by(region_id=region_id, year=0, month=month).first()
    if not normal:
        normal = MonthlyClimateSummary(
            region_id=region_id,
            year=0,
            month=month,
            source="Normal Climatológica CLIMAE (Atualização Incremental)",
            updated_at=now_utc
        )
        db.session.add(normal)

    normal.avg_temp_c = round(sum(valid_temps) / len(valid_temps), 1) if valid_temps else None
    normal.max_temp_c = round(sum(max_temps) / len(max_temps), 1) if max_temps else None
    normal.min_temp_c = round(sum(min_temps) / len(min_temps), 1) if min_temps else None
    normal.total_rain_mm = round(sum(rains) / len(rains), 1) if rains else 0.0
    normal.rainy_days = int(round(sum(rainy_days) / len(rainy_days))) if rainy_days else 0
    normal.max_wind_kmh = round(sum(winds) / len(winds), 1) if winds else None
    normal.updated_at = now_utc


def consolidate_closed_month(force: bool = False) -> dict:
    """
    Executa a consolidação do mês encerrado caso ainda não esteja no banco.
    Retorna um dicionário com o status e a quantidade de regiões consolidadas.
    """
    target_year, target_month = get_closed_month()

    if is_month_consolidated(target_year, target_month) and not force:
        logger.info("[CONSOLIDATOR] Mês %02d/%d já consolidado no histórico. Nenhuma ação necessária.", target_month, target_year)
        return {
            "status": "already_consolidated",
            "year": target_year,
            "month": target_month,
            "updated_regions": 0
        }

    logger.info("[CONSOLIDATOR] Iniciando consolidação incremental para o mês fechado: %02d/%d...", target_month, target_year)
    
    # Define o primeiro e último dia do mês fechado
    start_date = f"{target_year}-{target_month:02d}-01"
    _, last_day = calendar.monthrange(target_year, target_month)
    end_date = f"{target_year}-{target_month:02d}-{last_day:02d}"

    regions = Region.query.order_by(Region.municipality, Region.name).all()
    now_utc = datetime.now(timezone.utc)
    updated_count = 0

    # Agrupa por coordenadas arredondadas (células de grade ERA5) para evitar requisições repetidas
    grid_cache = {}

    for r in regions:
        # Verifica se essa região já possui esse mês específico gravado
        has_record = MonthlyClimateSummary.query.filter_by(
            region_id=r.id, year=target_year, month=target_month
        ).first()

        if has_record and not force:
            continue

        grid_key = (round(r.latitude, 2), round(r.longitude, 2))
        daily = grid_cache.get(grid_key)

        if daily is None:
            daily = fetch_month_daily(r.latitude, r.longitude, start_date, end_date)
            if daily:
                grid_cache[grid_key] = daily

        if not daily:
            continue

        # Agrega os 30-31 dias do mês
        t_means = [float(v) for v in daily.get("temperature_2m_mean", []) if v is not None]
        t_maxs = [float(v) for v in daily.get("temperature_2m_max", []) if v is not None]
        t_mins = [float(v) for v in daily.get("temperature_2m_min", []) if v is not None]
        precips = [float(v) for v in daily.get("precipitation_sum", []) if v is not None]
        winds = [float(v) for v in daily.get("wind_speed_10m_max", []) if v is not None]

        avg_t = round(sum(t_means) / len(t_means), 1) if t_means else None
        max_t = round(max(t_maxs), 1) if t_maxs else None
        min_t = round(min(t_mins), 1) if t_mins else None
        tot_rain = round(sum(precips), 1) if precips else 0.0
        r_days = sum(1 for p in precips if p >= 1.0)
        pk_wind = round(max(winds), 1) if winds else None

        if has_record:
            summary = has_record
        else:
            summary = MonthlyClimateSummary(region_id=r.id, year=target_year, month=target_month)
            db.session.add(summary)

        summary.avg_temp_c = avg_t
        summary.max_temp_c = max_t
        summary.min_temp_c = min_t
        summary.total_rain_mm = tot_rain
        summary.rainy_days = r_days
        summary.max_wind_kmh = pk_wind
        summary.source = f"Open-Meteo ERA5 Reanalysis ({target_month:02d}/{target_year})"
        summary.updated_at = now_utc

        # Recalcula a Normal Climatológica daquele mês
        recalculate_normal_for_month(r.id, target_month, now_utc)
        updated_count += 1

    if updated_count > 0:
        db.session.commit()
        logger.info("[CONSOLIDATOR] Sucesso! Mês %02d/%d consolidado para %d regiões.", target_month, target_year, updated_count)

    return {
        "status": "success",
        "year": target_year,
        "month": target_month,
        "updated_regions": updated_count
    }
