import argparse
import sys
import os
import time
from datetime import datetime, timezone
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.models import db, Region, Occurrence

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def fetch_batch_archive(regions_batch: list[Region], start_date: str, end_date: str, retries: int = 3) -> list[dict] | None:
    """
    Consulta múltiplos municípios em UMA ÚNICA requisição HTTP na Open-Meteo Archive,
    reduzindo drasticamente o consumo de requisições e tratando a janela de 60s do erro 429.
    """
    lats = ",".join(str(r.latitude) for r in regions_batch)
    lons = ",".join(str(r.longitude) for r in regions_batch)

    params = {
        "latitude": lats,
        "longitude": lons,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "precipitation_sum,wind_gusts_10m_max",
        "timezone": "UTC",
    }

    for attempt in range(retries):
        try:
            res = requests.get(ARCHIVE_URL, params=params, timeout=45)
            if res.status_code == 429:
                wait = 65  # A cota por minuto da Open-Meteo reseta a cada 60 segundos
                print(f"  [Aviso 429] Cota por minuto da Open-Meteo atingida. Aguardando {wait}s para resetar...")
                time.sleep(wait)
                continue

            res.raise_for_status()
            data = res.json()
            # Se for 1 única região, a API retorna um dict; se forem várias, retorna uma lista de dicts
            return data if isinstance(data, list) else [data]

        except requests.RequestException as exc:
            if attempt == retries - 1:
                print(f"  [Erro] Falha ao consultar lote: {exc}")
            else:
                time.sleep(5)

    return None


def scan_extremes(start_date: str, end_date: str, min_rain: float, min_wind: float, save_to_db: bool):
    app = create_app()
    with app.app_context():
        all_regions = Region.query.order_by(Region.municipality).all()

        # Seleciona 1 região representativa por município
        unique_regions = []
        seen = set()
        for r in all_regions:
            if r.municipality not in seen:
                seen.add(r.municipality)
                unique_regions.append(r)

        total_found = 0
        total_saved = 0

        print(f"\nBuscando anomalias históricas entre {start_date} e {end_date}")
        print(f"Critérios: Chuva >= {min_rain} mm/dia OU Rajada de Vento >= {min_wind} km/h")
        print(f"Total de municípios: {len(unique_regions)} (processados em lotes para evitar erro 429)\n" + "-" * 78)

        # Divide em lotes de 3 municípios por requisição
        batch_size = 3
        for b_idx in range(0, len(unique_regions), batch_size):
            batch = unique_regions[b_idx:b_idx + batch_size]
            nomes = ", ".join(r.municipality for r in batch)
            print(f"Consultando lote: {nomes}...")

            results = fetch_batch_archive(batch, start_date, end_date)
            if not results:
                continue

            batch_saved = 0
            for reg, data in zip(batch, results):
                daily = data.get("daily", {})
                dates = daily.get("time", [])
                rains = daily.get("precipitation_sum", [])
                winds = daily.get("wind_gusts_10m_max", [])

                for i, d_str in enumerate(dates):
                    rain_val = rains[i] if i < len(rains) and rains[i] is not None else 0.0
                    wind_val = winds[i] if i < len(winds) and winds[i] is not None else 0.0

                    if rain_val >= min_rain or wind_val >= min_wind:
                        total_found += 1
                        triggers = []
                        if rain_val >= min_rain:
                            triggers.append(f"Chuva: {rain_val:.1f} mm")
                        if wind_val >= min_wind:
                            triggers.append(f"Rajada: {wind_val:.1f} km/h")

                        trigger_text = " | ".join(triggers)
                        print(f"  [{d_str}] {reg.municipality:<24} -> {trigger_text}")

                        if save_to_db:
                            dt_utc = datetime.fromisoformat(d_str).replace(hour=12, tzinfo=timezone.utc)
                            ev_type = "Chuva Extrema (Reanálise)" if rain_val >= min_rain else "Vendaval (Reanálise)"
                            if rain_val >= min_rain and wind_val >= min_wind:
                                ev_type = "Tempestade Severa (Reanálise)"

                            exists = Occurrence.query.filter_by(
                                region_id=reg.id,
                                occurred_at=dt_utc,
                                source="Open-Meteo Archive"
                            ).first()

                            if not exists:
                                occ = Occurrence(
                                    region_id=reg.id,
                                    event_type=ev_type,
                                    occurred_at=dt_utc,
                                    severity="Alta" if (rain_val >= 110 or wind_val >= 100) else "Média",
                                    description=f"Gatilho meteorológico detectado via série histórica ({trigger_text}). Requer validação de impacto local.",
                                    source="Open-Meteo Archive",
                                    reliability="anomalia meteorológica"
                                )
                                db.session.add(occ)
                                batch_saved += 1
                                total_saved += 1

            if save_to_db and batch_saved > 0:
                db.session.commit()

            # Pausa entre lotes
            if b_idx + batch_size < len(unique_regions):
                time.sleep(4)

        print("-" * 78)
        print(f"Varredura concluída! Dias extremos encontrados ({start_date} a {end_date}): {total_found}")
        if save_to_db:
            print(f"Novos registros salvos no banco: {total_saved}")
        else:
            print("Dica: Use '--save' para gravar esses registros no banco.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Busca eventos meteorológicos extremos (2000-2025) em lotes no Open-Meteo Archive.")
    parser.add_argument("--start", default="2000-01-01", help="Data inicial (YYYY-MM-DD)")
    parser.add_argument("--end", default="2025-12-31", help="Data final (YYYY-MM-DD)")
    parser.add_argument("--rain", type=float, default=85.0, help="Limiar de chuva diária em mm (padrão: 85)")
    parser.add_argument("--wind", type=float, default=90.0, help="Limiar de rajada de vento em km/h (padrão: 90)")
    parser.add_argument("--save", action="store_true", help="Salva os extremos encontrados na tabela occurrences")

    args = parser.parse_args()
    scan_extremes(args.start, args.end, args.rain, args.wind, args.save)
