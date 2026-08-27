# -*- coding: utf-8 -*-
"""Single artifact/runtime configuration source for formal Scheme-2 MFAC."""

from copy import deepcopy
from pathlib import Path

from .runtime_config import DEFAULT_MFAC_RUNTIME_CONFIG


PROJECT_ROOT = Path(__file__).resolve().parents[4]
CONDITION_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "condition_model"
MFAC_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "mfac_model"
MFAC_OUTPUT_ROOT = MFAC_ROOT / "mfac_model_output"
MFAC_SNAPSHOTS_DIR = MFAC_OUTPUT_ROOT / "snapshots"
MFAC_ACTIVE_VERSION_FILE = MFAC_OUTPUT_ROOT / "active_version.json"
MFAC_RUNTIME_DIR = MFAC_OUTPUT_ROOT / "runtime"

# Runtime calibration defaults are defined by runtime_config, but the production
# runtime persistence path is an artifact path and therefore comes from this
# module's single path tree.
_PRIMARY_RUNTIME_CONFIG = deepcopy(DEFAULT_MFAC_RUNTIME_CONFIG)
_PRIMARY_RUNTIME_CONFIG["runtime_dir"] = str(MFAC_RUNTIME_DIR)


MFAC_PRIMARY_ARTIFACT_CONFIG = {
    "output_root": str(MFAC_OUTPUT_ROOT),
    "snapshots_dir": str(MFAC_SNAPSHOTS_DIR),
    "active_version_file": str(MFAC_ACTIVE_VERSION_FILE),
    "condition_snapshots_dir": str(CONDITION_ROOT / "snapshots"),
    "runtime_dir": str(MFAC_RUNTIME_DIR),
    "primary_mode": "MFAC_PRIMARY_SHADOW",
    # Runtime permission facts live only inside runtime config. They are also
    # defensively re-validated by builder/coordinator/P4PC, but are not repeated
    # as a second configurable set at this artifact level.
    "runtime": _PRIMARY_RUNTIME_CONFIG,
}


__all__ = [
    "PROJECT_ROOT",
    "CONDITION_ROOT",
    "MFAC_ROOT",
    "MFAC_OUTPUT_ROOT",
    "MFAC_SNAPSHOTS_DIR",
    "MFAC_ACTIVE_VERSION_FILE",
    "MFAC_RUNTIME_DIR",
    "MFAC_PRIMARY_ARTIFACT_CONFIG",
]
