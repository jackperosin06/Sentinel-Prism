"""LangGraph retry policy on classify (Story 3.6)."""

from __future__ import annotations

import logging
import uuid
from unittest.mock import patch

import pytest

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from sentinel_prism.graph.nodes.classify import node_classify
from sentinel_prism.graph.retry import classify_node_retry_policy
from sentinel_prism.graph.state import AgentState, new_pipeline_state
from sentinel_prism.services.llm.classification import StructuredClassification
from sentinel_prism.services.llm.classification_retry import is_transient_classification_error
from sentinel_prism.services.llm.settings import (
    CLASSIFICATION_MAX_ATTEMPTS_LOWER_BOUND,
    CLASSIFICATION_MAX_ATTEMPTS_UPPER_BOUND,
    DEFAULT_CLASSIFICATION_MAX_ATTEMPTS,
    ClassificationRetrySettings,
    get_classification_retry_settings,
)


def _normalized_stub() -> dict:
    return {
        "source_id": str(uuid.uuid4()),
        "item_url": "https://n/retry",
        "jurisdiction": "EU",
        "document_type": "unknown",
        "title": "Label change",
        "summary": None,
        "body_snippet": None,
    }


def test_is_transient_classification_error_timeout_vs_runtime() -> None:
    assert is_transient_classification_error(TimeoutError("x")) is True
    assert is_transient_classification_error(ConnectionError("x")) is True
    assert is_transient_classification_error(RuntimeError("provider down")) is False


class TestClassificationRetrySettingsEnvParsing:
    """Env parsing + clamp behavior for ``get_classification_retry_settings``."""

    def test_unset_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SENTINEL_CLASSIFICATION_MAX_ATTEMPTS", raising=False)
        assert (
            get_classification_retry_settings().max_attempts
            == DEFAULT_CLASSIFICATION_MAX_ATTEMPTS
        )

    def test_empty_string_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SENTINEL_CLASSIFICATION_MAX_ATTEMPTS", "")
        assert (
            get_classification_retry_settings().max_attempts
            == DEFAULT_CLASSIFICATION_MAX_ATTEMPTS
        )

    def test_whitespace_around_integer_is_trimmed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SENTINEL_CLASSIFICATION_MAX_ATTEMPTS", "  5  ")
        assert get_classification_retry_settings().max_attempts == 5

    def test_non_integer_falls_back_and_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.WARNING)
        monkeypatch.setenv("SENTINEL_CLASSIFICATION_MAX_ATTEMPTS", "abc")

        assert (
            get_classification_retry_settings().max_attempts
            == DEFAULT_CLASSIFICATION_MAX_ATTEMPTS
        )

        parse_events = [
            r
            for r in caplog.records
            if getattr(r, "event", None) == "classification_max_attempts_parse_error"
        ]
        assert len(parse_events) == 1
        assert parse_events[0].ctx["raw"] == "abc"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("0", CLASSIFICATION_MAX_ATTEMPTS_LOWER_BOUND),
            ("1", CLASSIFICATION_MAX_ATTEMPTS_LOWER_BOUND),
            ("-5", CLASSIFICATION_MAX_ATTEMPTS_LOWER_BOUND),
            ("50", CLASSIFICATION_MAX_ATTEMPTS_UPPER_BOUND),
            ("999", CLASSIFICATION_MAX_ATTEMPTS_UPPER_BOUND),
        ],
    )
    def test_out_of_range_is_clamped_and_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        raw: str,
        expected: int,
    ) -> None:
        caplog.set_level(logging.WARNING)
        monkeypatch.setenv("SENTINEL_CLASSIFICATION_MAX_ATTEMPTS", raw)

        assert get_classification_retry_settings().max_attempts == expected

        clamp_events = [
            r
            for r in caplog.records
            if getattr(r, "event", None) == "classification_max_attempts_clamped"
        ]
        assert len(clamp_events) == 1
        assert clamp_events[0].ctx["clamped"] == expected

    @pytest.mark.parametrize("raw", ["2", "3", "7", "10"])
    def test_in_range_values_do_not_warn(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        raw: str,
    ) -> None:
        caplog.set_level(logging.WARNING)
        monkeypatch.setenv("SENTINEL_CLASSIFICATION_MAX_ATTEMPTS", raw)

        assert get_classification_retry_settings().max_attempts == int(raw)

        warn_events = [
            r
            for r in caplog.records
            if getattr(r, "event", None)
            in {
                "classification_max_attempts_parse_error",
                "classification_max_attempts_clamped",
            }
        ]
        assert warn_events == []


