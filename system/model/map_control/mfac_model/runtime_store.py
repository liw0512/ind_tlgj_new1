# -*- coding: utf-8 -*-
"""Persistent runtime state for Scheme 2 MFAC sidecar.

Exact snapshot/context restore remains the first choice.  Seven-day offline
retraining may publish a new ConditionSnapshot while the physical operating
region is unchanged, so V2 also supports one deliberately narrow handoff:
carry only the learned MFAC state across snapshots when both ``mfac_context_id``
and ``grid_id`` are unchanged.  Residual/Pending/HOLD state is never migrated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Dict, Optional

from .mfac_schema import MFAC_SEMANTICS_VERSION, MFACRuntimeState


SCHEME2_RUNTIME_STORE_VERSION = (
    "SCHEME2_RUNTIME_STORE_V2_SAME_CONTEXT_GRID_SNAPSHOT_HANDOFF"
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass
class Scheme2RuntimeRestore:
    restored: bool
    reason: str
    runtime_state: Optional[MFACRuntimeState] = None
    residual_mfac_hold: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["runtime_state"] = (
            self.runtime_state.to_dict() if self.runtime_state is not None else None
        )
        return value


class Scheme2RuntimeStore:
    """Atomic JSON store bound to MFAC semantics and condition snapshot version."""

    def __init__(
        self,
        runtime_dir: str | Path,
        *,
        filename: str = "scheme2_mfac_runtime_state.json",
        enabled: bool = True,
    ) -> None:
        self.root = Path(runtime_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / str(filename)
        self.enabled = bool(enabled)
        self.lock = threading.RLock()
        self.state = self._load_state()

    @property
    def last_valid_algorithm_target(self) -> Optional[float]:
        return _finite(self.state.get("last_valid_algorithm_target"))

    def set_last_valid_algorithm_target(self, value: Optional[float]) -> None:
        if value is None:
            self.state["last_valid_algorithm_target"] = None
            return
        number = _finite(value)
        if number is None:
            raise ValueError("last_valid_algorithm_target must be finite")
        self.state["last_valid_algorithm_target"] = number

    def upsert_context(
        self,
        runtime_state: MFACRuntimeState,
        *,
        residual_mfac_hold: float,
    ) -> None:
        residual = _finite(residual_mfac_hold)
        if residual is None:
            raise ValueError("residual_mfac_hold must be finite")
        contexts = self.state.setdefault("contexts", {})
        if not isinstance(contexts, dict):
            contexts = {}
            self.state["contexts"] = contexts
        contexts[runtime_state.mfac_context_id] = {
            "runtime_state": runtime_state.to_dict(),
            "residual_mfac_hold": residual,
        }

    def _raw_context(self, mfac_context_id: str) -> Optional[Dict[str, Any]]:
        if self.state.get("semantics_version") != MFAC_SEMANTICS_VERSION:
            return None
        contexts = self.state.get("contexts")
        if not isinstance(contexts, dict):
            return None
        raw = contexts.get(str(mfac_context_id))
        return raw if isinstance(raw, dict) else None

    @staticmethod
    def _decode_runtime_state(raw: Dict[str, Any]) -> Optional[MFACRuntimeState]:
        raw_state = raw.get("runtime_state")
        if not isinstance(raw_state, dict):
            return None
        try:
            runtime_state = MFACRuntimeState.from_dict(raw_state)
        except Exception:
            return None
        if runtime_state.semantics_version != MFAC_SEMANTICS_VERSION:
            return None
        return runtime_state

    def restore_context(
        self,
        *,
        condition_snapshot_version: str,
        mfac_context_id: str,
    ) -> Scheme2RuntimeRestore:
        if self.state.get("semantics_version") != MFAC_SEMANTICS_VERSION:
            return Scheme2RuntimeRestore(False, "STORE_SEMANTICS_MISMATCH")
        raw = self._raw_context(mfac_context_id)
        if raw is None:
            return Scheme2RuntimeRestore(False, "NO_CONTEXT_STATE")
        runtime_state = self._decode_runtime_state(raw)
        if runtime_state is None:
            return Scheme2RuntimeRestore(False, "CORRUPT_CONTEXT_STATE")
        if runtime_state.condition_snapshot_version != str(condition_snapshot_version):
            return Scheme2RuntimeRestore(False, "SNAPSHOT_VERSION_MISMATCH")
        if runtime_state.mfac_context_id != str(mfac_context_id):
            return Scheme2RuntimeRestore(False, "MFAC_CONTEXT_MISMATCH")
        residual = _finite(raw.get("residual_mfac_hold"))
        if residual is None:
            return Scheme2RuntimeRestore(False, "CORRUPT_RESIDUAL_STATE")
        return Scheme2RuntimeRestore(
            restored=True,
            reason="RESTORED",
            runtime_state=runtime_state,
            residual_mfac_hold=residual,
        )

    def restore_same_context_across_snapshot(
        self,
        *,
        condition_snapshot_version: str,
        mfac_context_id: str,
        grid_id: str,
    ) -> Scheme2RuntimeRestore:
        """Migrate learned phi/confidence across a weekly snapshot boundary.

        This is intentionally narrower than a generic cross-version restore:
        the persisted state must have the same MFAC context id and an explicit
        ``runtime_grid_id`` equal to the current grid.  Only MFAC runtime state
        is copied to the new snapshot namespace; the residual is reset to zero
        so Pending/HOLD/control cadence can never leak across model versions.
        """
        if self.state.get("semantics_version") != MFAC_SEMANTICS_VERSION:
            return Scheme2RuntimeRestore(False, "STORE_SEMANTICS_MISMATCH")
        target_snapshot = str(condition_snapshot_version or "").strip()
        context_id = str(mfac_context_id or "").strip()
        current_grid = str(grid_id or "").strip()
        if not target_snapshot or not context_id:
            return Scheme2RuntimeRestore(False, "MIGRATION_CONTEXT_UNAVAILABLE")
        if not current_grid:
            return Scheme2RuntimeRestore(False, "MIGRATION_GRID_UNAVAILABLE")

        raw = self._raw_context(context_id)
        if raw is None:
            return Scheme2RuntimeRestore(False, "NO_CONTEXT_STATE")
        runtime_state = self._decode_runtime_state(raw)
        if runtime_state is None:
            return Scheme2RuntimeRestore(False, "CORRUPT_CONTEXT_STATE")
        if runtime_state.mfac_context_id != context_id:
            return Scheme2RuntimeRestore(False, "MFAC_CONTEXT_MISMATCH")
        if runtime_state.condition_snapshot_version == target_snapshot:
            return Scheme2RuntimeRestore(False, "SAME_SNAPSHOT_USE_EXACT_RESTORE")

        metadata = dict(runtime_state.metadata or {})
        persisted_grid = str(metadata.get("runtime_grid_id") or "").strip()
        if not persisted_grid:
            return Scheme2RuntimeRestore(False, "MIGRATION_SOURCE_GRID_UNAVAILABLE")
        if persisted_grid != current_grid:
            return Scheme2RuntimeRestore(False, "MIGRATION_GRID_MISMATCH")

        source_snapshot = str(runtime_state.condition_snapshot_version)
        migrated_payload = runtime_state.to_dict()
        migrated_payload["condition_snapshot_version"] = target_snapshot
        migrated_payload["mfac_context_id"] = context_id
        migrated_metadata = dict(metadata)
        migrated_metadata.update(
            {
                "runtime_grid_id": current_grid,
                "cross_snapshot_state_migrated": True,
                "cross_snapshot_source_version": source_snapshot,
                "cross_snapshot_target_version": target_snapshot,
                "cross_snapshot_migration_policy": "SAME_MFAC_CONTEXT_AND_GRID_ONLY",
                "cross_snapshot_residual_reused": False,
            }
        )
        migrated_payload["metadata"] = migrated_metadata
        migrated = MFACRuntimeState.from_dict(migrated_payload)
        return Scheme2RuntimeRestore(
            restored=True,
            reason="CROSS_SNAPSHOT_SAME_CONTEXT_GRID_MIGRATED",
            runtime_state=migrated,
            residual_mfac_hold=0.0,
        )

    def save(self) -> None:
        if not self.enabled:
            return
        with self.lock:
            self.state["schema_version"] = SCHEME2_RUNTIME_STORE_VERSION
            self.state["semantics_version"] = MFAC_SEMANTICS_VERSION
            self._atomic_write(self.state)

    def _load_state(self) -> Dict[str, Any]:
        empty = self._empty_state()
        if not self.path.exists():
            return empty
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            if not isinstance(value, dict):
                raise ValueError("runtime state must be a JSON object")
            value.setdefault("contexts", {})
            return value
        except Exception:
            broken = self.path.with_suffix(self.path.suffix + ".broken")
            try:
                os.replace(self.path, broken)
            except OSError:
                pass
            empty["state_recovered_from_error"] = True
            return empty

    @staticmethod
    def _empty_state() -> Dict[str, Any]:
        return {
            "schema_version": SCHEME2_RUNTIME_STORE_VERSION,
            "semantics_version": MFAC_SEMANTICS_VERSION,
            "last_valid_algorithm_target": None,
            "contexts": {},
        }

    def _atomic_write(self, value: Dict[str, Any]) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)