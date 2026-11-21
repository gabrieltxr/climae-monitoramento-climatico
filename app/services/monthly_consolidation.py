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

# A reanálise ERA5 é publicada com ~5 dias de atraso. Antes disso, os últimos dias
# do mês fechado vêm como null na API e o mês seria consolidado incompleto.
ERA5_LAG_DAYS = 6

# Quantos meses fechados são revisados a cada verificação. Uma região que ainda estava
# pendente quando o mês virou continua sendo tentada nos meses seguintes, em vez de
# ficar sem aquele mês no histórico para sempre.
CATCH_UP_MONTHS = 3


def get_closed_month(reference_date: datetime = None) -> tuple[int, int]:
    """Retorna o ano e mês do último mês completamente encerrado."""
    if reference_date is None:
        reference_date = datetime.now(timezone.utc)
    first_of_current = datetime(reference_date.year, reference_date.month, 1, tzinfo=timezone.utc)
    last_of_previous = first_of_current - timedelta(days=1)
    return last_of_previous.year, last_of_previous.month


def get_recent_closed_months(reference_date: datetime = None, count: int = CATCH_UP_MONTHS) -> list[tuple[int, int]]:
    """Retorna os `count` últimos meses fechados, do mais antigo para o mais recente."""
    year, month = get_closed_month(reference_date)
    months = []
    for _ in range(count):
        months.append((year, month))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return list(reversed(months))


def is_era5_available(year: int, month: int, reference_date: datetime = None) -> bool:
    """O mês só pode ser consolidado depois de ERA5_LAG_DAYS dias após o seu último dia."""
    if reference_date is None:
        reference_date = datetime.now(timezone.utc)
    _, last_day = calendar.monthrange(year, month)
    last_date = datetime(year, month, last_day, tzinfo=timezone.utc).date()
    return (reference_date.date() - last_date).days >= ERA5_LAG_DAYS


def is_daily_series_complete(daily: dict, expected_days: int) -> bool:
    """Exige todos os dias do mês com chuva e temperatura média preenchidas (sem null)."""
    for key in ("precipitation_sum", "temperature_2m_mean"):
        values = daily.get(key) or []
        if len(values) < expected_days or any(v is None for v in values[:expected_days]):
            return False
    return True


def mean_of_complete_days(values: list, expected_days: int) -> float | None:
    """Média dos valores diários do mês, só se todos os `expected_days` dias vierem
    preenchidos (sem null); com falha, None — média de mês parcial puxa para os dias que vieram."""
    if values is None or len(values) < expected_days:
        return None
    days = values[:expected_days]
    if any(v is None for v in days):
        return None
    return round(sum(float(v) for v in days) / expected_days, 1)


