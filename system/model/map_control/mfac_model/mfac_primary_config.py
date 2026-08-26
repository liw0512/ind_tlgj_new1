# -*- coding: utf-8 -*-
"""Artifact paths for MFAC as the formal second module.

This configuration replaces the historical slurry-policy snapshot root.  It is
kept intentionally small: condition-model lifecycle remains owned by module 1,
while MFAC runtime state/profile artifacts live under ``mfac_model_output``.
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
CONDITION_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "condition_model"
MFAC_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "mfac_model"
MFAC_OUTPUT_ROOT = MFAC_ROOT / "mfac_model_output"


MFAC_PRIMARY_ARTIFACT_CONFIG = {
    "output_root": str(MFAC_OUTPUT_ROOT),
    "snapshots_dir": str(MFAC_OUTPUT_ROOT / "snapshots"),
    "active_version_file": str(MFAC_OUTPUT_ROOT / "active_version.json"),
    "condition_snapshots_dir": str(CONDITION_ROOT / "snapshots"),
    "runtime_dir": str(MFAC_OUTPUT_ROOT / "runtime"),
    "primary_mode": "MFAC_PRIMARY_SHADOW",
    "learn_enabled": False,
    "residual_enabled": False,
    "dcs_write_enabled": False,
}
