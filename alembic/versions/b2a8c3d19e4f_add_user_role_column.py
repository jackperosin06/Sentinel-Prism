"""Add users.role for RBAC (Story 1.4).

Revision ID: b2a8c3d19e4f
Revises: c4f9e2b18d0a
Create Date: 2026-04-14

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2a8c3d19e4f"
down_revision: Union[str, Sequence[str], None] = "c4f9e2b18d0a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "role",
            sa.String(length=32),
            server_default=sa.text("'viewer'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role IN ('admin', 'analyst', 'viewer')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "role")
