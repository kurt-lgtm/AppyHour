"""Tests for PipelineState dataclass and PipelineStage forward-only state machine."""

from __future__ import annotations

import pytest

from pipeline.pipeline_state import InvalidTransitionError, PassProgress, PipelineStage, PipelineState


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def idle_state() -> PipelineState:
    return PipelineState(pipeline_id="2026-04-05-saturday")


# ── Forward transitions ───────────────────────────────────────────────────────


def test_advance_idle_to_inventory_synced(idle_state: PipelineState) -> None:
    new_state = idle_state.advance(PipelineStage.INVENTORY_SYNCED)
    assert new_state.stage == PipelineStage.INVENTORY_SYNCED


def test_full_forward_sequence(idle_state: PipelineState) -> None:
    stages = [
        PipelineStage.INVENTORY_SYNCED,
        PipelineStage.PASS1_COMPLETE,
        PipelineStage.PASS2_COMPLETE,
        PipelineStage.MATRIX_GENERATED,
        PipelineStage.GIFTS_MERGED,
        PipelineStage.FINALIZED,
    ]
    state = idle_state
    for stage in stages:
        state = state.advance(stage)
        assert state.stage == stage


# ── Illegal transitions raise InvalidTransitionError ─────────────────────────


def test_skip_stage_raises(idle_state: PipelineState) -> None:
    """IDLE -> PASS1_COMPLETE skips INVENTORY_SYNCED — illegal."""
    with pytest.raises(InvalidTransitionError):
        idle_state.advance(PipelineStage.PASS1_COMPLETE)


def test_backward_transition_raises() -> None:
    """PASS1_COMPLETE -> IDLE is backward — illegal."""
    state = PipelineState(pipeline_id="x", stage=PipelineStage.PASS1_COMPLETE)
    with pytest.raises(InvalidTransitionError):
        state.advance(PipelineStage.IDLE)


def test_same_stage_transition_raises(idle_state: PipelineState) -> None:
    """Advancing to the same stage is illegal."""
    state = PipelineState(pipeline_id="x", stage=PipelineStage.PASS1_COMPLETE)
    with pytest.raises(InvalidTransitionError):
        state.advance(PipelineStage.PASS1_COMPLETE)


# ── Immutability ──────────────────────────────────────────────────────────────


def test_advance_returns_new_instance(idle_state: PipelineState) -> None:
    new_state = idle_state.advance(PipelineStage.INVENTORY_SYNCED)
    assert new_state is not idle_state
    assert idle_state.stage == PipelineStage.IDLE  # original unchanged


def test_advance_updates_updated_at(idle_state: PipelineState) -> None:
    new_state = idle_state.advance(PipelineStage.INVENTORY_SYNCED)
    assert new_state.updated_at != ""


# ── Serialization round-trip ──────────────────────────────────────────────────


def test_to_dict_from_dict_round_trip(idle_state: PipelineState) -> None:
    state = idle_state.advance(PipelineStage.INVENTORY_SYNCED)
    d = state.to_dict()
    restored = PipelineState.from_dict(d)
    assert restored.pipeline_id == state.pipeline_id
    assert restored.stage == state.stage
    assert restored.dry_run == state.dry_run
    assert restored.updated_at == state.updated_at


def test_pass_progress_survives_serialization() -> None:
    state = PipelineState(
        pipeline_id="x",
        stage=PipelineStage.PASS1_COMPLETE,
        pass1=PassProgress(
            succeeded=["1001", "1002"],
            failed=["1003"],
            skipped=["1004"],
        ),
    )
    d = state.to_dict()
    restored = PipelineState.from_dict(d)
    assert restored.pass1.succeeded == ["1001", "1002"]
    assert restored.pass1.failed == ["1003"]
    assert restored.pass1.skipped == ["1004"]


def test_stage_serialized_as_name() -> None:
    state = PipelineState(pipeline_id="x", stage=PipelineStage.PASS2_COMPLETE)
    d = state.to_dict()
    assert d["stage"] == "PASS2_COMPLETE"


def test_optional_paths_survive_round_trip() -> None:
    state = PipelineState(
        pipeline_id="x",
        stage=PipelineStage.MATRIX_GENERATED,
        matrix_path="/tmp/matrix.xlsx",
        final_xlsx_path=None,
    )
    restored = PipelineState.from_dict(state.to_dict())
    assert restored.matrix_path == "/tmp/matrix.xlsx"
    assert restored.final_xlsx_path is None


def test_dry_run_defaults_true(idle_state: PipelineState) -> None:
    assert idle_state.dry_run is True


# ── PassProgress: commit_pending and errors fields ────────────────────────────


def test_pass_progress_defaults_commit_pending_empty() -> None:
    p = PassProgress()
    assert p.commit_pending == []


def test_pass_progress_defaults_errors_empty() -> None:
    p = PassProgress()
    assert p.errors == {}


def test_pass_progress_to_dict_includes_commit_pending() -> None:
    p = PassProgress(commit_pending=["1001", "1002"])
    d = p.to_dict()
    assert "commit_pending" in d
    assert d["commit_pending"] == ["1001", "1002"]


def test_pass_progress_to_dict_includes_errors() -> None:
    p = PassProgress(errors={"1003": "rate limit exceeded"})
    d = p.to_dict()
    assert "errors" in d
    assert d["errors"] == {"1003": "rate limit exceeded"}


def test_pass_progress_from_dict_missing_keys_defaults() -> None:
    """Old checkpoint without new fields deserializes to empty defaults."""
    old = {"succeeded": ["1001"], "failed": [], "skipped": []}
    p = PassProgress.from_dict(old)
    assert p.commit_pending == []
    assert p.errors == {}


def test_pass_progress_round_trip_with_new_fields() -> None:
    original = PassProgress(
        succeeded=["1001"],
        failed=["1002"],
        skipped=["1003"],
        commit_pending=["1004", "1005"],
        errors={"1006": "timeout", "1007": "not found"},
    )
    restored = PassProgress.from_dict(original.to_dict())
    assert restored.succeeded == ["1001"]
    assert restored.failed == ["1002"]
    assert restored.skipped == ["1003"]
    assert restored.commit_pending == ["1004", "1005"]
    assert restored.errors == {"1006": "timeout", "1007": "not found"}


def test_pass_progress_existing_fields_unaffected() -> None:
    p = PassProgress(succeeded=["a"], failed=["b"], skipped=["c"])
    d = p.to_dict()
    assert d["succeeded"] == ["a"]
    assert d["failed"] == ["b"]
    assert d["skipped"] == ["c"]


# ── _active_prefixes() helper ─────────────────────────────────────────────────
