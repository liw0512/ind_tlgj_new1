# -*- coding: utf-8 -*-
"""Artifact and runtime configuration for MFAC as the formal second module."""

from copy import deepcopy
from pathlib import Path

from .runtime_config import DEFAULT_MFAC_RUNTIME_CONFIG


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
    # Formal coordinator construction is fail-closed until real plant
    # calibration values are supplied.  Empty sections are intentional.
    "runtime": deepcopy(DEFAULT_MFAC_RUNTIME_CONFIG),
}


__all__ = [
    "PROJECT_ROOT",
    "CONDITION_ROOT",
    "MFAC_ROOT",
    "MFAC_OUTPUT_ROOT",
    "MFAC_PRIMARY_ARTIFACT_CONFIG",
]
