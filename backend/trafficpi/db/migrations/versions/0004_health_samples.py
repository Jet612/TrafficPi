"""A row a minute of how the machine was: temperature, load, frame rate, throttle flags, camera.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_samples",
        sa.Column("camera_id", sa.String(), sa.ForeignKey("cameras.id"), primary_key=True),
        sa.Column("minute_start", sa.Integer(), primary_key=True),
        sa.Column("temp_c_max", sa.Float(), nullable=True),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("fps_mean", sa.Float(), nullable=True),
        sa.Column("fps_min", sa.Float(), nullable=True),
        sa.Column("throttled", sa.Integer(), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.Column("camera_ok_samples", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("health_samples")
