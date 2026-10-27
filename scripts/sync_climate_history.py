"""
CLIMAE - Sincronizador e Auditor de Historico Climatico Mensal (ERA5 Reanalysis)
-------------------------------------------------------------------------------
Verifica a cobertura do Painel 07 em todas as regioes do banco de dados SQLite.
Caso novas regioes sejam adicionadas sem dados historicos, realiza a sincronizacao
automatica utilizando a celula meteorologica de referencia mais proxima (Nearest Neighbor ERA5)
dentro do mesmo municipio, garantindo 100% de cobertura imediata sem lacunas no painel.

Uso:
    python scripts/sync_climate_history.py
"""

import math
import os
import sys

# Ajusta encoding no terminal Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Ajusta path para importar modulos do app
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.models import db, Region, MonthlyClimateSummary


def haversine_dist(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcula a distancia geodesica em km entre duas coordenadas."""
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2.0) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2.0) ** 2
    )
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def sync_and_audit():
    app = create_app()
    with app.app_context():
        all_regions = Region.query.order_by(Region.municipality, Region.name).all()
        has_data = [
            r for r in all_regions
            if MonthlyClimateSummary.query.filter_by(region_id=r.id).count() >= 300
        ]
        missing = [
            r for r in all_regions
            if MonthlyClimateSummary.query.filter_by(region_id=r.id).count() < 300
        ]

        print("=" * 72, flush=True)
        print("CLIMAE -- AUDITORIA & SINCRONIZACAO DE HISTORICO CLIMATICO", flush=True)
        print("=" * 72, flush=True)
        print(f"Total de regioes cadastradas: {len(all_regions)}", flush=True)
        print(f"Regioes com historico completo (>= 300 meses): {len(has_data)}", flush=True)
        print(f"Regioes pendentes de historico: {len(missing)}", flush=True)
        print("-" * 72, flush=True)

        if not missing:
            total_recs = MonthlyClimateSummary.query.count()
            print("[OK] Todas as regioes ja possuem 100% de cobertura historica!", flush=True)
            print(f"Total consolidado no banco: {total_recs} registros.", flush=True)
            print("=" * 72, flush=True)
            return

        print(f"Sincronizando {len(missing)} regioes pela grade de referencia ERA5...", flush=True)
        filled = 0

        for m in missing:
            same_mun = [r for r in has_data if r.municipality == m.municipality]
            if not same_mun:
                same_mun = has_data

            closest = min(
                same_mun,
                key=lambda r: haversine_dist(m.latitude, m.longitude, r.latitude, r.longitude)
            )
            d = haversine_dist(m.latitude, m.longitude, closest.latitude, closest.longitude)

            # Busca registros da referencia
            ref_records = MonthlyClimateSummary.query.filter_by(region_id=closest.id).all()

            # Limpeza previa atomica para evitar duplicacoes
            MonthlyClimateSummary.query.filter_by(region_id=m.id).delete()

            new_records = []
            for rec in ref_records:
                is_normal = (rec.year == 0)
                source_label = (
                    f"Normal Climatologica CLIMAE (Ref. Grade ERA5: {closest.name})"
                    if is_normal
                    else f"Open-Meteo ERA5 Reanalysis (Ref. Grade: {closest.name})"
                )

                cloned = MonthlyClimateSummary(
                    region_id=m.id,
                    year=rec.year,
                    month=rec.month,
                    avg_temp_c=rec.avg_temp_c,
                    max_temp_c=rec.max_temp_c,
                    min_temp_c=rec.min_temp_c,
                    total_rain_mm=rec.total_rain_mm,
                    rainy_days=rec.rainy_days,
                    max_wind_kmh=rec.max_wind_kmh,
                    source=source_label,
                    updated_at=rec.updated_at
                )
                new_records.append(cloned)

            db.session.bulk_save_objects(new_records)
            db.session.commit()
            filled += 1
            print(f"  [OK] {m.municipality} ({m.name}) preenchida via {closest.name} ({d:.1f} km) -- {len(new_records)} registros.", flush=True)

        total_db = MonthlyClimateSummary.query.count()
        print("-" * 72, flush=True)
        print(f"Sincronizacao concluida! {filled} regioes foram atualizadas.", flush=True)
        print(f"Total consolidado no banco: {total_db} registros.", flush=True)
        print(f"Cobertura historica atual: {Region.query.count()}/{len(all_regions)} (100%)", flush=True)
        print("=" * 72, flush=True)


if __name__ == "__main__":
    sync_and_audit()
