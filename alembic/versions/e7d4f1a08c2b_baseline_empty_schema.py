"""Baseline: Alembic chain; no domain DDL (Story 1.2).

Story 1.3+ adds tables via new revisions.

Revision ID: e7d4f1a08c2b
Revises:
Create Date: 2026-04-14

"""

from typing import Sequence, Union

revision: str = "e7d4f1a08c2b"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intentionally empty: establishes revision chain and alembic_version only.
    pass


def downgrade() -> None:
    pass
