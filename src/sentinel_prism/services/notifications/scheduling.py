"""Orchestrate immediate vs digest notification paths (Story 5.4 — FR22)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sentinel_prism.services.notifications.digest_flush import enqueue_digest_decisions
from sentinel_prism.services.notifications.external import enqueue_external_for_decisions
from sentinel_prism.services.notifications.in_app import enqueue_critical_in_app_for_decisions
from sentinel_prism.services.notifications.notification_policy import (
    NotificationPolicySettings,
    load_notification_policy,
)

logger = logging.getLogger(__name__)

SessionMaker = async_sessionmaker[AsyncSession]

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def split_decisions_for_policy(
    decisions: list[dict[str, Any]],
    policy: NotificationPolicySettings,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition matched decisions into immediate vs digest by severity."""

    immediate: list[dict[str, Any]] = []
    digest: list[dict[str, Any]] = []
    for d in decisions:
        if not isinstance(d, dict) or not d.get("matched"):
            continue
        sev_raw = str(d.get("severity") or "").strip().lower()
        if not sev_raw:
            continue
        ts = d.get("team_slug")
        if ts is None or not str(ts).strip():
            continue
        url = str(d.get("item_url") or "").strip()
        if not url:
            continue
        if sev_raw in policy.immediate_severities:
            immediate.append(d)
        else:
            digest.append(d)
    return immediate, digest


async def process_routed_notification_deliveries(
    *,
    session_factory: SessionMaker,
    run_id: str,
    decisions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split by policy; enqueue immediate in-app + capped external; optional digest queue."""
    delivery_events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        policy = load_notification_policy()

        valid_decisions: list[dict[str, Any]] = []
        malformed_matched = 0
        for d in decisions:
            if not isinstance(d, dict) or not d.get("matched"):
                continue
            sev_raw = str(d.get("severity") or "").strip().lower()
            ts = d.get("team_slug")
            url = str(d.get("item_url") or "").strip()
            if not sev_raw or ts is None or not str(ts).strip() or not url:
                malformed_matched += 1
                continue
            valid_decisions.append(d)

        immediate, digest = split_decisions_for_policy(valid_decisions, policy)

        if malformed_matched:
            logger.warning(
                "notification_scheduling",
                extra={
                    "event": "notification_decisions_malformed",
                    "ctx": {"run_id": run_id, "count": malformed_matched},
                },
            )
            errors.append(
                {
                    "step": "notification_scheduling",
                    "message": "notification_decisions_malformed",
                    "error_class": "InvalidDecision",
                    "detail": f"count={malformed_matched}",
                }
            )

        if immediate:
            immediate = sorted(
                immediate,
                key=lambda d: (
                    _SEVERITY_ORDER.get(str(d.get("severity") or "").strip().lower(), 99),
                    str(d.get("item_url") or ""),
                ),
            )
            dev, err = await enqueue_critical_in_app_for_decisions(
                session_factory=session_factory,
                run_id=run_id,
                decisions=immediate,
            )
            delivery_events.extend(dev)
            errors.extend(err)

            ext_slice = immediate[: policy.max_external_immediate_per_run]
            if len(ext_slice) < len(immediate):
                truncated = len(immediate) - len(ext_slice)
                logger.warning(
                    "external_notifications",
                    extra={
                        "event": "external_immediate_truncated",
                        "ctx": {
                            "run_id": run_id,
                            "total": len(immediate),
                            "cap": policy.max_external_immediate_per_run,
                            "truncated": truncated,
                        },
                    },
                )
                errors.append(
                    {
                        "step": "external_notifications",
                        "message": "external_immediate_truncated",
                        "error_class": "BudgetExceeded",
                        "detail": f"truncated={truncated}, cap={policy.max_external_immediate_per_run}",
                    }
                )
                delivery_events.append(
                    {
                        "channel": "external_budget",
                        "status": "truncated",
                        "run_id": run_id,
                        "truncated": truncated,
                    }
                )
            ext_dev, ext_err = await enqueue_external_for_decisions(
                session_factory=session_factory,
                run_id=run_id,
                decisions=ext_slice,
            )
            delivery_events.extend(ext_dev)
            errors.extend(ext_err)

        if digest:
            if policy.digest_enabled:
                d_ev, d_err = await enqueue_digest_decisions(
                    session_factory=session_factory,
                    run_id=run_id,
                    decisions=digest,
                )
                delivery_events.extend(d_ev)
                errors.extend(d_err)
            else:
                logger.info(
                    "digest_enqueue",
                    extra={
                        "event": "digest_disabled_skipped",
                        "ctx": {"run_id": run_id, "count": len(digest)},
                    },
                )
                errors.append(
                    {
                        "step": "digest_enqueue",
                        "message": "digest_disabled_skipped",
                        "error_class": "DigestDisabled",
                        "detail": f"count={len(digest)}",
                    }
                )
                delivery_events.append(
                    {
                        "channel": "digest_queue",
                        "status": "skipped_disabled",
                        "run_id": run_id,
                        "rows": len(digest),
                    }
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "notification_scheduling",
            extra={
                "event": "notification_scheduling_unhandled",
                "ctx": {"run_id": run_id, "error_class": type(exc).__name__},
            },
        )
        errors.append(
            {
                "step": "notification_scheduling",
                "message": "notification_scheduling_unhandled",
                "error_class": type(exc).__name__,
                "detail": str(exc)[:200],
            }
        )

    return delivery_events, errors