def _classify_only_graph(monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr(
        "sentinel_prism.graph.retry.get_classification_retry_settings",
        lambda: ClassificationRetrySettings(
            max_attempts=2,
            initial_interval=0.0,
            backoff_factor=1.0,
            max_interval=0.0,
            jitter=False,
        ),
    )
    builder: StateGraph = StateGraph(AgentState)
    builder.add_node(
        "classify",
        node_classify,
        retry_policy=classify_node_retry_policy(),
    )
    builder.add_edge(START, "classify")
    builder.add_edge("classify", END)
    return builder.compile(checkpointer=MemorySaver())


@pytest.mark.asyncio
async def test_classify_transient_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # INFO level so the successful-attempt ``graph_classify_llm_done`` event is
    # captured alongside the failing-attempt WARNING — AC #1 requires run_id
    # correlation across attempts in **structured logs**, not just state.
    caplog.set_level(logging.INFO)
    calls: list[int] = []

    class Flaky:
        model_id = "stub"

        async def classify(self, *_a: object, **_k: object) -> StructuredClassification:
            calls.append(1)
            if len(calls) < 2:
                raise ConnectionError("transient")
            return StructuredClassification(
                severity="medium",
                impact_categories=["labeling"],
                urgency="informational",
                rationale="ok_after_retry",
                confidence=0.85,
            )

    with patch(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        return_value=Flaky(),
    ):
        app = _classify_only_graph(monkeypatch)
        rid = str(uuid.uuid4())
        state = new_pipeline_state(rid)
        state["normalized_updates"] = [_normalized_stub()]
        result = await app.ainvoke(
            state,
            config={"configurable": {"thread_id": rid}},
        )

    assert result["run_id"] == rid
    assert len(calls) == 2
    assert len(result["classifications"]) == 1
    assert result["classifications"][0]["rationale"] == "ok_after_retry"
    transient_ev = [
        r
        for r in caplog.records
        if getattr(r, "event", None) == "graph_classify_llm_transient"
    ]
    assert len(transient_ev) == 1
    assert transient_ev[0].ctx["run_id"] == rid
    assert transient_ev[0].ctx["step"] == "classify"
    assert transient_ev[0].ctx["detail"] == "transient"

    # AC #1: the successful retry must share ``run_id`` with the failing attempt
    # in structured logs, proving LangGraph did not mint a new correlation id.
    done_ev = [
        r for r in caplog.records if getattr(r, "event", None) == "graph_classify_llm_done"
    ]
    assert len(done_ev) >= 1
    assert done_ev[-1].ctx["run_id"] == rid
    assert transient_ev[0].ctx["run_id"] == done_ev[-1].ctx["run_id"]


@pytest.mark.asyncio
async def test_classify_transient_exhausts_retry_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    class AlwaysDown:
        model_id = "stub"

        async def classify(self, *_a: object, **_k: object) -> StructuredClassification:
            calls.append(1)
            raise ConnectionError("still down")

    with patch(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        return_value=AlwaysDown(),
    ):
        app = _classify_only_graph(monkeypatch)
        rid = str(uuid.uuid4())
        state = new_pipeline_state(rid)
        state["normalized_updates"] = [_normalized_stub()]
        with pytest.raises(ConnectionError, match="still down"):
            await app.ainvoke(
                state,
                config={"configurable": {"thread_id": rid}},
            )

    # AC #2: retries must stop after ``max_attempts`` (``_classify_only_graph``
    # pins ``max_attempts=2``). Guarantees both that retry fired at least once
    # AND that it did not loop indefinitely.
    assert len(calls) == 2, (
        f"expected exactly 2 classify attempts (max_attempts=2), got {len(calls)}"
    )

    # AC #5 state hygiene: on raise, LangGraph discards partial node output, so
    # the checkpointer should not leak ``classifications`` or ``llm_trace``.
    snap = app.get_state(config={"configurable": {"thread_id": rid}})
    assert snap.values.get("run_id") == rid
    assert not snap.values.get("classifications")
    assert not snap.values.get("llm_trace")


@pytest.mark.asyncio
async def test_full_pipeline_classify_retry_preserves_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: regulatory graph wiring keeps thread_id correlation (FR38)."""

    monkeypatch.setattr(
        "sentinel_prism.graph.retry.get_classification_retry_settings",
        lambda: ClassificationRetrySettings(
            max_attempts=2,
            initial_interval=0.0,
            backoff_factor=1.0,
            max_interval=0.0,
            jitter=False,
        ),
    )
    from sentinel_prism.graph import compile_regulatory_pipeline_graph

    calls: list[int] = []

    class Flaky:
        model_id = "stub"

        async def classify(self, *_a: object, **_k: object) -> StructuredClassification:
            calls.append(1)
            if len(calls) < 2:
                raise ConnectionError("transient")
            return StructuredClassification(
                severity="medium",
                impact_categories=["labeling"],
                urgency="informational",
                rationale="ok",
                confidence=0.85,
            )

    with patch(
        "sentinel_prism.graph.nodes.classify.build_classification_llm",
        return_value=Flaky(),
    ):
        app = compile_regulatory_pipeline_graph()
        rid = str(uuid.uuid4())
        state = new_pipeline_state(rid)
        state["normalized_updates"] = [_normalized_stub()]
        state["flags"] = {"needs_human_review": False}
        result = await app.ainvoke(
            state,
            config={"configurable": {"thread_id": rid}},
        )

    assert result["run_id"] == rid
    assert len(calls) == 2
    # AC #5: ``classifications`` uses ``operator.add`` — the retried attempt
    # must not double-append the classified item from the successful attempt.
    assert len(result["classifications"]) == 1
    assert result["classifications"][0]["rationale"] == "ok"
