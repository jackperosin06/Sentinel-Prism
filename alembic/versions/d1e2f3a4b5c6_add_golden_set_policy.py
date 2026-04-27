"""Add golden_set_policy singleton for label policy + cadence (Story 7.4).

Revision ID: d1e2f3a4b5c6
Revises: c9e1f2a3b4c5
Create Date: 2026-04-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c9e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_LABEL_POLICY = (
    "Golden-set reference labels are owned by Regulatory Affairs. Disputes are "
    "resolved in review with the compliance lead. This placeholder policy applies "
    "until an admin replaces it with the approved label criteria (FR44)."
)


def upgrade() -> None:
    op.create_table(
        "golden_set_policy",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("label_policy_text", sa.Text(), nullable=False),
        sa.Column("refresh_cadence", sa.String(length=32), nullable=False),
        sa.Column(
            "refresh_after_major_classification_change",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column("draft_label_policy_text", sa.Text(), nullable=True),
        sa.Column("draft_refresh_cadence", sa.String(length=32), nullable=True),
        sa.Column("draft_refresh_after_major", sa.Boolean(), nullable=True),
        sa.Column("draft_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_golden_set_policy_singleton_id"),
        sa.CheckConstraint("version >= 1", name="ck_golden_set_policy_version_ge_1"),
        sa.CheckConstraint(
            "length(trim(label_policy_text)) > 0 AND length(label_policy_text) <= 32768",
            name="ck_golden_set_policy_label_valid",
        ),
        sa.CheckConstraint(
            "refresh_cadence IN ('quarterly')",
            name="ck_golden_set_policy_cadence_quarterly_only",
        ),
        sa.CheckConstraint(
            "draft_label_policy_text IS NULL OR "
            "(length(trim(draft_label_policy_text)) > 0 "
            "AND length(draft_label_policy_text) <= 32768)",
            name="ck_golden_set_policy_draft_label_valid",
        ),
        sa.CheckConstraint(
            "draft_refresh_cadence IS NULL OR draft_refresh_cadence IN ('quarterly')",
            name="ck_golden_set_policy_draft_cadence",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_golden_set_policy")),
    )
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO golden_set_policy "
            "(id, version, label_policy_text, refresh_cadence, "
            "refresh_after_major_classification_change, "
            "draft_label_policy_text, draft_refresh_cadence, draft_refresh_after_major, "
            "draft_reason) "
            "VALUES (1, 1, :policy, 'quarterly', true, "
            "NULL, NULL, NULL, NULL)"
        ),
        {"policy": _DEFAULT_LABEL_POLICY},
    )


def downgrade() -> None:
    op.drop_table("golden_set_policy")
