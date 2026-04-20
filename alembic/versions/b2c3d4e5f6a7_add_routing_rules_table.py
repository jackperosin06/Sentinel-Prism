"""Add routing_rules mock table + routing_applied audit uniqueness for Story 5.1 (FR21).

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f7
Create Date: 2026-04-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "routing_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("rule_type", sa.String(length=16), nullable=False),
        sa.Column("impact_category", sa.String(length=128), nullable=True),
        sa.Column("severity_value", sa.String(length=32), nullable=True),
        sa.Column("team_slug", sa.String(length=128), nullable=False),
        sa.Column("channel_slug", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(rule_type = 'topic' AND impact_category IS NOT NULL AND severity_value IS NULL) "
            "OR (rule_type = 'severity' AND severity_value IS NOT NULL AND impact_category IS NULL)",
            name="ck_routing_rules_topic_xor_severity",
        ),
        # Story 5.1 review: enforce normalized matching keys at the DB so
        # two rows like ``"Labeling"`` / ``"labeling "`` cannot both exist
        # and alias against the same classification input after
        # application-side ``strip().lower()`` normalization.
        sa.CheckConstraint(
            "impact_category IS NULL OR ("
            "impact_category = lower(impact_category) "
            "AND impact_category = trim(impact_category) "
            "AND length(impact_category) > 0)",
            name="ck_routing_rules_impact_category_normalized",
        ),
        sa.CheckConstraint(
            "severity_value IS NULL OR ("
            "severity_value = lower(severity_value) "
            "AND severity_value = trim(severity_value) "
            "AND length(severity_value) > 0)",
            name="ck_routing_rules_severity_value_normalized",
        ),
    )
    op.create_index(
        "ix_routing_rules_rule_type_priority",
        "routing_rules",
        ["rule_type", "priority"],
        unique=False,
    )
    op.create_index(
        "ix_routing_rules_impact_category",
        "routing_rules",
        ["impact_category"],
        unique=False,
    )
    op.create_index(
        "ix_routing_rules_severity_value",
        "routing_rules",
        ["severity_value"],
        unique=False,
    )

    # Story 5.1 review — FR21 / AC #5: ``ROUTING_APPLIED`` must be at most
    # one row per ``run_id``. The application checks ``has_audit_event_for_run``
    # first, but two concurrent ``node_route`` invocations (orchestrator
    # retry + resume, or a race on resume) can pass the read before either
    # commits the write. This partial unique index closes that window at
    # the DB layer. Scoped to ``routing_applied`` so other append-only
    # actions (scout/normalize/classify completions) keep their existing
    # one-per-retry semantics untouched.
    op.create_index(
        "uq_audit_events_routing_applied_run_id",
        "audit_events",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("action = 'routing_applied'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_audit_events_routing_applied_run_id",
        table_name="audit_events",
        postgresql_where=sa.text("action = 'routing_applied'"),
    )
    op.drop_index("ix_routing_rules_severity_value", table_name="routing_rules")
    op.drop_index("ix_routing_rules_impact_category", table_name="routing_rules")
    op.drop_index("ix_routing_rules_rule_type_priority", table_name="routing_rules")
    op.drop_table("routing_rules")
