"""Add review_queue_items projection for human-review API (Story 4.1 — FR16).

Revision ID: f1e2d3c4b5a6
Revises: e9f0a2b4c6d8
Create Date: 2026-04-19

Projection rows are upserted at ``human_review_gate`` immediately before
``interrupt()`` so the API can list awaiting runs without parsing checkpoint
blobs. Story 4.2 will clear rows on resume.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, Sequence[str], None] = "e9f0a2b4c6d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_queue_items",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "items_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_review_queue_items_source_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_review_queue_items_run_id"),
    )
    op.create_index(
        "ix_review_queue_items_queued_at",
        "review_queue_items",
        ["queued_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drops ``review_queue_items`` — lossy on populated databases."""

    op.drop_index("ix_review_queue_items_queued_at", table_name="review_queue_items")
    op.drop_table("review_queue_items")
