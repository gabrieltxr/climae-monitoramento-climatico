"""Tabela official_forecasts (previsão oficial do INMET por município).

Revision ID: 0004_official_forecasts
Revises: 0003_official_warnings
"""
from alembic import op
import sqlalchemy as sa


revision = '0004_official_forecasts'
down_revision = '0003_official_warnings'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('official_forecasts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('geocode', sa.String(length=7), nullable=False),
    sa.Column('forecast_date', sa.Date(), nullable=False),
    sa.Column('period', sa.String(length=10), nullable=False),
    sa.Column('summary', sa.String(length=200), nullable=False),
    sa.Column('temp_min', sa.Float(), nullable=True),
    sa.Column('temp_max', sa.Float(), nullable=True),
    sa.Column('humidity_min', sa.Float(), nullable=True),
    sa.Column('humidity_max', sa.Float(), nullable=True),
    sa.Column('wind_direction', sa.String(length=10), nullable=True),
    sa.Column('wind_intensity', sa.String(length=30), nullable=True),
    sa.Column('icon_code', sa.String(length=10), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('geocode', 'forecast_date', 'period', name='uq_official_forecast_geocode_date_period')
    )


def downgrade():
    op.drop_table('official_forecasts')
