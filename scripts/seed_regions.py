import sys
import os

# Permite importar o pacote 'app' ao rodar o script diretamente
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.models import db, Region

INITIAL_REGIONS = [
    {"municipality": "Rio Grande", "name": "Centro / Zona Portuária", "latitude": -32.0350, "longitude": -52.0986},
    {"municipality": "Rio Grande", "name": "Balneário Cassino", "latitude": -32.1844, "longitude": -52.1611},
    {"municipality": "Rio Grande", "name": "Ilha dos Marinheiros", "latitude": -31.9961, "longitude": -52.1558},
    {"municipality": "Pelotas", "name": "Centro", "latitude": -31.7654, "longitude": -52.3376},
    {"municipality": "Pelotas", "name": "Laranjal", "latitude": -31.7519, "longitude": -52.2289},
    {"municipality": "São José do Norte", "name": "Sede Urbana", "latitude": -32.0150, "longitude": -52.0419},
    {"municipality": "São Lourenço do Sul", "name": "Sede Urbana / Orla", "latitude": -31.3653, "longitude": -51.9783},
    {"municipality": "Jaguarão", "name": "Sede Urbana", "latitude": -32.5661, "longitude": -53.3758},
    {"municipality": "Canguçu", "name": "Sede Urbana", "latitude": -31.3950, "longitude": -52.6756},
    {"municipality": "Santa Vitória do Palmar", "name": "Sede Urbana", "latitude": -33.5189, "longitude": -53.3681},
    {"municipality": "Chuí", "name": "Sede Urbana", "latitude": -33.6911, "longitude": -53.4561},
]

def seed():
    app = create_app()
    with app.app_context():
        # Cria todas as tabelas caso ainda não existam
        db.create_all()

        inserted = 0
        for item in INITIAL_REGIONS:
            exists = Region.query.filter_by(
                municipality=item["municipality"],
                name=item["name"]
            ).first()
            if not exists:
                region = Region(**item)
                db.session.add(region)
                inserted += 1

        db.session.commit()
        print(f"Processo concluído! {inserted} novas regiões cadastradas.")

if __name__ == "__main__":
    seed()
