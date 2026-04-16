"""Add unique constraint on sources.name (Story 2.1 review).

Revision ID: d4f6b8e0a2c1
Revises: a3c5e7d9f1b2
Create Date: 2026-04-16

"""

from typing import Sequence, Union

from alembic import op

revision: str = "d4f6b8e0a2c1"
down_revision: Union[str, Sequence[str], None] = "a3c5e7d9f1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_sources_name_unique", "sources", ["name"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_sources_name_unique", table_name="sources")
