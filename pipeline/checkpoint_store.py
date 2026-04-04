"""Atomic checkpoint store for pipeline state persistence.

React equivalent: localStorage wrapper with atomic write semantics.

Exports:
    CheckpointStore — load/save/backup/clear PipelineState to a JSON file
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from pipeline.pipeline_state import PipelineState


class CheckpointStore:
    """Persists PipelineState to a JSON file with atomic write semantics.

    Atomic write: data is written to a .tmp file then renamed to prevent
    partial writes on crash (T-01-01).

    Missing or corrupt files return None from load() — no silent swallow
    of json.JSONDecodeError (T-01-02).
    """

    DEFAULT_PATH = Path(".pipeline/checkpoint.json")

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self._path = Path(path)

    def load(self) -> PipelineState | None:
        """Return PipelineState from checkpoint file, or None if absent.

        Raises ValueError if the file exists but contains invalid JSON.
        """
        if not self._path.exists():
            return None
        raw = self._path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Corrupt checkpoint at {self._path}: {exc}") from exc
        return PipelineState.from_dict(data)

    def save(self, state: PipelineState) -> None:
        """Write state atomically: write .tmp then os.replace().

        Directory is created if absent.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        os.replace(str(tmp_path), str(self._path))

    def backup(self, state: PipelineState) -> None:
        """Write dated backup to .pipeline/checkpoint.YYYY-MM-DD.bak."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        bak_path = self._path.parent / f"checkpoint.{today}.bak"
        bak_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")

    def clear(self) -> None:
        """Remove checkpoint file (start fresh). No-op if file absent."""
        if self._path.exists():
            self._path.unlink()
