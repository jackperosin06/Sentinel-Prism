"""Add notification_delivery_attempts for external channel delivery log (Story 5.3).

Revision ID: f7e8d9c0b1a2
Revises: e6f7a8b9c0d1
Create Date: 2026-04-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "f7e8d9c0b1a2"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_delivery_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("item_url", sa.Text(), nullable=False),
        sa.Column(
            "channel",
            sa.String(32),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.String(16),
            nullable=False,
        ),
        sa.Column("error_class", sa.String(128), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("provider_message_id", sa.String(512), nullable=True),
        sa.Column("recipient_descriptor", sa.String(320), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "channel IN ('smtp', 'slack_webhook')",
            name="ck_notification_delivery_attempts_channel",
        ),
        sa.CheckConstraint(
            "outcome IN ('pending', 'success', 'failure', 'skipped')",
            name="ck_notification_delivery_attempts_outcome",
        ),
        sa.UniqueConstraint(
            "run_id",
            "item_url",
            "channel",
            "recipient_descriptor",
            name="uq_notification_delivery_attempts_idempotent",
        ),
    )
    op.create_index(
        "ix_notification_delivery_attempts_created_at",
        "notification_delivery_attempts",
        ["created_at"],
    )
    op.create_index(
        "ix_notification_delivery_attempts_outcome_created",
        "notification_delivery_attempts",
        ["outcome", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_delivery_attempts_outcome_created",
        table_name="notification_delivery_attempts",
    )
    op.drop_index(
        "ix_notification_delivery_attempts_created_at",
        table_name="notification_delivery_attempts",
    )
    op.drop_table("notification_delivery_attempts")
