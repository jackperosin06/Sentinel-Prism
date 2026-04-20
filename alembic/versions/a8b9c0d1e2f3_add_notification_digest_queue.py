"""Add notification_digest_queue for batched notifications (Story 5.4).

Revision ID: a8b9c0d1e2f3
Revises: f7e8d9c0b1a2
Create Date: 2026-04-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f7e8d9c0b1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_digest_queue",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("item_url", sa.Text(), nullable=False),
        sa.Column("team_slug", sa.String(128), nullable=False),
        sa.Column("channel_slug", sa.String(128), nullable=True),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "run_id",
            "item_url",
            "team_slug",
            name="uq_notification_digest_queue_run_item_team",
        ),
    )
    op.create_index(
        "ix_notification_digest_queue_pending_team",
        "notification_digest_queue",
        ["team_slug", "created_at"],
    )
    op.create_index(
        "ix_notification_digest_queue_created_at",
        "notification_digest_queue",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_digest_queue_created_at", table_name="notification_digest_queue"
    )
    op.drop_index(
        "ix_notification_digest_queue_pending_team",
        table_name="notification_digest_queue",
    )
    op.drop_table("notification_digest_queue")
