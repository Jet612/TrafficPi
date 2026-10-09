"""Stop events can be reviewed: confirmed, or marked as a false positive.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("events", sa.Column("review", sa.String(), nullable=True))
    op.add_column("events", sa.Column("reviewed_at", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("events", "reviewed_at")
    op.drop_column("events", "review")
