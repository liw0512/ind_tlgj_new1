# -*- coding: utf-8 -*-
"""Version artifacts for the canonical Scheme-2 MFAC second module.

The version builder is part of the Process4MapControl training lifecycle.  The
first module has already produced a condition-labelled CSV and immutable
ConditionSnapshot; this builder now executes the real MFAC offline evidence and
historical-prior training before writing the integrated-version manifest.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from system.model.config.mfac_plant_contract import plant_contract_snapshot

from .mfac_primary_config import (
    MFAC_PRIMARY_ARTIFACT_CONFIG,
    MFAC_PRIMARY_MODE,
)
from .offline_version_training import train_mfac_offline_version


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_condition_version(path: Path) -> str:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    version = str(value.get("snapshot_version") or "").strip()
    if not version.startswith("v") or not version[1:].isdigit():
        raise ValueError("condition snapshot version must be v###")
    return version


def _time_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return ""
    return pd.Timestamp(timestamp).isoformat()


def _existing_sha(path_text: Any) -> str:
    path = Path(str(path_text or ""))
    return sha256_file(path) if path.is_file() else ""


def build_mfac_version_artifact(
    *,
    input_csv: str,
    output_root: str,
    condition_snapshot: str,
    mode: str,
    previous_snapshot: Optional[str] = None,
) -> Dict[str, Any]:
    condition_path = Path(condition_snapshot).resolve()
    if not condition_path.is_file():
        raise FileNotFoundError("condition snapshot not found: %s" % condition_path)
    version = read_condition_version(condition_path)

    frame = pd.read_csv(input_csv, low_memory=False)
    if "date" not in frame.columns:
        raise ValueError("MFAC version input must contain date")
    timestamps = pd.to_datetime(frame["date"], errors="coerce").dropna()
    if timestamps.empty:
        raise ValueError("MFAC version input contains no valid date")

    root = Path(output_root).resolve()
    snapshot_dir = root / "snapshots" / version
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # This is the actual second-module offline train.  Any first-module version
    # mismatch or historical evidence failure aborts the builder before the
    # integrated version can be activated.
    offline_training = train_mfac_offline_version(
        input_csv=str(Path(input_csv).resolve()),
        output_root=str(root),
        condition_snapshot=str(condition_path),
        mode=str(mode).upper(),
        previous_snapshot=previous_snapshot,
    )

    plant_snapshot = plant_contract_snapshot()
    target_contract = plant_snapshot["target_supply_flow"]
    runtime_semantics = (
        "Q_TARGET=CLIP(QBASE+RESIDUAL_HOLD,%s,%s)"
        % (target_contract["minimum"], target_contract["maximum"])
    )

    offline_report_path = offline_training["offline_training_report_path"]
    summary = {
        "artifact_type": "MFAC_SECOND_MODULE_VERSION",
        "version": version,
        "mode": str(mode).upper(),
        "record_count": int(len(frame)),
        "first_data_timestamp": _time_text(timestamps.min()),
        "last_data_timestamp": _time_text(timestamps.max()),
        "condition_snapshot_version": version,
        "condition_snapshot_path": str(condition_path),
        "condition_snapshot_sha256": sha256_file(condition_path),
        "previous_snapshot": str(previous_snapshot or ""),
        "offline_training_status": offline_training["status"],
        "offline_training_report_path": str(offline_report_path),
        "offline_training_report_sha256": _existing_sha(offline_report_path),
        "current_valid_episode_count": int(
            offline_training["current_valid_episode_count"]
        ),
        "cumulative_valid_episode_count": int(
            offline_training["cumulative_valid_episode_count"]
        ),
        "bootstrap_status": "HISTORICAL_PRIOR_REVIEW_REQUIRED",
        "runtime_prior_reviewed": False,
        "runtime_prior_allowed": False,
        "online_runtime_state_overwrite": False,
        "online_update_trigger": "VALID_COMPLETED_CAUSAL_RESPONSE_EVENT",
        "learn_enabled": False,
        "residual_enabled": False,
        "dcs_write_enabled": False,
    }
    summary_path = snapshot_dir / "training_summary.json"
    with summary_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, allow_nan=False)

    manifest = {
        "artifact_type": "MFAC_PRIMARY_MANIFEST",
        "version": version,
        "condition_snapshot_version": version,
        "condition_snapshot_path": str(condition_path),
        "condition_snapshot_sha256": summary["condition_snapshot_sha256"],
        "training_summary_path": str(summary_path),
        "training_summary_sha256": sha256_file(summary_path),
        "offline_training_report_path": str(offline_report_path),
        "offline_training_report_sha256": summary["offline_training_report_sha256"],
        "historical_valid_episodes_path": offline_training[
            "historical_valid_episodes_path"
        ],
        "historical_valid_episodes_sha256": _existing_sha(
            offline_training["historical_valid_episodes_path"]
        ),
        "offline_effective_config_path": offline_training[
            "offline_effective_config_path"
        ],
        "offline_effective_config_sha256": _existing_sha(
            offline_training["offline_effective_config_path"]
        ),
        "offline_training_status": offline_training["status"],
        "runtime_semantics": runtime_semantics,
        "plant_contract_snapshot": plant_snapshot,
        "legacy_second_module_present": False,
        "primary_mode": MFAC_PRIMARY_MODE,
        "historical_prior_role": "REVIEW_CANDIDATE_ONLY",
        "runtime_prior_reviewed": False,
        "runtime_prior_allowed": False,
        "persisted_online_state_precedence": True,
        "runtime_state_namespace": [
            "condition_snapshot_version",
            "mfac_context_id",
        ],
        "cross_snapshot_online_state_reuse": False,
        "learn_enabled": False,
        "residual_enabled": False,
        "dcs_write_enabled": False,
    }
    manifest_path = snapshot_dir / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2, allow_nan=False)
    manifest["manifest_path"] = str(manifest_path)
    manifest["manifest_sha256"] = sha256_file(manifest_path)
    manifest["training_summary"] = summary
    manifest["offline_training"] = offline_training
    return manifest


def active_version_path() -> Path:
    return Path(MFAC_PRIMARY_ARTIFACT_CONFIG["active_version_file"])
