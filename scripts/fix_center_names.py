# scripts/fix_center_names.py
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import create_app, db
from app.models import Region

class FixConfig:
    SQLALCHEMY_DATABASE_URI = 'sqlite:///clima_zonasul.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True

app = create_app(FixConfig)

with app.app_context():
    # Mapeamento direto dos IDs originais e normalização de encoding
    target_centers = {
        6: ("São José do Norte", "SJN (Centro)"),
        7: ("São Lourenço do Sul", "São Lourenço (Centro)"),
        8: ("Jaguarão", "Jaguarão (Centro)"),
        9: ("Canguçu", "Canguçu (Centro)"),
        10: ("Santa Vitória do Palmar", "SVP (Centro)"),
        11: ("Chuí", "Chuí (Centro)"),
    }

    for reg_id, (city_name, new_center_name) in target_centers.items():
        reg = db.session.get(Region, reg_id)
        if reg:
            reg.municipality = city_name
            reg.name = new_center_name
            print(f"[OK] ID {reg_id}: {city_name} -> {new_center_name}")

    # Também roda a verificação genérica por nome
    rename_map = {
        "Santa Vitória do Palmar": "SVP (Centro)",
        "São Lourenço do Sul": "São Lourenço (Centro)",
        "São José do Norte": "SJN (Centro)",
        "Canguçu": "Canguçu (Centro)",
        "Jaguarão": "Jaguarão (Centro)",
        "Chuí": "Chuí (Centro)",
    }

    for city_name, new_center_name in rename_map.items():
        reg = Region.query.filter_by(municipality=city_name, name=city_name).first()
        if reg:
            reg.name = new_center_name
            print(f"[OK] {city_name} -> {new_center_name}")

    db.session.commit()
    print("\nTodos os centros foram padronizados com sucesso!")
