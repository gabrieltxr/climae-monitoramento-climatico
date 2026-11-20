"""Tabela official_warnings (avisos meteorológicos oficiais do INMET).

Revision ID: 0003_official_warnings
Revises: 0002_station_observations
"""
from alembic import op
import sqlalchemy as sa


revision = '0003_official_warnings'
down_revision = '0002_station_observations'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('official_warnings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('inmet_id', sa.String(length=20), nullable=False),
    sa.Column('warning_number', sa.String(length=20), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=True),
    sa.Column('event', sa.String(length=100), nullable=False),
    sa.Column('severity', sa.String(length=50), nullable=False),
    sa.Column('severity_level', sa.Integer(), nullable=True),
    sa.Column('color', sa.String(length=10), nullable=True),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('geocodes', sa.Text(), nullable=False),
    sa.Column('risks', sa.Text(), nullable=True),
    sa.Column('instructions', sa.Text(), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notification_status', sa.String(length=30), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('inmet_id')
    )
    with op.batch_alter_table('official_warnings', schema=None) as batch_op:
        batch_op.create_index('ix_official_warnings_ends_at', ['ends_at'], unique=False)


def downgrade():
    with op.batch_alter_table('official_warnings', schema=None) as batch_op:
        batch_op.drop_index('ix_official_warnings_ends_at')
    op.drop_table('official_warnings')
