# -*- coding: utf-8 -*-
"""Version artifacts for MFAC replacing the historical second module."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from .mfac_primary_config import MFAC_PRIMARY_ARTIFACT_CONFIG


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

    frame = pd.read_csv(input_csv)
    if "date" not in frame.columns:
        raise ValueError("MFAC version input must contain date")
    timestamps = pd.to_datetime(frame["date"], errors="coerce").dropna()
    if timestamps.empty:
        raise ValueError("MFAC version input contains no valid date")

    root = Path(output_root).resolve()
    snapshot_dir = root / "snapshots" / version
    snapshot_dir.mkdir(parents=True, exist_ok=True)

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
        "bootstrap_status": "NOT_ACTIVATED_IN_PRIMARY_REPLACEMENT",
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
        "runtime_semantics": "Q_TARGET=CLIP(QBASE+RESIDUAL_HOLD,0,70)",
        "legacy_second_module_present": False,
        "primary_mode": "MFAC_PRIMARY_SHADOW",
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
    return manifest


def active_version_path() -> Path:
    return Path(MFAC_PRIMARY_ARTIFACT_CONFIG["active_version_file"])
