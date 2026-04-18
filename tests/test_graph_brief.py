"""Briefing node grouping and FR20 sections (Story 4.3)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from sentinel_prism.graph import new_pipeline_state
from sentinel_prism.graph.nodes.brief import _build_groups_json, _NormClsMember
from sentinel_prism.graph.nodes.brief import node_brief
from sentinel_prism.services.briefing.settings import BriefingGroupingSettings


def _norm(url: str, *, jurisdiction: str = "EU", doc: str = "guidance") -> dict:
    return {
        "source_id": str(uuid.uuid4()),
        "source_name": "S",
        "jurisdiction": jurisdiction,
        "item_url": url,
        "title": f"T-{url[-4:]}",
        "published_at": "2026-04-18T12:00:00+00:00",
        "document_type": doc,
        "body_snippet": "snippet",
        "summary": None,
    }


def _cls(
    url: str,
    *,
    severity: str = "medium",
    categories: list[str] | None = None,
) -> dict:
    return {
        "source_id": str(uuid.uuid4()),
        "item_url": url,
        "in_scope": True,
        "severity": severity,
        "impact_categories": categories or ["labeling"],
        "urgency": "informational",
        "rationale": f"why-{severity}",
        "confidence": 0.8,
        "needs_human_review": False,
        "rule_reasons": [],
    }


def test_build_groups_splits_by_severity_dimension() -> None:
    settings = BriefingGroupingSettings(
        dimensions=("severity",),
        date_bucket_granularity="day",
    )
    members = [
        _NormClsMember(_norm("https://a/1"), None, _cls("https://a/1", severity="high"), None),
        _NormClsMember(_norm("https://a/2"), None, _cls("https://a/2", severity="low"), None),
    ]
    groups = _build_groups_json(members, settings)
    assert len(groups) == 2
    for g in groups:
        sec = g["sections"]
        assert set(sec.keys()) == {
            "what_changed",
            "why_it_matters",
            "who_should_care",
            "confidence",
            "suggested_actions",
        }
        assert sec["what_changed"]
        assert sec["why_it_matters"]
        assert sec["who_should_care"]
        assert sec["confidence"]
        assert sec["suggested_actions"]


@pytest.mark.asyncio
async def test_node_brief_skips_when_no_in_scope_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BRIEFING_GROUPING_DIMENSIONS",
        '["jurisdiction","severity"]',
    )
    st = new_pipeline_state(uuid.uuid4())
    st["normalized_updates"] = [_norm("https://x/1")]
    st["classifications"] = [
        {
            **_cls("https://x/1"),
            "in_scope": False,
        }
    ]
    out = await node_brief(st)
    assert out == {}


@pytest.mark.asyncio
async def test_node_brief_persists_via_state_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "BRIEFING_GROUPING_DIMENSIONS",
        '["severity"]',
    )
    captured: dict[str, object] = {}

    async def fake_upsert(
        _session: object, **kwargs: object
    ) -> tuple[uuid.UUID, bool]:
        captured.update(kwargs)
        return uuid.uuid4(), True

    st = new_pipeline_state(uuid.uuid4())
    st["normalized_updates"] = [
        _norm("https://a/1", jurisdiction="US"),
        _norm("https://a/2", jurisdiction="US"),
    ]
    st["classifications"] = [
        _cls("https://a/1", severity="high"),
        _cls("https://a/2", severity="high"),
    ]

    with (
        patch(
            "sentinel_prism.graph.nodes.brief.briefings_repo.upsert_briefing_for_run",
            new_callable=AsyncMock,
            side_effect=fake_upsert,
        ),
        patch(
            "sentinel_prism.graph.nodes.brief.record_pipeline_audit_event",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        out = await node_brief(st)

    assert "briefings" in out
    groups = captured.get("groups")
    assert isinstance(groups, list) and len(groups) == 1
    g0 = groups[0]
    assert g0["dimensions"]["severity"] == "high"
    assert len(g0["members"]) == 2
    sec = g0["sections"]
    assert sec["confidence"] and sec["suggested_actions"]
