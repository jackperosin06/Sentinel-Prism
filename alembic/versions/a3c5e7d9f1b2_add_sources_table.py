"""Add sources table (Story 2.1).

Revision ID: a3c5e7d9f1b2
Revises: b2a8c3d19e4f
Create Date: 2026-04-16

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3c5e7d9f1b2"
down_revision: Union[str, Sequence[str], None] = "b2a8c3d19e4f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("jurisdiction", sa.String(length=256), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("primary_url", sa.Text(), nullable=False),
        sa.Column("schedule", sa.String(length=512), nullable=False),
        sa.Column(
            "extra_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "source_type IN ('rss', 'http')",
            name="ck_sources_source_type",
        ),
    )
    op.create_index("ix_sources_created_at", "sources", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sources_created_at", table_name="sources")
    op.drop_table("sources")
