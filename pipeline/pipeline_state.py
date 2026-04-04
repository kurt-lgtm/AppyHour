"""Pipeline state machine. React equivalent: TypeScript enum + reducer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class PipelineStage(Enum):
    """Forward-only pipeline stages. Values must be sequential integers."""

    IDLE = 0
    INVENTORY_SYNCED = 1
    PASS1_COMPLETE = 2
    PASS2_COMPLETE = 3
    MATRIX_GENERATED = 4
    GIFTS_MERGED = 5
    FINALIZED = 6


class InvalidTransitionError(Exception):
    """Raised when attempting an illegal backward or same-stage transition."""


@dataclass
class PassProgress:
    """Per-order tracking for a single pipeline pass."""

    succeeded: list[str] = field(default_factory=list)  # order IDs
    failed: list[str] = field(default_factory=list)  # order IDs
    skipped: list[str] = field(default_factory=list)  # order IDs
    commit_pending: list[str] = field(default_factory=list)  # order IDs awaiting commit confirmation
    errors: dict[str, str] = field(default_factory=dict)  # order ID -> error message

    def to_dict(self) -> dict:
        """Serialize to plain JSON-safe dict."""
        return {
            "succeeded": list(self.succeeded),
            "failed": list(self.failed),
            "skipped": list(self.skipped),
            "commit_pending": list(self.commit_pending),
            "errors": dict(self.errors),
        }

    @classmethod
    def from_dict(cls, d: dict) -> PassProgress:
        """Deserialize from dict. Missing keys default to empty (backward-compatible with old checkpoints)."""
        return cls(
            succeeded=list(d.get("succeeded", [])),
            failed=list(d.get("failed", [])),
            skipped=list(d.get("skipped", [])),
            commit_pending=list(d.get("commit_pending", [])),
            errors=dict(d.get("errors", {})),
        )


def _active_prefixes(pass_number: int) -> tuple[str, ...]:
    """Return the SKU prefixes active for a given pass number.

    Pass 1: PR-CJAM only (cheese jam pairings go on orders first).
    Pass 2: Standard food/packaging prefixes — PR-CJAM already present.

    Used by cmd_sync to filter which SKUs are processed per pass (D-06, D-08).
    """
    if pass_number == 1:
        return ("PR-CJAM",)
    return ("CH-", "MT-", "AC-", "PK-", "TR-")


@dataclass
class PipelineState:
    """Immutable pipeline state snapshot. Advance with advance() — never mutate directly."""

    pipeline_id: str  # e.g. "2026-04-05-saturday"
    stage: PipelineStage = PipelineStage.IDLE
    pass1: PassProgress = field(default_factory=PassProgress)
    pass2: PassProgress = field(default_factory=PassProgress)
    matrix_path: str | None = None
    final_xlsx_path: str | None = None
    started_at: str = ""
    updated_at: str = ""
    dry_run: bool = True

    def advance(self, to: PipelineStage) -> PipelineState:
        """Return a new PipelineState advanced to `to`.

        Raises InvalidTransitionError if the transition is backward, same-stage,
        or skips a stage. Only one-step-forward transitions are allowed.
        """
        if to.value != self.stage.value + 1:
            raise InvalidTransitionError(
                f"Cannot transition from {self.stage.name} to {to.name}. "
                f"Expected next stage value {self.stage.value + 1}, got {to.value}."
            )
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        from dataclasses import replace

        return replace(self, stage=to, updated_at=now)

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict. stage is stored as its name string."""
        return {
            "pipeline_id": self.pipeline_id,
            "stage": self.stage.name,
            "pass1": self.pass1.to_dict(),
            "pass2": self.pass2.to_dict(),
            "matrix_path": self.matrix_path,
            "final_xlsx_path": self.final_xlsx_path,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "dry_run": self.dry_run,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PipelineState:
        """Deserialize from dict produced by to_dict()."""
        return cls(
            pipeline_id=d["pipeline_id"],
            stage=PipelineStage[d["stage"]],
            pass1=PassProgress.from_dict(d.get("pass1", {})),
            pass2=PassProgress.from_dict(d.get("pass2", {})),
            matrix_path=d.get("matrix_path"),
            final_xlsx_path=d.get("final_xlsx_path"),
            started_at=d.get("started_at", ""),
            updated_at=d.get("updated_at", ""),
            dry_run=d.get("dry_run", True),
        )
