"""Add classification_policy singleton for governed threshold/prompt (Story 7.3).

Revision ID: c9e1f2a3b4c5
Revises: b3c4d5e6f7a8
Create Date: 2026-04-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LOW_CONFIDENCE_THRESHOLD = 0.5
CLASSIFICATION_SYSTEM_PROMPT = (
    "You are a regulatory monitoring classifier. Given a normalized public-source "
    "update, assign severity, impact_categories, urgency, a short rationale, and a "
    "confidence score in [0,1]. Use only the provided fields; do not invent citations.\n"
    "\n"
    "`impact_categories` MUST be drawn from: safety, labeling, manufacturing, "
    "deadlines, reporting, licensing, pricing, other. Use `other` when none fit."
)


def upgrade() -> None:
    op.create_table(
        "classification_policy",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column(
            "version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("low_confidence_threshold", sa.Float(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("draft_low_confidence_threshold", sa.Float(), nullable=True),
        sa.Column("draft_system_prompt", sa.Text(), nullable=True),
        sa.Column("draft_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "id = 1",
            name="ck_classification_policy_singleton_id",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_classification_policy_version_ge_1",
        ),
        sa.CheckConstraint(
            "low_confidence_threshold >= 0 AND low_confidence_threshold <= 1",
            name="ck_classification_policy_threshold_range",
        ),
        sa.CheckConstraint(
            "length(trim(system_prompt)) > 0 AND length(system_prompt) <= 32768",
            name="ck_classification_policy_prompt_valid",
        ),
        sa.CheckConstraint(
            "draft_low_confidence_threshold IS NULL OR "
            "(draft_low_confidence_threshold >= 0 "
            "AND draft_low_confidence_threshold <= 1)",
            name="ck_classification_policy_draft_threshold_range",
        ),
        sa.CheckConstraint(
            "draft_system_prompt IS NULL OR "
            "(length(trim(draft_system_prompt)) > 0 "
            "AND length(draft_system_prompt) <= 32768)",
            name="ck_classification_policy_draft_prompt_valid",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classification_policy")),
    )
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO classification_policy "
            "(id, version, low_confidence_threshold, system_prompt, "
            "draft_low_confidence_threshold, draft_system_prompt, draft_reason) "
            "VALUES (1, 1, :th, :prompt, NULL, NULL, NULL)"
        ),
        {"th": LOW_CONFIDENCE_THRESHOLD, "prompt": CLASSIFICATION_SYSTEM_PROMPT},
    )


def downgrade() -> None:
    op.drop_table("classification_policy")
