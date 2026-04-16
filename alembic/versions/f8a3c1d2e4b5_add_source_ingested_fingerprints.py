"""Add source_ingested_fingerprints table (Story 2.4).

Revision ID: f8a3c1d2e4b5
Revises: e2f8a1c3d5b7
Create Date: 2026-04-16

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f8a3c1d2e4b5"
down_revision: Union[str, Sequence[str], None] = "e2f8a1c3d5b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_ingested_fingerprints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("item_url", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id",
            "fingerprint",
            name="uq_source_ingested_fingerprint_source_fingerprint",
        ),
    )
    op.create_index(
        "ix_source_ingested_fingerprints_source_id",
        "source_ingested_fingerprints",
        ["source_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_source_ingested_fingerprints_source_id",
        table_name="source_ingested_fingerprints",
    )
    op.drop_table("source_ingested_fingerprints")
