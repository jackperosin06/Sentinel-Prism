"""Add functional index on lower(users.team_slug) for case-insensitive lookup.

Story 5.2 code-review fix (P4): ``list_user_ids_for_team_slug`` filters with
``lower(users.team_slug) == :norm``, which cannot use the plain b-tree
``ix_users_team_slug`` index. Add a functional index so the case-insensitive
team-membership lookup in the notification enqueue path is an index scan
instead of a seq scan.

Revision ID: e6f7a8b9c0d1
Revises: d4e5f6a7b8c9
Create Date: 2026-04-20
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_users_team_slug_lower "
        "ON users (lower(team_slug))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_team_slug_lower")
