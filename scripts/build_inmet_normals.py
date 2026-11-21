"""
Gera app/data/inmet_normals_1991_2020.json a partir das planilhas oficiais do INMET
(Normais Climatológicas do Brasil 1991–2020, portal.inmet.gov.br/normais, aba 1991-2020).

As normais são publicadas uma vez por período de 30 anos: o JSON fica versionado no
repositório e este script só precisa rodar de novo se o INMET corrigir as planilhas.

Uso:
    python scripts/build_inmet_normals.py              # baixa as planilhas (~400 KB)
    python scripts/build_inmet_normals.py --dir PASTA  # usa planilhas já baixadas

Só entram as estações convencionais da Zona Sul (STATIONS). Valor "-" na planilha
(falha no período) vira null.
"""
import argparse
import io
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

import requests

BASE_URL = "https://portal.inmet.gov.br/uploads/normais/Normal-Climatologica-{name}.xlsx"
USER_AGENT = "Mozilla/5.0 (compatible; CLIMAE/1.0)"
OUTPUT = os.path.join(os.path.dirname(__file__), "..", "app", "data", "inmet_normals_1991_2020.json")

# Estações convencionais com normal 1991–2020 na Zona Sul do RS (nome com acentos;
# a planilha traz em maiúsculas e sem acento).
STATIONS = {"83985": "Pelotas", "83995": "Rio Grande", "83997": "Santa Vitória do Palmar"}
# Planilha -> campo no JSON.
VARIABLES = {
    "PREC": "total_rain_mm",          # precipitação acumulada (mm)
    "PRENDIAS1": "rainy_days",        # nº de dias com precipitação >= 1 mm
    "TMEDSECA": "avg_temp_c",         # temperatura média compensada (bulbo seco)
    "TMAX": "mean_max_temp_c",        # média das máximas diárias
    "TMIN": "mean_min_temp_c",        # média das mínimas diárias
}
STATIONS_SHEET = "ESTAÇÕES"

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def read_xlsx_rows(content: bytes) -> list[list[str]]:
    """Linhas da 1ª planilha como listas de texto (só biblioteca padrão; colunas vazias = "")."""
    z = zipfile.ZipFile(io.BytesIO(content))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{_NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
    sheets = sorted(
        (n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
        key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[-1]).group()),
    )
    rows = []
    for row in ET.fromstring(z.read(sheets[0])).iter(f"{_NS}row"):
        cells = {}
        for c in row.iter(f"{_NS}c"):
            letters = re.match(r"[A-Z]+", c.get("r", "")).group()
            index = 0
            for ch in letters:
                index = index * 26 + (ord(ch) - 64)
            v = c.find(f"{_NS}v")
            if c.get("t") == "s" and v is not None:
                value = shared[int(v.text)]
            elif v is not None:
                value = v.text or ""
            else:
                value = "".join(t.text or "" for t in c.iter(f"{_NS}t"))
            cells[index - 1] = value.strip()
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


def _number(value: str):
    try:
        return round(float(value.replace(",", ".")), 1)
    except (AttributeError, ValueError):
        return None


def parse_monthly(rows: list[list[str]], stations=STATIONS) -> dict:
    """{código: [jan..dez]} das linhas "código | nome | UF | 12 meses | ano"."""
    result = {}
    for row in rows:
        if row and row[0] in stations and len(row) >= 15:
            result[row[0]] = [_number(v) for v in row[3:15]]
    return result


def parse_stations(rows: list[list[str]], stations=STATIONS) -> dict:
    """{código: {latitude, longitude}} da planilha de estações (Nº | código | nome | UF | lat | lon ...)."""
    result = {}
    for row in rows:
        if len(row) >= 6 and row[1] in stations:
            result[row[1]] = {"latitude": _number_raw(row[4]), "longitude": _number_raw(row[5])}
    return result


def _number_raw(value: str):
    try:
        return round(float(value), 4)
    except ValueError:
        return None


def build(sheets: dict) -> dict:
    """sheets: {nome da planilha: bytes}. Devolve o documento do JSON."""
    info = parse_stations(read_xlsx_rows(sheets[STATIONS_SHEET]))
    values = {field: parse_monthly(read_xlsx_rows(sheets[name])) for name, field in VARIABLES.items()}
    stations = []
    for code in STATIONS:
        if code not in info:
            raise ValueError(f"estação {code} não encontrada na planilha de estações")
        months = []
        for m in range(12):
            entry = {"month": m + 1}
            for field, by_station in values.items():
                series = by_station.get(code)
                entry[field] = series[m] if series else None
            if entry["rainy_days"] is not None:
                entry["rainy_days"] = int(entry["rainy_days"])
            months.append(entry)
        stations.append({"code": code, "name": STATIONS[code], **info[code], "months": months})
    return {
        "period": "1991-2020",
        "source": "INMET — Normais Climatológicas do Brasil 1991-2020 (estações convencionais)",
        "source_url": "https://portal.inmet.gov.br/normais",
        "stations": stations,
    }


def _download(name: str) -> bytes:
    res = requests.get(BASE_URL.format(name=name), headers={"User-Agent": USER_AGENT}, timeout=(10, 60))
    res.raise_for_status()
    return res.content


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", help="pasta com as planilhas já baixadas (Normal-Climatologica-*.xlsx)")
    args = parser.parse_args()

    sheets = {}
    for name in (STATIONS_SHEET, *VARIABLES):
        if args.dir:
            with open(os.path.join(args.dir, f"Normal-Climatologica-{name}.xlsx"), "rb") as fh:
                sheets[name] = fh.read()
        else:
            sheets[name] = _download(name)

    document = build(sheets)
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(document, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"Gravado {os.path.normpath(OUTPUT)} ({len(document['stations'])} estações).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
