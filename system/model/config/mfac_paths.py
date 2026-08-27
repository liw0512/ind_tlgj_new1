# -*- coding: utf-8 -*-
"""Single filesystem-path contract for Scheme-2 MFAC integration artifacts.

Only path topology lives here. Algorithm parameters, plant facts and runtime
permissions must not be added to this module.
"""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONDITION_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "condition_model"
MODEL_CSV_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "model_csv"
MFAC_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "mfac_model"
MFAC_OUTPUT_ROOT = MFAC_ROOT / "mfac_model_output"
MFAC_SNAPSHOTS_DIR = MFAC_OUTPUT_ROOT / "snapshots"
MFAC_ACTIVE_VERSION_FILE = MFAC_OUTPUT_ROOT / "active_version.json"
MFAC_RUNTIME_DIR = MFAC_OUTPUT_ROOT / "runtime"


__all__ = [
    "PROJECT_ROOT",
    "CONDITION_ROOT",
    "MODEL_CSV_ROOT",
    "MFAC_ROOT",
    "MFAC_OUTPUT_ROOT",
    "MFAC_SNAPSHOTS_DIR",
    "MFAC_ACTIVE_VERSION_FILE",
    "MFAC_RUNTIME_DIR",
]
