"""
Esquema do banco via migrations (Flask-Migrate / Alembic, pasta migrations/).

ensure_schema() deixa o banco na última revisão:
- banco novo (vazio): cria tudo pelas migrations;
- banco legado, criado por db.create_all() antes das migrations (tem tabelas mas não tem
  alembic_version): marca a revisão inicial sem recriar nada e aplica as seguintes;
- banco já versionado: aplica só as revisões que faltam.

Precisa de contexto de aplicação (with app.app_context()).
"""
import logging

from flask_migrate import stamp, upgrade
from sqlalchemy import inspect

from app.models import db

logger = logging.getLogger(__name__)

BASELINE_REVISION = "0001_esquema_inicial"


def ensure_schema() -> str:
    tables = set(inspect(db.engine).get_table_names())
    status = "atualizado"
    if "alembic_version" not in tables and "regions" in tables:
        logger.info("Banco sem controle de versão: marcando a revisão inicial (%s).", BASELINE_REVISION)
        stamp(revision=BASELINE_REVISION)
        status = "legado_marcado"
    elif "alembic_version" not in tables:
        status = "criado"
    upgrade()
    return status
