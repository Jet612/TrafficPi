"""Trails: the path each vehicle took, one row per finished track.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trails",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("camera_id", sa.String(), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("started_at", sa.Integer(), nullable=False),
        sa.Column("ended_at", sa.Integer(), nullable=False),
        sa.Column("class_label", sa.String(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("extent", sa.Float(), nullable=False),
        sa.Column("path", sa.LargeBinary(), nullable=False),
    )
    op.create_index("ix_trails_camera_time", "trails", ["camera_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_trails_camera_time", table_name="trails")
    op.drop_table("trails")
