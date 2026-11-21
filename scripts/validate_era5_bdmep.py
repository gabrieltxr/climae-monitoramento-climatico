"""
Valida o histórico mensal ERA5 do CLIMAE (tabela monthly_climate_summaries) contra as
medições das estações automáticas do INMET (BDMEP, dados históricos horários).

Para cada ano pedido o script baixa portal.inmet.gov.br/uploads/dadoshistoricos/{ano}.zip
(~90–100 MB; sem token), extrai só as estações da Zona Sul (STATIONS) e descarta o zip
(a não ser com --cache). Nada é gravado no banco: a saída é um relatório em Markdown.

Regras:
- Hora UTC do INMET convertida para o horário de Brasília (UTC-3) antes de agrupar por dia.
- Um mês da estação só entra numa variável se tiver >= MIN_COMPLETENESS das horas válidas.
- Chuva: mês completo com total < SUSPECT_RAIN_RATIO do ERA5 (e ERA5 >= SUSPECT_RAIN_MIN_MM)
  é marcado como "pluviômetro suspeito" e fica fora das estatísticas (aparece listado).
- Cada estação é comparada com a região mais próxima do banco (até MAX_PAIR_KM).
- Diferença = ERA5 − estação.

Uso:
    python scripts/validate_era5_bdmep.py                       # 2008 até o ano passado
    python scripts/validate_era5_bdmep.py --years 2020-2025 --out relatorio.md
    python scripts/validate_era5_bdmep.py --years 2025 --cache PASTA   # guarda/reusa os zips
    python scripts/validate_era5_bdmep.py --db caminho/para/copia.db
"""
import argparse
import io
import math
import os
import re
import sqlite3
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta

import requests

ZIP_URL = "https://portal.inmet.gov.br/uploads/dadoshistoricos/{year}.zip"
USER_AGENT = "Mozilla/5.0 (compatible; CLIMAE/1.0)"
DEFAULT_DB = os.path.join(os.path.dirname(__file__), "..", "instance", "climae_zonasul.db")

# Estações automáticas da Zona Sul (código WMO -> nome).
STATIONS = {
    "A802": "Rio Grande",
    "A811": "Canguçu",
    "A836": "Jaguarão",
    "A887": "Capão do Leão (Pelotas)",
    "A899": "Santa Vitória do Palmar (Barra do Chuí)",
    "B826": "Herval",
    "B829": "Santa Vitória do Palmar (Taim)",
    "B830": "Santa Vitória do Palmar (Centro)",
}
MIN_COMPLETENESS = 0.9
SUSPECT_RAIN_RATIO = 0.2
SUSPECT_RAIN_MIN_MM = 40.0
MAX_PAIR_KM = 10.0
LOCAL_OFFSET = timedelta(hours=-3)

# Colunas do CSV do BDMEP (0 = Data, 1 = Hora UTC).
COL_RAIN, COL_TEMP, COL_TMAX, COL_TMIN = 2, 7, 9, 10

MONTHS_PT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def haversine_km(lat1, lon1, lat2, lon2):
    r = math.radians
    a = math.sin(r(lat2 - lat1) / 2) ** 2 + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def _num(value: str):
    value = value.strip().replace(",", ".")
    if not value or value == "-9999":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_station_csv(text: str) -> dict:
    """{code, name, latitude, longitude, hours: [(datetime local, rain, temp, tmax, tmin)]}."""
    lines = text.splitlines()
    meta = {}
    start = 0
    for i, line in enumerate(lines):
        parts = line.split(";")
        if parts and parts[0].strip().lower().startswith("data"):
            start = i + 1
            break
        if len(parts) >= 2:
            meta[parts[0].strip().rstrip(":").upper()] = parts[1].strip()
    hours = []
    for line in lines[start:]:
        p = line.split(";")
        if len(p) <= COL_TMIN or not p[0].strip():
            continue
        stamp = p[0].strip().replace("-", "/") + " " + re.sub(r"\D", "", p[1])[:4].zfill(4)
        try:
            when = datetime.strptime(stamp, "%Y/%m/%d %H%M") + LOCAL_OFFSET
        except ValueError:
            continue
        hours.append((when, _num(p[COL_RAIN]), _num(p[COL_TEMP]), _num(p[COL_TMAX]), _num(p[COL_TMIN])))
    return {
        "code": meta.get("CODIGO (WMO)", ""),
        "latitude": _num(meta.get("LATITUDE", "")),
        "longitude": _num(meta.get("LONGITUDE", "")),
        "hours": hours,
    }


