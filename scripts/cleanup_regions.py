# scripts/cleanup_regions.py
import os
import sys

# Garante acesso aos módulos da aplicação
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from app.models import (
    Alert,
    MonthlyClimateSummary,
    Occurrence,
    Region,
    RiskAssessment,
    WeatherObservation,
    db,
)

class CleanupConfig:
    SQLALCHEMY_DATABASE_URI = 'sqlite:///clima_zonasul.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True

app = create_app(CleanupConfig)

with app.app_context():
    # 1. Mapa de equivalência para fundir duplicatas antigas
    # Antigo nome curto -> Novo nome padronizado
    legacy_mapping = {
        # Pelotas
        ("Pelotas", "Centro"): "Pelotas (Centro)",
        ("Pelotas", "Laranjal"): "Pelotas (Laranjal)",
        # Rio Grande
        ("Rio Grande", "Centro"): "Rio Grande (Centro)",
        ("Rio Grande", "Centro / Zona Portuária"): "Rio Grande (Centro)",
        ("Rio Grande", "Cassino"): "Rio Grande (Cassino)",
        ("Rio Grande", "Balneário Cassino"): "Rio Grande (Cassino)",
        ("Rio Grande", "Ilha dos Marinheiros"): "Rio Grande (Ilha dos Marinheiros)",
        # Demais municípios originais
        ("São José do Norte", "Sede Urbana"): "São José do Norte",
        ("São Lourenço do Sul", "Sede Urbana / Orla"): "São Lourenço do Sul",
        ("Jaguarão", "Sede Urbana"): "Jaguarão",
        ("Canguçu", "Sede Urbana"): "Canguçu",
        ("Santa Vitória do Palmar", "Sede Urbana"): "Santa Vitória do Palmar",
        ("Chuí", "Sede Urbana"): "Chuí",
    }

    for (city, old_name), new_name in legacy_mapping.items():
        old_reg = Region.query.filter_by(municipality=city, name=old_name).first()
        new_reg = Region.query.filter_by(municipality=city, name=new_name).first()

        if old_reg and new_reg and old_reg.id != new_reg.id:
            print(f"[FUSÃO] Removendo duplicata de {new_name}: transferindo dados do ID {new_reg.id} para o ID original {old_reg.id}...")
            
            # Migra observações, alertas, ocorrências e avaliações de risco do ID duplicado
            WeatherObservation.query.filter_by(region_id=new_reg.id).update({"region_id": old_reg.id})
            RiskAssessment.query.filter_by(region_id=new_reg.id).update({"region_id": old_reg.id})
            Alert.query.filter_by(region_id=new_reg.id).update({"region_id": old_reg.id})
            Occurrence.query.filter_by(region_id=new_reg.id).update({"region_id": old_reg.id})

            # Migra resumos mensais com segurança contra chave única (region_id, year, month)
            for m in MonthlyClimateSummary.query.filter_by(region_id=new_reg.id).all():
                has_in_old = MonthlyClimateSummary.query.filter_by(
                    region_id=old_reg.id, year=m.year, month=m.month
                ).first()
                if has_in_old:
                    db.session.delete(m)
                else:
                    m.region_id = old_reg.id

            # Atualiza o nome e coordenadas do registro original para o padrão completo
            old_reg.name = new_name
            old_reg.latitude = new_reg.latitude
            old_reg.longitude = new_reg.longitude
            
            # Deleta o registro duplicado
            db.session.delete(new_reg)
            db.session.commit()

        elif old_reg and not new_reg:
            # Apenas renomeia o antigo para o padrão novo
            print(f"[PADRONIZAÇÃO] Atualizando nome de '{old_name}' para '{new_name}' (ID {old_reg.id}).")
            old_reg.name = new_name
            db.session.commit()

    # 2. Totalização das regiões ativas no banco
    total_regions = Region.query.count()
    print(f"\n[CLIMAE] Limpeza concluída com sucesso! Total de regiões ativas no banco: {total_regions}")
