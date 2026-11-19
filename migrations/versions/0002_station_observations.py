"""Tabela station_observations (medições das estações automáticas do INMET).

Revision ID: 0002_station_observations
Revises: 0001_esquema_inicial
"""
from alembic import op
import sqlalchemy as sa


revision = '0002_station_observations'
down_revision = '0001_esquema_inicial'
branch_labels = None
depends_on = None


def upgrade():
    # Versões anteriores criavam esta tabela ao subir o app; num banco assim ela já existe.
    if sa.inspect(op.get_bind()).has_table('station_observations'):
        return
    op.create_table('station_observations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('station_code', sa.String(length=10), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('collected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('temperature_c', sa.Float(), nullable=True),
    sa.Column('humidity_percent', sa.Float(), nullable=True),
    sa.Column('precipitation_mm', sa.Float(), nullable=True),
    sa.Column('wind_speed_kmh', sa.Float(), nullable=True),
    sa.Column('wind_gust_kmh', sa.Float(), nullable=True),
    sa.Column('wind_direction', sa.Float(), nullable=True),
    sa.Column('pressure_hpa', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('station_code', 'observed_at', name='uq_station_observed_at')
    )


def downgrade():
    op.drop_table('station_observations')
