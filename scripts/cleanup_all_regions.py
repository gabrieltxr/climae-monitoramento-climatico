# scripts/cleanup_all_regions.py
import os
import sys

# Garante acesso aos módulos da aplicação Flask
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app, db
from app.config import Config
from app.models import Region
from app.regions_catalog import REGIONS, LEGACY_REGION_NAMES
from app.services.region_maintenance import merge_region_into, delete_region_with_dependents

class CleanupConfig(Config):
    TESTING = True

app = create_app(CleanupConfig)

# Catálogo único e nomes antigos: app/regions_catalog.py
CANONICAL_REGIONS = REGIONS
ALIASES = LEGACY_REGION_NAMES

with app.app_context():
    print("[CLIMAE SANEAMENTO] Iniciando auditoria e limpeza da base de regiões...")

    # 1. Resolver aliases e fundir dados
    for (city, old_name), target_canonical_name in ALIASES.items():
        old_reg = Region.query.filter_by(municipality=city, name=old_name).first()
        target_reg = Region.query.filter_by(municipality=city, name=target_canonical_name).first()

        if old_reg and target_reg and old_reg.id != target_reg.id:
            print(f" -> Fusão: Migrando dados de '{old_name}' (ID {old_reg.id}) para '{target_canonical_name}' (ID {target_reg.id})...")
            result = merge_region_into(old_reg, target_reg)
            print(f"    movidos: {result['moved'] or 'nada'} | duplicados descartados: {result['dropped_duplicates'] or 'nenhum'}")
        elif old_reg and not target_reg:
            print(f" -> Renomeando: '{old_name}' para formato canônico '{target_canonical_name}' (ID {old_reg.id}).")
            old_reg.name = target_canonical_name
            db.session.commit()

    # 2. Assegurar que as 56 regiões canônicas existam e com coordenadas corretas
    canonical_names = {r["name"] for r in CANONICAL_REGIONS}
    for item in CANONICAL_REGIONS:
        reg = Region.query.filter_by(name=item["name"]).first()
        if reg:
            reg.city = item["city"]
            reg.latitude = item["latitude"]
            reg.longitude = item["longitude"]
        else:
            print(f" -> Inserindo região faltante: {item['name']}")
            db.session.add(Region(**item))
    db.session.commit()

    # 3. Remover registros espúrios que não pertençam ao catálogo canônico oficial
    all_current_regions = Region.query.all()
    removed_count = 0
    for reg in all_current_regions:
        if reg.name not in canonical_names:
            print(f" -> Removendo registro espúrio/redundante: '{reg.name}' (Cidade: {reg.city}, ID: {reg.id})")
            # Apaga junto tudo o que aponta para a região (alertas, riscos, relatos etc.).
            deleted = delete_region_with_dependents(reg)
            print(f"    linhas apagadas junto: {deleted or 'nenhuma'}")
            removed_count += 1
            
    db.session.commit()

    total_final = Region.query.count()
    print(f"\n[CLIMAE SANEAMENTO CONCLUÍDO]")
    print(f"  • Registros redundantes/espúrios removidos: {removed_count}")
    print(f"  • Total exato de regiões ativas na base: {total_final} (Esperado: 56)\n")
