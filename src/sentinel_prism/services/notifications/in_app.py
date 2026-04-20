"""Enqueue in-app notifications from routing decisions (Story 5.2 — FR24).

**Scope and semantics (code-review 2026-04-20):**

* Severity gate: only severities in :data:`IN_APP_ALLOWED_SEVERITIES` enqueue
  rows. MVP ships ``{"critical"}``. Any other severity is skipped with an
  INFO-level log event so upstream taxonomy drift (new severity labels) is
  observable in operator logs.
* Team targeting: ``team_slug`` is looked up case-insensitively against
  ``users.team_slug``; the **original** (pre-lowercase) casing from the
  routing decision is persisted on the notification row so audit/UI reflect
  the rule-author's canonical slug. The lowercase form exists only as a
  query key inside this module.
* Snapshot-at-delivery semantics: once a row is inserted, its ``team_slug``
  is **not** re-evaluated on later user team changes. A user who leaves a
  team keeps previously-delivered notifications; new notifications require
  current membership. This is intentional for audit fidelity — moving to
  read-time membership filtering belongs in a later story.
* Idempotency: DB unique constraint on ``(run_id, item_url, user_id)``.
  Graph retries that re-call this function with the same decisions produce
  zero new rows; the replay is surfaced via a ``delivery_events`` entry
  with ``status="no_new_rows"`` so the audit trail distinguishes "not
  considered" from "considered, all duplicates".
* Transient failure replay (**limitation**): ``node_route`` dedupes against
  ``state["routing_decisions"]`` by ``item_url``, so a transient enqueue
  failure on the first pass **will not** be retried automatically — the
  URLs are already in state and will be filtered out. Failures are surfaced
  via ``errors[]`` so operators can replay from audit. A dedicated
  delivery-retry ledger is out of scope for Story 5.2.
* Transaction model: all inserts for a single call share one transaction
  committed once at the end. Per-user inserts run inside ``SAVEPOINT`` so a
  single FK-violation (e.g., user deleted between ``list_user_ids_for_team_slug``
  and insert) does not roll back the entire batch.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sentinel_prism.db.repositories import in_app_notifications as in_app_repo

logger = logging.getLogger(__name__)

# Story 5.2 AC #3 — the epic gates in-app delivery to ``critical`` routed items.
# Kept as a set so adding a policy flag (e.g., promoting ``high`` under a config
# flag) is a one-line change and the ``not in`` check is O(1).
IN_APP_ALLOWED_SEVERITIES: frozenset[str] = frozenset({"critical"})

# Backward-compat alias for existing imports (tests). Prefer
# :data:`IN_APP_ALLOWED_SEVERITIES` for new code.
IN_APP_MIN_SEVERITY: str = "critical"

SessionMaker = async_sessionmaker[AsyncSession]

# Bound the text fields we persist so a pathological upstream URL or summary
# cannot blow up individual rows (and the subsequent list response).
_MAX_ITEM_URL_CHARS = 2048
_MAX_BODY_CHARS = 2048


def _norm_item_url(raw: Any) -> str:
    return str(raw or "").strip()


def _safe_error_detail(exc: BaseException, *, limit: int = 200) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _truncate(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


async def enqueue_critical_in_app_for_decisions(
    *,
    session_factory: SessionMaker,
    run_id: str,
    decisions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Persist inbox rows for **critical** matched decisions with a ``team_slug``.

    Returns ``(delivery_events, errors)`` — errors are non-fatal; routing
    decisions remain valid even when enqueue partially fails. See module
    docstring for full semantics (severity gate, snapshot-at-delivery,
    idempotency, retry behavior).
    """

    delivery_events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    rid_raw = str(run_id).strip()
    try:
        rid = uuid.UUID(rid_raw)
    except (ValueError, TypeError):
        return (
            [],
            [
                {
                    "step": "in_app_notifications",
                    "message": "invalid_run_id",
                    "error_class": "ValueError",
                    "detail": "run_id is not a valid UUID",
                }
            ],
        )

    inserted_total = 0
    committed = False
    eligible_decisions = 0
    try:
        async with session_factory() as session:
            for d in decisions:
                if not isinstance(d, dict):
                    continue
                if not d.get("matched"):
                    continue
                sev_raw = str(d.get("severity") or "").strip().lower()
                if sev_raw not in IN_APP_ALLOWED_SEVERITIES:
                    # P20 — surface severity-filter skips so upstream taxonomy
                    # drift is detectable in operator logs (otherwise a new
                    # severity label would silently disable in-app routing).
                    if sev_raw:
                        logger.info(
                            "in_app_notifications",
                            extra={
                                "event": "in_app_severity_skipped",
                                "ctx": {
                                    "run_id": str(rid),
                                    "severity": sev_raw,
                                },
                            },
                        )
                    continue
                ts = d.get("team_slug")
                if ts is None or not str(ts).strip():
                    continue
                team_slug_canonical = str(ts).strip()
                team_slug_key = team_slug_canonical.lower()
                url = _truncate(
                    _norm_item_url(d.get("item_url")), limit=_MAX_ITEM_URL_CHARS
                )
                if not url:
                    continue

                user_ids = await in_app_repo.list_user_ids_for_team_slug(
                    session, team_slug=team_slug_key
                )
                if not user_ids:
                    # P11 — zero matching recipients for a critical item is a
                    # pager-worthy silent-drop condition. Surface via structured
                    # log and a non-fatal errors[] envelope (routing decision
                    # itself is still valid — this only reports that enqueue
                    # had nowhere to deliver).
                    logger.warning(
                        "in_app_notifications",
                        extra={
                            "event": "in_app_no_recipients",
                            "ctx": {
                                "run_id": str(rid),
                                "team_slug": team_slug_canonical,
                                "severity": sev_raw,
                            },
                        },
                    )
                    errors.append(
                        {
                            "step": "in_app_notifications",
                            "message": "in_app_no_recipients",
                            "error_class": "NoRecipients",
                            "detail": f"team_slug={team_slug_canonical}",
                        }
                    )
                    continue

                # Only count decisions where we actually attempt inserts as
                # "eligible" — a team with zero members is reported via the
                # errors[] envelope above, not as a "no_new_rows" replay.
                eligible_decisions += 1

                title = "Critical routed update"
                body = _truncate(url, limit=_MAX_BODY_CHARS)
                for uid in user_ids:
                    # P3 — wrap each per-user insert in a SAVEPOINT so an
                    # FK-violation from a concurrent user deletion does not
                    # roll back the whole batch. ``begin_nested()`` issues
                    # ``SAVEPOINT`` on enter and ``RELEASE``/``ROLLBACK TO``
                    # on exit; the outer transaction continues.
                    try:
                        async with session.begin_nested():
                            ok = await in_app_repo.insert_notification_ignore_conflict(
                                session,
                                user_id=uid,
                                run_id=rid,
                                item_url=url,
                                team_slug=team_slug_canonical,
                                severity=sev_raw,
                                title=title,
                                body=body,
                            )
                    except SQLAlchemyError as exc:
                        logger.warning(
                            "in_app_notifications",
                            extra={
                                "event": "in_app_insert_skipped",
                                "ctx": {
                                    "run_id": str(rid),
                                    "team_slug": team_slug_canonical,
                                    "error_class": type(exc).__name__,
                                },
                            },
                        )
                        errors.append(
                            {
                                "step": "in_app_notifications",
                                "message": "in_app_insert_skipped",
                                "error_class": type(exc).__name__,
                                "detail": _safe_error_detail(exc),
                            }
                        )
                        continue
                    if ok:
                        inserted_total += 1
            await session.commit()
            committed = True
    except SQLAlchemyError as exc:
        logger.warning(
            "in_app_notifications",
            extra={"event": "in_app_enqueue_failed", "error_class": type(exc).__name__},
        )
        errors.append(
            {
                "step": "in_app_notifications",
                "message": "in_app_enqueue_persist_failed",
                "error_class": type(exc).__name__,
                "detail": _safe_error_detail(exc),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "in_app_notifications",
            extra={"event": "in_app_enqueue_failed", "error_class": type(exc).__name__},
        )
        errors.append(
            {
                "step": "in_app_notifications",
                "message": "in_app_enqueue_failed",
                "error_class": type(exc).__name__,
                "detail": _safe_error_detail(exc),
            }
        )

    # P1 — only emit ``delivery_events`` claiming persistence after the
    # commit succeeded. If commit raises, ``committed`` stays ``False`` and
    # the audit trail does not falsely report rows as "recorded".
    if committed:
        if inserted_total:
            delivery_events.append(
                {
                    "channel": "in_app",
                    "status": "recorded",
                    "run_id": str(rid),
                    "rows_inserted": inserted_total,
                }
            )
        elif eligible_decisions:
            # P34 — replay / all-duplicate path: we considered at least one
            # eligible critical decision but wrote no new rows (the unique
            # constraint caught them). Emit an explicit marker so the audit
            # trail distinguishes this from "never ran".
            delivery_events.append(
                {
                    "channel": "in_app",
                    "status": "no_new_rows",
                    "run_id": str(rid),
                    "rows_inserted": 0,
                }
            )
    return delivery_events, errors
