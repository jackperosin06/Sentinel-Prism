"""Add update_feedback for Story 7.1 (FR26, FR27).

Revision ID: b3c4d5e6f7a8
Revises: a8b9c0d1e2f3
Create Date: 2026-04-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "update_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "normalized_update_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("normalized_updates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "classification_snapshot",
            postgresql.JSONB(none_as_null=True),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_update_feedback_normalized_update_id",
        "update_feedback",
        ["normalized_update_id"],
    )
    op.create_index(
        "ix_update_feedback_user_id_created",
        "update_feedback",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_update_feedback_run_id",
        "update_feedback",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_update_feedback_run_id", table_name="update_feedback")
    op.drop_index("ix_update_feedback_user_id_created", table_name="update_feedback")
    op.drop_index("ix_update_feedback_normalized_update_id", table_name="update_feedback")
    op.drop_table("update_feedback")
