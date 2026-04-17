"""Add sources.fallback_url and sources.fallback_mode (Story 2.5 — FR5).

Revision ID: b9c8d7e6f5a4
Revises: f8a3c1d2e4b5
Create Date: 2026-04-17

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9c8d7e6f5a4"
down_revision: Union[str, Sequence[str], None] = "f8a3c1d2e4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("fallback_url", sa.Text(), nullable=True))
    op.add_column(
        "sources",
        sa.Column(
            "fallback_mode",
            sa.String(length=32),
            server_default="none",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("sources", "fallback_mode")
    op.drop_column("sources", "fallback_url")
