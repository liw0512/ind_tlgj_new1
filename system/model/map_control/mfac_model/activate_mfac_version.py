# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from system.model.map_control.mfac_model.mfac_primary_config import (
    MFAC_PRIMARY_ARTIFACT_CONFIG,
    MFAC_PRIMARY_MODE,
)
from system.model.map_control.mfac_model.version_artifacts import sha256_file


OFFLINE_ACTIVATION_LIFECYCLE_VERSION = (
    "SCHEME2_OFFLINE_ACTIVATION_V2_7DAY_CONDITION_THEN_MFAC"
)


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object: %s" % path)
    return value


def _require_version(value: Any, expected: str, field_name: str) -> None:
    observed = str(value or "").strip()
    if observed != expected:
        raise ValueError(
            "%s version mismatch: %s != %s" % (field_name, observed, expected)
        )


def _require_file_hash(path: Path, expected_sha: Any, field_name: str) -> None:
    if not path.is_file():
        raise FileNotFoundError("%s not found: %s" % (field_name, path))
    expected = str(expected_sha or "").strip()
    if not expected:
        raise ValueError("%s sha256 is required" % field_name)
    observed = sha256_file(path)
    if observed != expected:
        raise ValueError(
            "%s sha256 mismatch: %s != %s" % (field_name, observed, expected)
        )


def validate_offline_lifecycle_artifacts(
    *,
    version: str,
    snapshot_dir: str | Path,
    condition_path: str | Path,
) -> Dict[str, Any]:
    """Fail closed unless Process4 completed condition -> MFAC offline training.

    The seven-day offline refresh and event-driven online MFAC adaptation are
    intentionally different lifecycles.  Activation may replace the immutable
    condition/MFAC prior version pair, but the version artifact is forbidden
    from claiming that it overwrites online phi/confidence or carries residual,
    PendingDose or HOLD state across the weekly boundary.
    """
    version_text = str(version or "").strip()
    if not version_text.startswith("v") or not version_text[1:].isdigit():
        raise ValueError("version must be v###")

    root = Path(snapshot_dir).resolve()
    manifest_path = root / "manifest.json"
    summary_path = root / "training_summary.json"
    condition = Path(condition_path).resolve()
    if not manifest_path.is_file() or not summary_path.is_file():
        raise FileNotFoundError("MFAC version artifact incomplete: %s" % root)
    if not condition.is_file():
        raise FileNotFoundError("condition snapshot not found: %s" % condition)

    manifest = _read_json(manifest_path)
    summary = _read_json(summary_path)

    _require_version(manifest.get("version"), version_text, "manifest")
    _require_version(
        manifest.get("condition_snapshot_version"),
        version_text,
        "manifest.condition_snapshot_version",
    )
    _require_version(summary.get("version"), version_text, "training_summary")
    _require_version(
        summary.get("condition_snapshot_version"),
        version_text,
        "training_summary.condition_snapshot_version",
    )

    manifest_condition = Path(
        str(manifest.get("condition_snapshot_path") or "")
    ).resolve()
    if manifest_condition != condition:
        raise ValueError(
            "MFAC manifest references a different condition snapshot: %s != %s"
            % (manifest_condition, condition)
        )
    _require_file_hash(
        condition,
        manifest.get("condition_snapshot_sha256"),
        "condition_snapshot",
    )

    manifest_summary = Path(
        str(manifest.get("training_summary_path") or "")
    ).resolve()
    if manifest_summary != summary_path:
        raise ValueError(
            "MFAC manifest references a different training summary: %s != %s"
            % (manifest_summary, summary_path)
        )
    _require_file_hash(
        summary_path,
        manifest.get("training_summary_sha256"),
        "training_summary",
    )

    mode = str(summary.get("mode") or "").upper()
    if mode not in {"INITIAL", "INCREMENTAL"}:
        raise ValueError("training_summary.mode must be INITIAL or INCREMENTAL")
    if int(summary.get("periodic_offline_retrain_days") or 0) != 7:
        raise ValueError("MFAC offline retraining period must be 7 days")
    if str(summary.get("online_update_trigger") or "") != (
        "VALID_COMPLETED_CAUSAL_RESPONSE_EVENT"
    ):
        raise ValueError("MFAC online adaptation must remain event driven")
    if summary.get("online_runtime_state_overwrite") is not False:
        raise ValueError("offline MFAC version must not overwrite online runtime state")

    if manifest.get("persisted_online_state_precedence") is not True:
        raise ValueError("persisted online MFAC state must precede offline prior")
    if manifest.get("cross_snapshot_online_state_reuse") is not True:
        raise ValueError("weekly snapshot handoff contract is missing")
    if str(manifest.get("cross_snapshot_online_state_reuse_policy") or "") != (
        "SAME_MFAC_CONTEXT_AND_GRID_ONLY"
    ):
        raise ValueError("cross-snapshot MFAC state reuse policy is invalid")
    if manifest.get("cross_snapshot_online_state_requires_runtime_grid_id") is not True:
        raise ValueError("cross-snapshot MFAC handoff must require runtime grid identity")
    if manifest.get("cross_snapshot_residual_reuse") is not False:
        raise ValueError("residual must not cross a weekly version boundary")
    if manifest.get("cross_snapshot_pending_or_hold_reuse") is not False:
        raise ValueError("PendingDose/HOLD state must not cross a weekly version boundary")
    if manifest.get("historical_prior_may_overwrite_online_evidence") is not False:
        raise ValueError("offline historical prior must not overwrite online evidence")

    offline_report_path = Path(
        str(manifest.get("offline_training_report_path") or "")
    ).resolve()
    _require_file_hash(
        offline_report_path,
        manifest.get("offline_training_report_sha256"),
        "offline_training_report",
    )
    offline = _read_json(offline_report_path)
    _require_version(offline.get("version"), version_text, "offline_training_report")
    offline_mode = str(offline.get("mode") or "").upper()
    if offline_mode != mode:
        raise ValueError(
            "offline training mode mismatch: %s != %s" % (offline_mode, mode)
        )
    lifecycle = offline.get("lifecycle_contract")
    if not isinstance(lifecycle, dict):
        raise ValueError("offline training lifecycle_contract is required")
    if lifecycle.get("offline_order") != ["CONDITION", "MFAC"]:
        raise ValueError("offline training order must be CONDITION -> MFAC")
    if int(lifecycle.get("periodic_offline_retrain_days") or 0) != 7:
        raise ValueError("offline lifecycle period must be 7 days")
    if lifecycle.get("online_update_is_periodic") is not False:
        raise ValueError("online MFAC adaptation must not be periodic")
    if str(lifecycle.get("online_update_trigger") or "") != (
        "VALID_COMPLETED_CAUSAL_RESPONSE_EVENT"
    ):
        raise ValueError("offline lifecycle online trigger is invalid")
    if lifecycle.get("historical_prior_may_overwrite_online_evidence") is not False:
        raise ValueError("historical prior overwrite contract is invalid")

    historical_episodes_path = Path(
        str(manifest.get("historical_valid_episodes_path") or "")
    ).resolve()
    _require_file_hash(
        historical_episodes_path,
        manifest.get("historical_valid_episodes_sha256"),
        "historical_valid_episodes",
    )
    effective_config_path = Path(
        str(manifest.get("offline_effective_config_path") or "")
    ).resolve()
    _require_file_hash(
        effective_config_path,
        manifest.get("offline_effective_config_sha256"),
        "offline_effective_config",
    )

    return {
        "semantics_version": OFFLINE_ACTIVATION_LIFECYCLE_VERSION,
        "version": version_text,
        "mode": mode,
        "offline_order": ["CONDITION", "MFAC"],
        "periodic_offline_retrain_days": 7,
        "online_update_trigger": "VALID_COMPLETED_CAUSAL_RESPONSE_EVENT",
        "online_update_is_periodic": False,
        "persisted_online_state_precedence": True,
        "cross_snapshot_online_state_reuse_policy": (
            "SAME_MFAC_CONTEXT_AND_GRID_ONLY"
        ),
        "cross_snapshot_residual_reuse": False,
        "cross_snapshot_pending_or_hold_reuse": False,
        "historical_prior_may_overwrite_online_evidence": False,
        "manifest_path": str(manifest_path),
        "offline_training_report_path": str(offline_report_path),
    }


