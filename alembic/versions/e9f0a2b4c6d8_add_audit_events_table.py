"""Add audit_events for pipeline domain audit (Story 3.8 — FR33 partial).

Revision ID: e9f0a2b4c6d8
Revises: d8e9f0a1b2c3
Create Date: 2026-04-18

FK notes:
- audit_events.source_id → sources.id ON DELETE RESTRICT (align with raw_captures).
- audit_events.actor_user_id → users.id ON DELETE SET NULL (automated rows use NULL).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e9f0a2b4c6d8"
down_revision: Union[str, Sequence[str], None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_events_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_audit_events_source_id_sources"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_run_id_created_at",
        "audit_events",
        ["run_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_action",
        "audit_events",
        ["action"],
        unique=False,
    )
    op.create_index(
        "ix_audit_events_source_id",
        "audit_events",
        ["source_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop ``audit_events`` and its indexes.

    WARNING — LOSSY ON POPULATED DATABASES:
    ``audit_events`` is the append-only forensic trail for pipeline steps
    (FR33 partial, Architecture §3.5). ``op.drop_table`` permanently destroys
    every historical row. Before running ``alembic downgrade`` in any
    environment that has ingested data, operators MUST export the table
    (e.g. ``pg_dump -t audit_events``) or accept the loss. There is no
    application-level recovery path once this migration is reversed.
    """

    op.drop_index("ix_audit_events_source_id", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_index("ix_audit_events_run_id_created_at", table_name="audit_events")
    op.drop_table("audit_events")
