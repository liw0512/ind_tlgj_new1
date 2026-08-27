# -*- coding: utf-8 -*-
"""Single artifact/runtime configuration source for formal Scheme-2 MFAC."""

from copy import deepcopy

from system.model.config.mfac_paths import (
    CONDITION_ROOT,
    MFAC_ACTIVE_VERSION_FILE,
    MFAC_OUTPUT_ROOT,
    MFAC_ROOT,
    MFAC_RUNTIME_DIR,
    MFAC_SNAPSHOTS_DIR,
    PROJECT_ROOT,
)

from .runtime_config import DEFAULT_MFAC_RUNTIME_CONFIG


# Runtime calibration defaults are defined by runtime_config, while production
# persistence uses the canonical path contract shared by every MFAC integration
# layer.
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
