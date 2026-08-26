# -*- coding: utf-8 -*-
"""Persistent runtime state for Scheme 2 MFAC sidecar."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Dict, Optional

from .mfac_schema import MFAC_SEMANTICS_VERSION, MFACRuntimeState


SCHEME2_RUNTIME_STORE_VERSION = "SCHEME2_RUNTIME_STORE_V1"


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

    def restore_context(
        self,
        *,
        condition_snapshot_version: str,
        mfac_context_id: str,
    ) -> Scheme2RuntimeRestore:
        if self.state.get("semantics_version") != MFAC_SEMANTICS_VERSION:
            return Scheme2RuntimeRestore(False, "STORE_SEMANTICS_MISMATCH")
        contexts = self.state.get("contexts")
        if not isinstance(contexts, dict):
            return Scheme2RuntimeRestore(False, "NO_CONTEXT_STATE")
        raw = contexts.get(str(mfac_context_id))
        if not isinstance(raw, dict):
            return Scheme2RuntimeRestore(False, "NO_CONTEXT_STATE")
        raw_state = raw.get("runtime_state")
        if not isinstance(raw_state, dict):
            return Scheme2RuntimeRestore(False, "CORRUPT_CONTEXT_STATE")
        try:
            runtime_state = MFACRuntimeState.from_dict(raw_state)
        except Exception:
            return Scheme2RuntimeRestore(False, "CORRUPT_CONTEXT_STATE")
        if runtime_state.semantics_version != MFAC_SEMANTICS_VERSION:
            return Scheme2RuntimeRestore(False, "CONTEXT_SEMANTICS_MISMATCH")
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
