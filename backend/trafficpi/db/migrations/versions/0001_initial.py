"""Initial schema: cameras, events, minute counts, users, settings, profiles.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cameras",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("camera_id", sa.String(), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("occurred_at", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("track_id", sa.Integer()),
        sa.Column("class_label", sa.String()),
        sa.Column("line_id", sa.String()),
        sa.Column("direction", sa.Integer()),
        sa.Column("details", sa.JSON()),
        sa.Column("evidence_path", sa.String()),
    )
    op.create_index("ix_events_camera_time", "events", ["camera_id", "occurred_at"])
    op.create_index("ix_events_type_time", "events", ["type", "occurred_at"])
    op.create_table(
        "minute_counts",
        sa.Column("camera_id", sa.String(), sa.ForeignKey("cameras.id"), primary_key=True),
        sa.Column("bucket_start", sa.Integer(), primary_key=True),
        sa.Column("line_id", sa.String(), primary_key=True),
        sa.Column("direction", sa.Integer(), primary_key=True),
        sa.Column("class_label", sa.String(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
    )
    op.create_table(
        "profiles",
        sa.Column("camera_id", sa.String(), sa.ForeignKey("cameras.id"), primary_key=True),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    for table in ("profiles", "settings", "users", "minute_counts", "events", "cameras"):
        op.drop_table(table)
