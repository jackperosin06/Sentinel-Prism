"""Add raw_captures and normalized_updates (Story 3.1 — FR7–FR10).

Revision ID: d8e9f0a1b2c3
Revises: a7f6e5d4c3b2
Create Date: 2026-04-17

FK notes:
- raw_captures.source_id → sources.id ON DELETE RESTRICT (preserve audit if source removed).
- normalized_updates.raw_capture_id → raw_captures.id ON DELETE RESTRICT.
- normalized_updates.source_id → sources.id ON DELETE RESTRICT.

Uniqueness notes:
- normalized_updates.raw_capture_id is UNIQUE: spec AC4 pins a one-to-one link
  raw→normalized for MVP. The UNIQUE B-tree replaces a would-be redundant plain
  index on the same column.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "a7f6e5d4c3b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "raw_captures",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("item_url", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_raw_captures_source_id_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raw_captures")),
    )
    op.create_index(
        "ix_raw_captures_run_id",
        "raw_captures",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_raw_captures_source_captured",
        "raw_captures",
        ["source_id", "captured_at"],
        unique=False,
    )

    op.create_table(
        "normalized_updates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_capture_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=512), nullable=False),
        sa.Column("jurisdiction", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("item_url", sa.Text(), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("body_snippet", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("extra_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parser_confidence", sa.Float(), nullable=True),
        sa.Column("extraction_quality", sa.Float(), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["raw_capture_id"],
            ["raw_captures.id"],
            name=op.f("fk_normalized_updates_raw_capture_id_raw_captures"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_normalized_updates_source_id_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_normalized_updates")),
        sa.UniqueConstraint(
            "raw_capture_id", name="uq_normalized_updates_raw_capture_id"
        ),
    )
    op.create_index(
        "ix_normalized_updates_run_id",
        "normalized_updates",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_normalized_updates_source_created",
        "normalized_updates",
        ["source_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_normalized_updates_source_created", table_name="normalized_updates")
    op.drop_index("ix_normalized_updates_run_id", table_name="normalized_updates")
    op.drop_table("normalized_updates")
    op.drop_index("ix_raw_captures_source_captured", table_name="raw_captures")
    op.drop_index("ix_raw_captures_run_id", table_name="raw_captures")
    op.drop_table("raw_captures")
