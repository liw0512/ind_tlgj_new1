# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from system.model.config.mfac_training_lifecycle import (
    INCREMENTAL_OFFLINE_TRAINING_DAYS,
    INITIAL_OFFLINE_TRAINING_DAYS,
    OFFLINE_TRAINING_ORDER,
    ONLINE_MFAC_UPDATE_TRIGGER,
    training_days_for_mode,
)
from system.model.map_control.mfac_model.mfac_primary_config import (
    MFAC_PRIMARY_ARTIFACT_CONFIG,
    MFAC_PRIMARY_MODE,
)
from system.model.map_control.mfac_model.version_artifacts import sha256_file


OFFLINE_ACTIVATION_LIFECYCLE_VERSION = (
    "SCHEME2_OFFLINE_ACTIVATION_V3_7DAY_INITIAL_3DAY_INCREMENTAL_CONDITION_THEN_MFAC"
)
_INCREMENTAL_TRIGGER_RULE = (
    "AFTER_AT_LEAST_INCREMENTAL_TRAINING_DAYS_OF_NEW_DATA"
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


def _validate_shared_cadence(
    value: Mapping[str, Any],
    *,
    field_name: str,
    mode: str | None = None,
    require_mode_days: bool = False,
) -> None:
    """Reject duplicate weekly semantics and validate the canonical 7d/3d pair."""
    if "periodic_offline_retrain_days" in value:
        raise ValueError(
            "%s contains deprecated periodic_offline_retrain_days" % field_name
        )
    if int(value.get("initial_training_days") or 0) != int(
        INITIAL_OFFLINE_TRAINING_DAYS
    ):
        raise ValueError("%s initial training must be 7 days" % field_name)
    if int(value.get("incremental_training_days") or 0) != int(
        INCREMENTAL_OFFLINE_TRAINING_DAYS
    ):
        raise ValueError("%s incremental training must be 3 days" % field_name)
    if require_mode_days:
        expected = training_days_for_mode(str(mode or ""))
        if int(value.get("required_training_days") or 0) != expected:
            raise ValueError(
                "%s required_training_days must be %s for %s"
                % (field_name, expected, mode)
            )


def validate_offline_lifecycle_artifacts(
    *,
    version: str,
    snapshot_dir: str | Path,
    condition_path: str | Path,
) -> Dict[str, Any]:
    """Fail closed unless Process4 completed condition -> MFAC offline training.

    Initial offline training and incremental offline training intentionally use
    different accumulation windows: 7 days for the first integrated version and
    3 days of new data after the active watermark for subsequent versions.
    Online MFAC phi/confidence adaptation is a separate event-driven lifecycle.

    Activation may replace the immutable condition/MFAC prior version pair, but
    the version artifact is forbidden from claiming that it overwrites online
    phi/confidence or carries residual, PendingDose or HOLD state across a
    ConditionSnapshot boundary.
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
    _validate_shared_cadence(
        summary,
        field_name="training_summary",
        mode=mode,
        require_mode_days=True,
    )
    if str(summary.get("online_update_trigger") or "") != ONLINE_MFAC_UPDATE_TRIGGER:
        raise ValueError("MFAC online adaptation must remain event driven")
    if summary.get("online_runtime_state_overwrite") is not False:
        raise ValueError("offline MFAC version must not overwrite online runtime state")

    if manifest.get("persisted_online_state_precedence") is not True:
        raise ValueError("persisted online MFAC state must precede offline prior")
    if manifest.get("cross_snapshot_online_state_reuse") is not True:
        raise ValueError("snapshot handoff contract is missing")
    if str(manifest.get("cross_snapshot_online_state_reuse_policy") or "") != (
        "SAME_MFAC_CONTEXT_AND_GRID_ONLY"
    ):
        raise ValueError("cross-snapshot MFAC state reuse policy is invalid")
    if manifest.get("cross_snapshot_online_state_requires_runtime_grid_id") is not True:
        raise ValueError("cross-snapshot MFAC handoff must require runtime grid identity")
    if manifest.get("cross_snapshot_residual_reuse") is not False:
        raise ValueError("residual must not cross a snapshot version boundary")
    if manifest.get("cross_snapshot_pending_or_hold_reuse") is not False:
        raise ValueError("PendingDose/HOLD state must not cross a snapshot version boundary")
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
    _validate_shared_cadence(lifecycle, field_name="offline lifecycle")
    if lifecycle.get("offline_order") != list(OFFLINE_TRAINING_ORDER):
        raise ValueError("offline training order must be CONDITION -> MFAC")
    if str(lifecycle.get("incremental_trigger_rule") or "") != _INCREMENTAL_TRIGGER_RULE:
        raise ValueError("offline lifecycle incremental trigger rule is invalid")
    if lifecycle.get("online_update_is_periodic") is not False:
        raise ValueError("online MFAC adaptation must not be periodic")
    if str(lifecycle.get("online_update_trigger") or "") != ONLINE_MFAC_UPDATE_TRIGGER:
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
        "offline_order": list(OFFLINE_TRAINING_ORDER),
        "required_training_days": training_days_for_mode(mode),
        "initial_training_days": int(INITIAL_OFFLINE_TRAINING_DAYS),
        "incremental_training_days": int(INCREMENTAL_OFFLINE_TRAINING_DAYS),
        "online_update_trigger": ONLINE_MFAC_UPDATE_TRIGGER,
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
    lifecycle_validation = validate_offline_lifecycle_artifacts(
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
            "offline_order": list(OFFLINE_TRAINING_ORDER),
            "mode": lifecycle_validation["mode"],
            "required_training_days": lifecycle_validation[
                "required_training_days"
            ],
            "initial_training_days": int(INITIAL_OFFLINE_TRAINING_DAYS),
            "incremental_training_days": int(INCREMENTAL_OFFLINE_TRAINING_DAYS),
            "incremental_trigger_rule": _INCREMENTAL_TRIGGER_RULE,
            "online_update_trigger": ONLINE_MFAC_UPDATE_TRIGGER,
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