def _hours_in_month(year: int, month: int) -> int:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - date(year, month, 1)).days * 24


def monthly_summary(hours) -> dict:
    """{(ano, mês): {...}}; variável fica None quando o mês não atinge MIN_COMPLETENESS."""
    by_month = defaultdict(lambda: {"rain": defaultdict(list), "temp": [], "tmax": [], "tmin": []})
    for when, rain, temp, tmax, tmin in hours:
        bucket = by_month[(when.year, when.month)]
        if rain is not None:
            bucket["rain"][when.date()].append(rain)
        if temp is not None:
            bucket["temp"].append(temp)
        if tmax is not None:
            bucket["tmax"].append(tmax)
        if tmin is not None:
            bucket["tmin"].append(tmin)
    result = {}
    for (year, month), b in by_month.items():
        expected = _hours_in_month(year, month)
        rain_hours = sum(len(v) for v in b["rain"].values())
        rain_ok = rain_hours >= MIN_COMPLETENESS * expected
        temp_ok = len(b["temp"]) >= MIN_COMPLETENESS * expected
        daily = [sum(v) for v in b["rain"].values()]
        result[(year, month)] = {
            "rain_completeness": rain_hours / expected,
            "temp_completeness": len(b["temp"]) / expected,
            "total_rain_mm": round(sum(daily), 1) if rain_ok else None,
            "rainy_days": sum(1 for v in daily if v >= 1.0) if rain_ok else None,
            "avg_temp_c": round(sum(b["temp"]) / len(b["temp"]), 2) if temp_ok else None,
            "max_temp_c": max(b["tmax"]) if temp_ok and b["tmax"] else None,
            "min_temp_c": min(b["tmin"]) if temp_ok and b["tmin"] else None,
        }
    return result


def is_suspect_rain(station_mm, era5_mm) -> bool:
    """Mês completo com chuva muito abaixo do ERA5: provável pluviômetro entupido/parado."""
    if station_mm is None or era5_mm is None or era5_mm < SUSPECT_RAIN_MIN_MM:
        return False
    return station_mm < SUSPECT_RAIN_RATIO * era5_mm


def nearest_region(regions, lat, lon):
    if lat is None or lon is None or not regions:
        return None
    dist, region = min(((haversine_km(lat, lon, r["latitude"], r["longitude"]), r) for r in regions), key=lambda t: t[0])
    return (region, dist) if dist <= MAX_PAIR_KM else None


VARIABLES = [
    ("avg_temp_c", "Temperatura média (°C)"),
    ("max_temp_c", "Máxima absoluta (°C)"),
    ("min_temp_c", "Mínima absoluta (°C)"),
    ("total_rain_mm", "Chuva do mês (mm)"),
    ("rainy_days", "Dias com chuva ≥ 1 mm"),
]


def compare(station_months: dict, era5: dict) -> tuple[list, list]:
    """Pares (variável, ano, mês, estação, era5) válidos e a lista de meses com chuva suspeita."""
    pairs, suspects = [], []
    for (year, month), s in sorted(station_months.items()):
        e = era5.get((year, month))
        if not e:
            continue
        rain_suspect = is_suspect_rain(s["total_rain_mm"], e["total_rain_mm"])
        if rain_suspect:
            suspects.append((year, month, s["total_rain_mm"], e["total_rain_mm"]))
        for field, _ in VARIABLES:
            if field in ("total_rain_mm", "rainy_days") and rain_suspect:
                continue
            if s[field] is not None and e[field] is not None:
                pairs.append((field, year, month, s[field], e[field]))
    return pairs, suspects


