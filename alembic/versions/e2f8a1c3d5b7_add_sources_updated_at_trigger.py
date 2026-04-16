"""Add DB-level trigger to maintain sources.updated_at.

Revision ID: e2f8a1c3d5b7
Revises: d4f6b8e0a2c1
Create Date: 2026-04-16

"""

from typing import Sequence, Union

from alembic import op

revision: str = "e2f8a1c3d5b7"
down_revision: Union[str, Sequence[str], None] = "d4f6b8e0a2c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;
"""

_DROP_FUNCTION = "DROP FUNCTION IF EXISTS set_updated_at();"

_CREATE_TRIGGER = """
CREATE TRIGGER trg_sources_updated_at
BEFORE UPDATE ON sources
FOR EACH ROW EXECUTE FUNCTION set_updated_at();
"""

_DROP_TRIGGER = "DROP TRIGGER IF EXISTS trg_sources_updated_at ON sources;"


def upgrade() -> None:
    op.execute(_CREATE_FUNCTION)
    op.execute(_CREATE_TRIGGER)


def downgrade() -> None:
    op.execute(_DROP_TRIGGER)
    op.execute(_DROP_FUNCTION)