def _mean_or_none(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def recalculate_daily_mean_normals(region_id: int, now_utc: datetime = None, months=range(1, 13)) -> int:
    """
    Atualiza na normal (year = 0) só a média das máximas e das mínimas diárias, a partir
    dos anos que já têm o valor. As demais colunas da normal não são tocadas. Sem normal
    gravada para o mês, não cria uma. Retorna quantas normais foram atualizadas.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    updated = 0
    for month in months:
        normal = MonthlyClimateSummary.query.filter_by(region_id=region_id, year=0, month=month).first()
        if not normal:
            continue
        years = MonthlyClimateSummary.query.filter(
            MonthlyClimateSummary.region_id == region_id,
            MonthlyClimateSummary.month == month,
            MonthlyClimateSummary.year > 0,
        ).all()
        normal.mean_max_temp_c = _mean_or_none([y.mean_max_temp_c for y in years])
        normal.mean_min_temp_c = _mean_or_none([y.mean_min_temp_c for y in years])
        normal.updated_at = now_utc
        updated += 1
    return updated


def is_month_consolidated(year: int, month: int) -> bool:
    """Verifica se o mês/ano especificado já está consolidado nas regiões cadastradas."""
    total_regions = Region.query.count()
    if total_regions == 0:
        return True

    existing_count = MonthlyClimateSummary.query.filter_by(year=year, month=month).count()
    # Exige todas as regiões: com o limiar antigo de 90%, regiões com série ERA5 incompleta
    # (puladas na consolidação) nunca seriam reprocessadas. A verificação é diária, então
    # uma região pendente custa no máximo uma requisição por dia até completar.
    return existing_count >= total_regions


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
    normal.mean_max_temp_c = _mean_or_none([y.mean_max_temp_c for y in all_years])
    normal.mean_min_temp_c = _mean_or_none([y.mean_min_temp_c for y in all_years])
    normal.updated_at = now_utc


def consolidate_closed_month(force: bool = False, reference_date: datetime = None) -> dict:
    """
    Consolida o último mês encerrado e revisa os CATCH_UP_MONTHS - 1 meses anteriores,
    preenchendo só as regiões que ainda não têm registro neles (sem lacunas, não há
    nenhuma requisição extra). `force` vale apenas para o último mês encerrado.

    Retorna o status do último mês encerrado; `updated_regions` e `incomplete_regions`
    somam todos os meses revisados, e `months` traz o resultado de cada um.
    """
    months = get_recent_closed_months(reference_date)
    results = [
        consolidate_month(year, month, force=force and (year, month) == months[-1], reference_date=reference_date)
        for year, month in months
    ]
    latest = results[-1]
    return {
        "status": latest["status"],
        "year": latest["year"],
        "month": latest["month"],
        "updated_regions": sum(r["updated_regions"] for r in results),
        "incomplete_regions": sum(r["incomplete_regions"] for r in results),
        "months": results,
    }


def consolidate_month(target_year: int, target_month: int, force: bool = False, reference_date: datetime = None) -> dict:
    """
    Consolida um mês fechado específico caso ainda não esteja no banco.
    Só grava regiões cuja série diária do mês está completa no ERA5.
    Retorna um dicionário com o status e a quantidade de regiões consolidadas.
    """
    if not is_era5_available(target_year, target_month, reference_date):
        logger.info(
            "[CONSOLIDATOR] ERA5 de %02d/%d ainda não publicado por completo (atraso de ~%d dias). Aguardando.",
            target_month, target_year, ERA5_LAG_DAYS,
        )
        return {
            "status": "awaiting_era5",
            "year": target_year,
            "month": target_month,
            "updated_regions": 0,
            "incomplete_regions": 0,
        }

    if is_month_consolidated(target_year, target_month) and not force:
        logger.info("[CONSOLIDATOR] Mês %02d/%d já consolidado no histórico. Nenhuma ação necessária.", target_month, target_year)
        return {
            "status": "already_consolidated",
            "year": target_year,
            "month": target_month,
            "updated_regions": 0,
            "incomplete_regions": 0,
        }

    logger.info("[CONSOLIDATOR] Iniciando consolidação incremental para o mês fechado: %02d/%d...", target_month, target_year)
    
    # Define o primeiro e último dia do mês fechado
    start_date = f"{target_year}-{target_month:02d}-01"
    _, last_day = calendar.monthrange(target_year, target_month)
    end_date = f"{target_year}-{target_month:02d}-{last_day:02d}"

    regions = Region.query.order_by(Region.municipality, Region.name).all()
    now_utc = datetime.now(timezone.utc)
    updated_count = 0
    incomplete_count = 0

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

        # Série com dias faltando (null) não é gravada: a região fica pendente e é
        # reprocessada na próxima verificação, em vez de ficar consolidada incompleta.
        if not is_daily_series_complete(daily, last_day):
            incomplete_count += 1
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
        summary.mean_max_temp_c = mean_of_complete_days(daily.get("temperature_2m_max"), last_day)
        summary.mean_min_temp_c = mean_of_complete_days(daily.get("temperature_2m_min"), last_day)
        summary.source = f"Open-Meteo ERA5 Reanalysis ({target_month:02d}/{target_year})"
        summary.updated_at = now_utc

        # Recalcula a Normal Climatológica daquele mês
        recalculate_normal_for_month(r.id, target_month, now_utc)
        updated_count += 1

    if updated_count > 0:
        db.session.commit()
        logger.info("[CONSOLIDATOR] Sucesso! Mês %02d/%d consolidado para %d regiões.", target_month, target_year, updated_count)
    if incomplete_count > 0:
        logger.warning(
            "[CONSOLIDATOR] %d regiões com série ERA5 incompleta em %02d/%d; serão reprocessadas na próxima verificação.",
            incomplete_count, target_month, target_year,
        )

    return {
        "status": "success",
        "year": target_year,
        "month": target_month,
        "updated_regions": updated_count,
        "incomplete_regions": incomplete_count,
    }
