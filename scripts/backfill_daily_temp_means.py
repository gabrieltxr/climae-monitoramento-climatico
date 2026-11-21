"""
Preenche a média das máximas e das mínimas diárias (mean_max_temp_c / mean_min_temp_c)
nos meses do histórico ERA5 já gravados, e recalcula essas duas colunas nas normais.

Só grava as duas colunas novas, e só onde estão vazias: chuva, média, extremos absolutos
e vento ficam como estão (o ERA5 é revisado com o tempo; baixar tudo de novo mudaria o
histórico sem aviso). Um mês só recebe o valor com todos os dias preenchidos no ERA5.

Retomável: cada execução pega só as regiões que ainda têm meses sem o valor e grava
região por região. Se a API recusar por limite diário/horário, para e avisa — é só rodar
de novo depois, que continua de onde parou.

Uso:
  python scripts/backfill_daily_temp_means.py            # simula: conta o que falta, não baixa nada
  python scripts/backfill_daily_temp_means.py --apply    # cópia do banco + preenchimento
    --limit N      no máximo N regiões nesta execução
    --pause S      segundos entre regiões (padrão 5)
    --skip-backup  sem cópia automática (só para bancos não-SQLite, com backup manual feito)

Com --apply, antes de gravar copia o banco SQLite para
instance/climae_zonasul.backup-AAAAMMDD-HHMMSS.db; se a cópia falhar, nada é alterado.
"""
import argparse
import calendar
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import inspect, or_

from app import create_app
from app.db_schema import ensure_schema
from app.models import db, Region, MonthlyClimateSummary
from app.services.monthly_consolidation import (
    ARCHIVE_URL, mean_of_complete_days, recalculate_daily_mean_normals,
)
from scripts.clear_legacy_wind_direction import backup_sqlite_database

DAILY_VARIABLES = "temperature_2m_max,temperature_2m_min"
NEW_COLUMNS = ("mean_max_temp_c", "mean_min_temp_c")
MINUTE_LIMIT_WAIT_SEC = 65
MAX_ATTEMPTS = 5


class ApiLimitReached(Exception):
    """Limite diário/horário do Open-Meteo: não adianta insistir agora."""


def fetch_daily_temps(lat: float, lon: float, start_date: str, end_date: str) -> dict | None:
    """Série diária de máximas e mínimas (ERA5, UTC, como a carga original)."""
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": start_date, "end_date": end_date,
        "daily": DAILY_VARIABLES, "timezone": "UTC",
    }
    headers = {"User-Agent": "CLIMAE-Backfill/1.0"}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            res = requests.get(ARCHIVE_URL, params=params, headers=headers, timeout=60)
        except requests.RequestException as exc:
            print(f"    [Tentativa {attempt}/{MAX_ATTEMPTS}] Falha de rede: {exc}", flush=True)
            time.sleep(10 * attempt)
            continue
        if res.status_code == 429:
            reason = ""
            try:
                reason = str(res.json().get("reason", ""))
            except ValueError:
                pass
            if "minut" not in reason.lower():
                raise ApiLimitReached(reason or "HTTP 429")
            print(f"    [AVISO] Limite por minuto. Aguardando {MINUTE_LIMIT_WAIT_SEC}s...", flush=True)
            time.sleep(MINUTE_LIMIT_WAIT_SEC)
            continue
        if res.status_code != 200:
            print(f"    [Tentativa {attempt}/{MAX_ATTEMPTS}] HTTP {res.status_code}: {res.text[:120]}", flush=True)
            time.sleep(10 * attempt)
            continue
        daily = res.json().get("daily")
        if daily and "time" in daily:
            return daily
        return None
    return None


def schema_has_new_columns() -> bool:
    cols = {c["name"] for c in inspect(db.engine).get_columns("monthly_climate_summaries")}
    return all(c in cols for c in NEW_COLUMNS)


def _pending_filter():
    return (
        MonthlyClimateSummary.year > 0,
        or_(MonthlyClimateSummary.mean_max_temp_c.is_(None), MonthlyClimateSummary.mean_min_temp_c.is_(None)),
    )


def pending_by_region() -> dict[int, list[MonthlyClimateSummary]]:
    rows = MonthlyClimateSummary.query.filter(*_pending_filter()).all()
    out = defaultdict(list)
    for row in rows:
        out[row.region_id].append(row)
    return dict(out)


