"""SQLAlchemy ORM models and shared metadata."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UserRole(StrEnum):
    """RBAC roles (Story 1.4). Stored as VARCHAR; no PostgreSQL native enum type."""

    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class SourceType(StrEnum):
    """Public regulatory source connector kind (Story 2.1)."""

    RSS = "rss"
    HTTP = "http"


class FallbackMode(StrEnum):
    """Alternate fetch when primary URL fails (Story 2.5 — FR5)."""

    NONE = "none"
    SAME_AS_PRIMARY = "same_as_primary"
    HTML_PAGE = "html_page"


class RoutingRuleType(StrEnum):
    """Mock routing rule discriminator (Story 5.1 — FR21)."""

    TOPIC = "topic"
    SEVERITY = "severity"


class PipelineAuditAction(StrEnum):
    """Append-only pipeline audit vocabulary (Story 3.8 — FR33 partial)."""

    PIPELINE_SCOUT_COMPLETED = "pipeline_scout_completed"
    PIPELINE_NORMALIZE_COMPLETED = "pipeline_normalize_completed"
    PIPELINE_CLASSIFY_COMPLETED = "pipeline_classify_completed"
    # Story 4.1 — emitted when `record_review_queue_projection` fails so the
    # interrupted run is still discoverable via audit search even when the
    # dedicated projection row could not be written.
    HUMAN_REVIEW_QUEUE_PROJECTION_FAILED = "human_review_queue_projection_failed"
    # Story 4.2 — analyst decisions (FR17); distinct actions for Epic 8 search.
    HUMAN_REVIEW_APPROVED = "human_review_approved"
    HUMAN_REVIEW_REJECTED = "human_review_rejected"
    HUMAN_REVIEW_OVERRIDDEN = "human_review_overridden"
    BRIEFING_GENERATED = "briefing_generated"
    ROUTING_APPLIED = "routing_applied"


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


metadata = Base.metadata


def _str_enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """SQLAlchemy ``Enum`` persists member *names* by default; DB check constraints use *values*."""

    return [m.value for m in enum_cls]


class User(Base):
    """Local user account (email + password) with RBAC role (Story 1.4)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            native_enum=False,
            length=32,
            values_callable=_str_enum_values,
        ),
        nullable=False,
        server_default=UserRole.VIEWER.value,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Source(Base):
    """Registered public ingestion source (Story 2.1 — FR1)."""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    jurisdiction: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(
            SourceType,
            native_enum=False,
            length=32,
            values_callable=_str_enum_values,
        ),
        nullable=False,
    )
    primary_url: Mapped[str] = mapped_column(Text(), nullable=False)
    fallback_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    fallback_mode: Mapped[FallbackMode] = mapped_column(
        Enum(
            FallbackMode,
            native_enum=False,
            length=32,
            values_callable=_str_enum_values,
        ),
        nullable=False,
        default=FallbackMode.NONE,
        server_default=FallbackMode.NONE.value,
    )
    schedule: Mapped[str] = mapped_column(String(512), nullable=False)
    poll_attempts_success: Mapped[int] = mapped_column(
        BigInteger(), nullable=False, default=0, server_default="0"
    )
    poll_attempts_failed: Mapped[int] = mapped_column(
        BigInteger(), nullable=False, default=0, server_default="0"
    )
    items_ingested_total: Mapped[int] = mapped_column(
        BigInteger(), nullable=False, default=0, server_default="0"
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_latency_ms: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    last_success_fetch_path: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # ``none_as_null=True`` so Python ``None`` maps to SQL NULL rather than JSON
    # ``'null'``. Without this, ``record_poll_failure``'s ``coalesce(extra_metadata,
    # '{}'::jsonb) || patch`` would see JSONB ``null`` (coalesce only masks SQL NULL)
    # and the ``||`` operator would promote both sides to single-element arrays,
    # yielding ``[null, {...}]`` instead of a merged object (breaks the dict contract
    # exposed via ``SourceResponse.extra_metadata``).
    extra_metadata: Mapped[dict | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SourceIngestedFingerprint(Base):
    """Lightweight idempotency ledger for connector output (Story 2.4 — FR3)."""

    __tablename__ = "source_ingested_fingerprints"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "fingerprint",
            name="uq_source_ingested_fingerprint_source_fingerprint",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        # No separate index: the UNIQUE (source_id, fingerprint) constraint creates
        # a covering index usable for prefix scans on source_id. A redundant B-tree
        # index would add write overhead on every INSERT.
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    item_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RawCapture(Base):
    """Durable scout output before normalization (Story 3.1 — FR7).

    ``payload`` JSONB holds a serialized ``ScoutRawItem``-compatible dict (reconstructible).
    ``sources`` row deletion is RESTRICTed so audit captures are not silently removed.
    Optional ``run_id`` reserved for LangGraph correlation (Story 3.2+); unused until then.
    """

    __tablename__ = "raw_captures"
    __table_args__ = (Index("ix_raw_captures_source_captured", "source_id", "captured_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    item_url: Mapped[str] = mapped_column(Text(), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NormalizedUpdateRow(Base):
    """Normalized regulatory update derived from a raw capture (Story 3.1 — FR8, FR10).

    ``raw_capture_id`` is UNIQUE: spec AC4 pins a one-to-one link raw→normalized for MVP.
    ON DELETE RESTRICT on both FKs keeps the audit chain intact.

    ``source_name`` and ``jurisdiction`` are **point-in-time denormalized snapshots**
    taken when the capture was normalized; a later admin rename/re-jurisdiction of
    ``sources`` will NOT back-propagate, intentionally preserving audit fidelity.

    ``body_snippet`` is an excerpt (typically first 2–3 sentences) as produced by
    ``ScoutRawItem.body_snippet``; full-body extraction lands in Story 3.3+.

    ``parser_confidence`` / ``extraction_quality`` hold MVP heuristic floats in
    ``[0, 0.95]`` (the heuristic weights cap at 0.95 by design; the 1.0 headroom is
    reserved for future non-MVP pipelines). Both columns are nullable so future
    non-heuristic paths may legitimately omit one or both.
    """

    __tablename__ = "normalized_updates"
    __table_args__ = (
        Index("ix_normalized_updates_source_created", "source_id", "created_at"),
        UniqueConstraint(
            "raw_capture_id", name="uq_normalized_updates_raw_capture_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    raw_capture_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("raw_captures.id", ondelete="RESTRICT"),
        nullable=False,
        # No separate index: UNIQUE (raw_capture_id) creates a covering B-tree.
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_name: Mapped[str] = mapped_column(String(512), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(Text(), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    item_url: Mapped[str] = mapped_column(Text(), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    body_snippet: Mapped[str | None] = mapped_column(Text(), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    # ``none_as_null=True`` matches ``Source.extra_metadata`` — a missing metadata
    # payload is SQL NULL, never JSON ``'null'`` (the latter would silently poison
    # any future JSONB merge with ``||``).
    extra_metadata: Mapped[dict | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    parser_confidence: Mapped[float | None] = mapped_column(Float(), nullable=True)
    extraction_quality: Mapped[float | None] = mapped_column(Float(), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReviewQueueItem(Base):
    """Durable projection of runs waiting at ``human_review_gate`` (Story 4.1).

    Upserted immediately before LangGraph ``interrupt()`` so analysts can list
    the queue via SQL. Checkpoint state remains the source of truth for full
    ``AgentState``; Story 4.2 removes or updates rows when the graph resumes.
    """

    __tablename__ = "review_queue_items"
    __table_args__ = (Index("ix_review_queue_items_queued_at", "queued_at"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, nullable=False
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=True,
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    items_summary: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(none_as_null=True), nullable=False, server_default=text("'[]'::jsonb")
    )


class RoutingRule(Base):
    """Mock routing table rows: topic (impact category) or severity → team/channel (Story 5.1).

    **Precedence (application code, not DB):** topic rules assign
    ``team_slug`` and ``channel_slug``. Severity rules always set
    ``channel_slug``; when **no topic rule matched**, a severity rule also
    backfills ``team_slug`` so severity-only routing still produces a usable
    ``(team_slug, channel_slug)`` pair. When a topic rule matched, the
    severity rule overrides ``channel_slug`` only and leaves ``team_slug``
    untouched. Within each rule type, lower ``priority`` values are tried
    first and ties break deterministically on ``id`` (see resolver /
    repository ordering).

    The DB CHECK constraints enforce that matching key columns
    (``impact_category`` / ``severity_value``) are stored pre-normalized
    (trimmed, lower-cased, non-empty) so the application-side normalization
    in :mod:`sentinel_prism.services.routing.resolve` cannot silently alias
    case/whitespace variants of the same logical rule.
    """

    __tablename__ = "routing_rules"
    __table_args__ = (
        CheckConstraint(
            "(rule_type = 'topic' AND impact_category IS NOT NULL AND severity_value IS NULL) "
            "OR (rule_type = 'severity' AND severity_value IS NOT NULL AND impact_category IS NULL)",
            name="ck_routing_rules_topic_xor_severity",
        ),
        CheckConstraint(
            "impact_category IS NULL OR ("
            "impact_category = lower(impact_category) "
            "AND impact_category = trim(impact_category) "
            "AND length(impact_category) > 0)",
            name="ck_routing_rules_impact_category_normalized",
        ),
        CheckConstraint(
            "severity_value IS NULL OR ("
            "severity_value = lower(severity_value) "
            "AND severity_value = trim(severity_value) "
            "AND length(severity_value) > 0)",
            name="ck_routing_rules_severity_value_normalized",
        ),
        Index("ix_routing_rules_rule_type_priority", "rule_type", "priority"),
        Index("ix_routing_rules_impact_category", "impact_category"),
        Index("ix_routing_rules_severity_value", "severity_value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    priority: Mapped[int] = mapped_column(Integer(), nullable=False)
    rule_type: Mapped[RoutingRuleType] = mapped_column(
        Enum(
            RoutingRuleType,
            native_enum=False,
            length=16,
            values_callable=_str_enum_values,
        ),
        nullable=False,
    )
    impact_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    severity_value: Mapped[str | None] = mapped_column(String(32), nullable=True)
    team_slug: Mapped[str] = mapped_column(String(128), nullable=False)
    channel_slug: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Briefing(Base):
    """Persisted regulatory briefing for a pipeline run (Story 4.3 — FR18–FR20).

    At most one row per ``run_id`` (upserted from ``node_brief``) so LangGraph
    retries or duplicate tail execution do not multiply briefings.
    """

    __tablename__ = "briefings"
    __table_args__ = (
        Index("ix_briefings_created_at", "created_at"),
        UniqueConstraint("run_id", name="uq_briefings_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    grouping_dimensions: Mapped[list[str]] = mapped_column(
        JSONB(none_as_null=True), nullable=False, server_default=text("'[]'::jsonb")
    )
    groups: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(none_as_null=True), nullable=False, server_default=text("'[]'::jsonb")
    )


class AuditEvent(Base):
    """Operator-queryable pipeline audit row (Story 3.8 — Architecture §3.5).

    Append-only in application code: INSERT via
    :func:`~sentinel_prism.db.repositories.audit_events.append_audit_event` only;
    no ORM update/delete helpers. ``metadata`` must stay non-secret (counts,
    flags, bounded samples) — never raw captures, prompts, or credentials (NFR12).
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_run_id_created_at", "run_id", "created_at"),
        Index("ix_audit_events_action", "action"),
        Index("ix_audit_events_source_id", "source_id"),
        # Story 5.1 FR21 / AC #5: ``ROUTING_APPLIED`` must be at most one
        # row per ``run_id``. The application-side
        # :func:`sentinel_prism.db.repositories.audit_events.has_audit_event_for_run`
        # gate still fires for observability (audit row is skipped rather
        # than written-and-rejected on the happy path), but the partial
        # unique index is the hard guarantee against the read-then-write
        # TOCTOU between two concurrent ``node_route`` invocations on the
        # same run. Scoped to ``routing_applied`` so other actions keep
        # their existing append-only semantics (scout/normalize/classify
        # intentionally emit one completion event per retry).
        Index(
            "uq_audit_events_routing_applied_run_id",
            "run_id",
            unique=True,
            postgresql_where=text("action = 'routing_applied'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[PipelineAuditAction] = mapped_column(
        Enum(
            PipelineAuditAction,
            native_enum=False,
            length=64,
            values_callable=_str_enum_values,
        ),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_metadata: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB(none_as_null=True),
        nullable=True,
    )
