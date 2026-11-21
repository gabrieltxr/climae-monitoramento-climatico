"""Média das máximas e das mínimas diárias no histórico mensal (monthly_climate_summaries).

Colunas novas ficam vazias (null) nos meses já gravados; o preenchimento é feito por
scripts/backfill_daily_temp_means.py, sem mexer nas demais colunas.

Revision ID: 0005_daily_temp_means
Revises: 0004_official_forecasts
"""
from alembic import op
import sqlalchemy as sa


revision = '0005_daily_temp_means'
down_revision = '0004_official_forecasts'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('monthly_climate_summaries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mean_max_temp_c', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('mean_min_temp_c', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('monthly_climate_summaries', schema=None) as batch_op:
        batch_op.drop_column('mean_min_temp_c')
        batch_op.drop_column('mean_max_temp_c')