def stats(pairs):
    """{field: (n, viés médio, erro absoluto médio, soma estação, soma era5)}."""
    acc = defaultdict(list)
    for field, _, _, s, e in pairs:
        acc[field].append((s, e))
    out = {}
    for field, values in acc.items():
        diffs = [e - s for s, e in values]
        out[field] = (
            len(values),
            sum(diffs) / len(diffs),
            sum(abs(d) for d in diffs) / len(diffs),
            sum(s for s, _ in values),
            sum(e for _, e in values),
        )
    return out


def load_era5(db_path: str):
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        regions = [
            {"id": r[0], "name": r[1], "latitude": r[2], "longitude": r[3]}
            for r in con.execute("SELECT id, name, latitude, longitude FROM regions")
        ]
        era5 = defaultdict(dict)
        for row in con.execute(
            "SELECT region_id, year, month, avg_temp_c, max_temp_c, min_temp_c, total_rain_mm, rainy_days "
            "FROM monthly_climate_summaries WHERE year > 0"
        ):
            era5[row[0]][(row[1], row[2])] = dict(zip(
                ("avg_temp_c", "max_temp_c", "min_temp_c", "total_rain_mm", "rainy_days"), row[3:]
            ))
    finally:
        con.close()
    return regions, era5


def station_files(zf: zipfile.ZipFile):
    """(código, nome do arquivo) das estações de STATIONS dentro do zip anual."""
    for name in zf.namelist():
        m = re.search(r"_RS_([AB]\d{3})_", name)
        if m and m.group(1) in STATIONS and name.upper().endswith(".CSV"):
            yield m.group(1), name


def _open_year_zip(year: int, cache_dir: str | None):
    if cache_dir:
        path = os.path.join(cache_dir, f"{year}.zip")
        if os.path.exists(path):
            return path, False
    else:
        fd, path = tempfile.mkstemp(suffix=f"-bdmep-{year}.zip")
        os.close(fd)
    print(f"  baixando {year}.zip ...", file=sys.stderr)
    with requests.get(ZIP_URL.format(year=year), headers={"User-Agent": USER_AGENT}, stream=True, timeout=(10, 120)) as res:
        if res.status_code == 404:
            if not cache_dir:
                os.remove(path)
            return None, False
        res.raise_for_status()
        with open(path, "wb") as fh:
            for chunk in res.iter_content(1 << 20):
                fh.write(chunk)
    return path, not cache_dir


def collect_station_months(years, cache_dir=None) -> dict:
    """{código: {"latitude", "longitude", "months": {(ano, mês): resumo}}}."""
    stations = {}
    for year in years:
        path, temporary = _open_year_zip(year, cache_dir)
        if path is None:
            print(f"  {year}.zip não existe no portal, pulado", file=sys.stderr)
            continue
        try:
            with zipfile.ZipFile(path) as zf:
                for code, name in station_files(zf):
                    parsed = parse_station_csv(zf.read(name).decode("latin-1"))
                    entry = stations.setdefault(code, {"latitude": parsed["latitude"], "longitude": parsed["longitude"], "months": {}})
                    for key, value in monthly_summary(parsed["hours"]).items():
                        if key[0] == year:  # as horas convertidas p/ UTC-3 podem cair no ano anterior
                            entry["months"][key] = value
        finally:
            if temporary:
                os.remove(path)
    return stations


def _fmt(value, digits=1, signed=False):
    if value is None:
        return "–"
    return f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"


