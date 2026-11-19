import os
import sys

# Permite importar o pacote 'app' ao rodar o script diretamente
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.models import db, Region
from app.db_schema import ensure_schema
from app.regions_catalog import REGIONS

# scripts/seed_regions.py
# Malha Geoespacial de Alta Densidade do CLIMAE (8 Cidades x 7 Regiões = 56 Pontos)

# Catálogo único: app/regions_catalog.py
ALL_REGIONS = REGIONS

def seed_all_regions(db_session, RegionModel):
    added, updated = 0, 0
    for reg_data in ALL_REGIONS:
        existing = db_session.query(RegionModel).filter_by(name=reg_data["name"]).first()
        if existing:
            existing.city = reg_data["city"]
            existing.latitude = reg_data["latitude"]
            existing.longitude = reg_data["longitude"]
            updated += 1
        else:
            new_region = RegionModel(**reg_data)
            db_session.add(new_region)
            added += 1
            
    db_session.commit()
    print(f"[CLIMAE] Malha Geoespacial Sincronizada: {added} novas regiões adicionadas, {updated} atualizadas (Total: {len(ALL_REGIONS)} pontos).")

def seed():
    app = create_app()
    with app.app_context():
        ensure_schema()
        seed_all_regions(db.session, Region)

if __name__ == "__main__":
    seed()
