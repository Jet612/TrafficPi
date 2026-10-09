"""The daily email: who gets it, each scheduled mail, and each recipient's copy of it.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_recipients",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("address", sa.String(), nullable=False, unique=True),
        sa.Column("added_at", sa.Integer(), nullable=False),
        sa.Column("last_at", sa.Integer(), nullable=True),
        sa.Column("last_kind", sa.String(), nullable=True),
        sa.Column("last_status", sa.String(), nullable=True),
        sa.Column("last_detail", sa.String(), nullable=True),
    )
    op.create_table(
        "mail_sends",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scheduled_at", sa.Integer(), nullable=False, unique=True),
        sa.Column("period_start", sa.Integer(), nullable=False),
        sa.Column("period_end", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("sender", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body_text", sa.String(), nullable=False),
        sa.Column("body_html", sa.String(), nullable=False),
    )
    op.create_table(
        "mail_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "send_id",
            sa.Integer(),
            sa.ForeignKey("mail_sends.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recipient_id",
            sa.Integer(),
            sa.ForeignKey("mail_recipients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("provider_id", sa.String(), nullable=True),
        sa.Column("sent_at", sa.Integer(), nullable=True),
        sa.UniqueConstraint("send_id", "recipient_id"),
    )
    op.create_index(
        "ix_mail_deliveries_due", "mail_deliveries", ["status", "next_attempt_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_mail_deliveries_due", table_name="mail_deliveries")
    op.drop_table("mail_deliveries")
    op.drop_table("mail_sends")
    op.drop_table("mail_recipients")
