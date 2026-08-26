# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from system.model.map_control.mfac_model.mfac_primary_config import (
    MFAC_PRIMARY_ARTIFACT_CONFIG,
)
from system.model.map_control.mfac_model.version_artifacts import sha256_file


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object: %s" % path)
    return value


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
    if not condition_path.is_file():
        raise FileNotFoundError(
            "condition snapshot referenced by MFAC manifest not found: %s"
            % condition_path
        )
    condition_version = str(manifest.get("condition_snapshot_version") or "")
    if condition_version != version:
        raise ValueError(
            "MFAC/condition version mismatch: %s != %s"
            % (version, condition_version)
        )

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
            "mode": "MFAC_PRIMARY_SHADOW",
            "learn_enabled": False,
            "residual_enabled": False,
            "dcs_write_enabled": False,
        },
        # Temporary compatibility block for IntegratedVersionManager.  It no
        # longer points to or loads the deleted slurry-policy implementation.
        "slurry_policy": {
            "version": version,
            "source_condition_version": version,
            "snapshot_path": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "backend": "MFAC_COMPAT_POINTER",
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
