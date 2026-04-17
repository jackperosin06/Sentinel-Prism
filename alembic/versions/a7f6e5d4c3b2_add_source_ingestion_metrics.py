"""Add per-source ingestion metrics columns (Story 2.6 — NFR9).

Revision ID: a7f6e5d4c3b2
Revises: b9c8d7e6f5a4
Create Date: 2026-04-17

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7f6e5d4c3b2"
down_revision: Union[str, Sequence[str], None] = "b9c8d7e6f5a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # BigInteger for all counters: ``items_ingested_total`` can realistically exceed
    # 2**31 for high-volume sources over the source's lifetime; ``poll_attempts_*``
    # use BigInteger for consistency and cheap future-proofing (Story 2.6 review P7).
    op.add_column(
        "sources",
        sa.Column(
            "poll_attempts_success",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "poll_attempts_failed",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "items_ingested_total",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "sources",
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("last_success_latency_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("last_success_fetch_path", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sources", "last_success_fetch_path")
    op.drop_column("sources", "last_success_latency_ms")
    op.drop_column("sources", "last_failure_at")
    op.drop_column("sources", "last_success_at")
    op.drop_column("sources", "items_ingested_total")
    op.drop_column("sources", "poll_attempts_failed")
    op.drop_column("sources", "poll_attempts_success")