def fill_region(region: Region, rows: list[MonthlyClimateSummary], daily: dict) -> tuple[int, int]:
    """Grava as duas colunas nos meses pendentes da região. Retorna (preenchidos, sem dado completo)."""
    by_month = defaultdict(lambda: {"max": [], "min": []})
    dates = daily.get("time", [])
    t_max = daily.get("temperature_2m_max", [])
    t_min = daily.get("temperature_2m_min", [])
    for i, d in enumerate(dates):
        bucket = by_month[(int(d[0:4]), int(d[5:7]))]
        bucket["max"].append(t_max[i] if i < len(t_max) else None)
        bucket["min"].append(t_min[i] if i < len(t_min) else None)

    filled = missing = 0
    for row in rows:
        days = calendar.monthrange(row.year, row.month)[1]
        bucket = by_month.get((row.year, row.month), {"max": [], "min": []})
        if row.mean_max_temp_c is None:
            row.mean_max_temp_c = mean_of_complete_days(bucket["max"], days)
        if row.mean_min_temp_c is None:
            row.mean_min_temp_c = mean_of_complete_days(bucket["min"], days)
        if row.mean_max_temp_c is not None and row.mean_min_temp_c is not None:
            filled += 1
        else:
            missing += 1
    recalculate_daily_mean_normals(region.id)
    db.session.commit()
    return filled, missing


def _range_for(rows) -> tuple[str, str]:
    first = min((r.year, r.month) for r in rows)
    last = max((r.year, r.month) for r in rows)
    end_day = calendar.monthrange(*last)[1]
    return f"{first[0]}-{first[1]:02d}-01", f"{last[0]}-{last[1]:02d}-{end_day:02d}"


def run_backfill(limit: int | None = None, pause_sec: float = 5.0) -> dict:
    pending = pending_by_region()
    regions = Region.query.filter(Region.id.in_(pending)).order_by(Region.municipality, Region.name).all()
    if limit is not None:
        regions = regions[:limit]
    summary = {"regions": 0, "filled": 0, "missing": 0, "stopped": None}
    for idx, region in enumerate(regions, start=1):
        rows = pending[region.id]
        start, end = _range_for(rows)
        print(f"[{idx}/{len(regions)}] {region.municipality} ({region.name}): "
              f"{len(rows)} meses, {start} a {end}...", flush=True)
        try:
            daily = fetch_daily_temps(region.latitude, region.longitude, start, end)
        except ApiLimitReached as exc:
            summary["stopped"] = f"limite da API ({exc})"
            print(f"  [PARADO] Limite da API: {exc}. Rode de novo mais tarde; continua daqui.", flush=True)
            break
        if not daily:
            summary["stopped"] = "falha ao baixar"
            print("  [PARADO] Não foi possível baixar a série. Rode de novo mais tarde; continua daqui.", flush=True)
            break
        filled, missing = fill_region(region, rows, daily)
        summary["regions"] += 1
        summary["filled"] += filled
        summary["missing"] += missing
        print(f"  [OK] {filled} meses preenchidos" + (f", {missing} sem série completa no ERA5" if missing else ""), flush=True)
        if idx < len(regions) and pause_sec:
            time.sleep(pause_sec)
    return summary


def main(argv=None, app=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="baixa e grava (sem isso, só conta o que falta)")
    parser.add_argument("--limit", type=int, default=None, help="no máximo N regiões nesta execução")
    parser.add_argument("--pause", type=float, default=5.0, help="segundos entre regiões (padrão 5)")
    parser.add_argument("--skip-backup", action="store_true",
                        help="não faz a cópia automática (só para bancos não-SQLite, com backup manual feito)")
    args = parser.parse_args(argv)

    app = app or create_app()
    with app.app_context():
        print(f"Banco: {app.config['SQLALCHEMY_DATABASE_URI']}")
        if not args.apply:
            if not schema_has_new_columns():
                print("[SIMULAÇÃO] O banco ainda não tem a migration 0005. Ela é aplicada pelo --apply "
                      "(depois da cópia) ou no próximo start do servidor. Nada foi alterado.")
                return 0
            pending = pending_by_region()
            months = sum(len(v) for v in pending.values())
            print(f"[SIMULAÇÃO] {months} meses em {len(pending)} regiões sem a média das máximas/mínimas. "
                  "Use --apply para baixar e gravar.")
            return 0

        if not args.skip_backup:
            url = db.engine.url
            if url.get_backend_name() != "sqlite":
                print("[ERRO] Cópia automática só funciona com SQLite. Faça o backup manualmente "
                      "e rode de novo com --skip-backup. Nada foi alterado.")
                return 1
            try:
                backup_path = backup_sqlite_database(url.database)
            except Exception as exc:
                print(f"[ERRO] Falha ao copiar o banco: {exc}. Nada foi alterado.")
                return 1
            print(f"Cópia de segurança: {backup_path}")

        ensure_schema()
        result = run_backfill(limit=args.limit, pause_sec=args.pause)
        left = MonthlyClimateSummary.query.filter(*_pending_filter()).count()
        print("-" * 72)
        print(f"{result['regions']} regiões processadas, {result['filled']} meses preenchidos; "
              f"{left} meses ainda sem o valor.")
        if result["stopped"]:
            print(f"Interrompido: {result['stopped']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