def render_report(stations: dict, regions, era5, years) -> str:
    lines = [
        "# Validação ERA5 × estações automáticas INMET (BDMEP)",
        "",
        f"Anos: {years[0]}–{years[-1]} • diferença = ERA5 − estação • mês entra com ≥ {MIN_COMPLETENESS:.0%} das horas • "
        f"chuva suspeita: estação < {SUSPECT_RAIN_RATIO:.0%} do ERA5 com ERA5 ≥ {SUSPECT_RAIN_MIN_MM:.0f} mm (fora das estatísticas).",
        "",
    ]
    all_pairs = []
    for code in sorted(stations):
        info = stations[code]
        pair = nearest_region(regions, info["latitude"], info["longitude"])
        lines.append(f"## {code} {STATIONS[code]}")
        if pair is None:
            lines += ["", f"Sem região do CLIMAE a até {MAX_PAIR_KM:.0f} km.", ""]
            continue
        region, dist = pair
        pairs, suspects = compare(info["months"], era5.get(region["id"], {}))
        all_pairs += pairs
        incomplete = sum(1 for m in info["months"].values() if m["total_rain_mm"] is None or m["avg_temp_c"] is None)
        lines += [
            "",
            f"Região: {region['name']} ({dist:.1f} km) • meses no arquivo: {len(info['months'])} • "
            f"meses com alguma variável incompleta: {incomplete} • chuva suspeita: {len(suspects)}",
            "",
            "| Variável | Meses | Viés médio | Erro médio abs. | Total estação | Total ERA5 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        st = stats(pairs)
        for field, label in VARIABLES:
            if field not in st:
                lines.append(f"| {label} | 0 | – | – | – | – |")
                continue
            n, bias, mae, s_sum, e_sum = st[field]
            totals = (f"{s_sum:.0f}", f"{e_sum:.0f}") if field in ("total_rain_mm", "rainy_days") else ("–", "–")
            lines.append(f"| {label} | {n} | {_fmt(bias, signed=True)} | {_fmt(mae)} | {totals[0]} | {totals[1]} |")
        if suspects:
            listed = ", ".join(f"{MONTHS_PT[m - 1]}/{y} ({s:.0f} × {e:.0f} mm)" for y, m, s, e in suspects)
            lines += ["", f"Chuva suspeita (estação × ERA5): {listed}"]
        lines.append("")

    lines += [
        "## Viés por mês do ano (todas as estações)",
        "",
        "| Mês | Temp. média | Máx. abs. | Mín. abs. | Chuva (mm) | Chuva ERA5/estação | Dias ≥ 1 mm |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for month in range(1, 13):
        st = stats([p for p in all_pairs if p[2] == month])
        cell = lambda f: _fmt(st[f][1], signed=True) if f in st else "–"
        ratio = "–"
        if "total_rain_mm" in st and st["total_rain_mm"][3] > 0:
            ratio = f"{st['total_rain_mm'][4] / st['total_rain_mm'][3]:.0%}"
        lines.append(
            f"| {MONTHS_PT[month - 1]} | {cell('avg_temp_c')} | {cell('max_temp_c')} | {cell('min_temp_c')} | "
            f"{cell('total_rain_mm')} | {ratio} | {cell('rainy_days')} |"
        )
    lines.append("")
    return "\n".join(lines)


def parse_years(value: str) -> list[int]:
    if "-" in value:
        start, end = (int(v) for v in value.split("-", 1))
    else:
        start = end = int(value)
    if start > end:
        raise argparse.ArgumentTypeError("intervalo de anos invertido")
    return list(range(start, end + 1))


def _db_path(arg: str | None) -> str:
    if arg:
        return arg
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///"):]
    return os.path.normpath(DEFAULT_DB)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--years", type=parse_years, default=parse_years(f"2008-{date.today().year - 1}"),
                        help="ano ou intervalo (ex.: 2025 ou 2015-2025); padrão 2008 até o ano passado")
    parser.add_argument("--db", help="banco SQLite (padrão: DATABASE_URL ou instance/climae_zonasul.db); aberto só para leitura")
    parser.add_argument("--cache", help="pasta para guardar/reusar os zips anuais (sem ela, cada zip é apagado após o uso)")
    parser.add_argument("--out", help="grava o relatório neste arquivo (padrão: saída padrão)")
    args = parser.parse_args()

    if args.cache:
        os.makedirs(args.cache, exist_ok=True)
    regions, era5 = load_era5(_db_path(args.db))
    stations = collect_station_months(args.years, args.cache)
    report = render_report(stations, regions, era5, args.years)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(report)
        print(f"Relatório gravado em {args.out}", file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