def activate(version: str) -> Path:
    version = str(version or "").strip()
    if not version.startswith("v") or not version[1:].isdigit():
        raise ValueError("version must be v###")

    root = Path(MFAC_PRIMARY_ARTIFACT_CONFIG["output_root"]).resolve()
    snapshot_dir = root / "snapshots" / version
    manifest_path = snapshot_dir / "manifest.json"
    summary_path = snapshot_dir / "training_summary.json"
    if not manifest_path.is_file() or not summary_path.is_file():
        raise FileNotFoundError(
            "MFAC version artifact incomplete: %s" % snapshot_dir
        )

    manifest = _read_json(manifest_path)
    condition_path = Path(str(manifest.get("condition_snapshot_path") or "")).resolve()
    validate_offline_lifecycle_artifacts(
        version=version,
        snapshot_dir=snapshot_dir,
        condition_path=condition_path,
    )

    manifest_mode = str(manifest.get("primary_mode") or "").strip()
    if manifest_mode and manifest_mode != MFAC_PRIMARY_MODE:
        raise ValueError(
            "MFAC primary mode mismatch: %s != %s"
            % (manifest_mode, MFAC_PRIMARY_MODE)
        )

    # Canonical pointer: module 2 is MFAC. No slurry_policy compatibility
    # block is emitted anymore; IntegratedVersionManager can still read older
    # migration pointers, but all newly activated versions are MFAC-native.
    pointer = {
        "integrated_version": version,
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "backend": "MFAC",
        "condition": {
            "version": version,
            "snapshot_path": str(condition_path),
            "snapshot_sha256": sha256_file(condition_path),
        },
        "mfac": {
            "version": version,
            "source_condition_version": version,
            "snapshot_path": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "mode": MFAC_PRIMARY_MODE,
            "learn_enabled": False,
            "residual_enabled": False,
            "dcs_write_enabled": False,
        },
        "offline_online_lifecycle": {
            "semantics_version": OFFLINE_ACTIVATION_LIFECYCLE_VERSION,
            "offline_order": ["CONDITION", "MFAC"],
            "periodic_offline_retrain_days": 7,
            "online_update_trigger": "VALID_COMPLETED_CAUSAL_RESPONSE_EVENT",
            "online_update_is_periodic": False,
            "cross_snapshot_online_state_reuse_policy": (
                "SAME_MFAC_CONTEXT_AND_GRID_ONLY"
            ),
            "cross_snapshot_residual_reuse": False,
            "cross_snapshot_pending_or_hold_reuse": False,
            "historical_prior_may_overwrite_online_evidence": False,
        },
    }

    active_path = Path(MFAC_PRIMARY_ARTIFACT_CONFIG["active_version_file"]).resolve()
    active_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = active_path.with_suffix(active_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(pointer, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, active_path)
    return active_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Activate MFAC second-module version")
    parser.add_argument("--version", required=True)
    parser.add_argument("--config", default="")
    args = parser.parse_args()
    del args.config
    print(activate(args.version))


if __name__ == "__main__":
    main()
