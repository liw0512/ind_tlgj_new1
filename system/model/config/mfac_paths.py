# -*- coding: utf-8 -*-
"""Single filesystem-path contract for Scheme-2 model artifacts.

The project keeps the three Scheme-2 model modules under
``system/model/map_control``:

- ``condition_model`` for canonical condition snapshots;
- ``mfac_model`` for MFAC versions, runtime state, evidence and diagnostics;
- ``fast_change_mode`` for FAST snapshots and runtime state.

Only path topology lives here. Algorithm parameters, plant facts and runtime
permissions must not be added to this module. Canonical paths must never point
to temporary repository-root workspaces such as ``_scheme2_work``.
"""
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAP_CONTROL_ROOT = PROJECT_ROOT / "system" / "model" / "map_control"

CONDITION_ROOT = MAP_CONTROL_ROOT / "condition_model"
CONDITION_SNAPSHOTS_DIR = CONDITION_ROOT / "snapshots"
MODEL_CSV_ROOT = MAP_CONTROL_ROOT / "model_csv"

MFAC_ROOT = MAP_CONTROL_ROOT / "mfac_model"
MFAC_OUTPUT_ROOT = MFAC_ROOT / "mfac_model_output"
MFAC_SNAPSHOTS_DIR = MFAC_OUTPUT_ROOT / "snapshots"
MFAC_ACTIVE_VERSION_FILE = MFAC_OUTPUT_ROOT / "active_version.json"
MFAC_RUNTIME_DIR = MFAC_OUTPUT_ROOT / "runtime"
MFAC_EVIDENCE_ROOT = MFAC_OUTPUT_ROOT / "evidence"
MFAC_EVIDENCE_OBJECTS_DIR = MFAC_EVIDENCE_ROOT / "objects"
MFAC_EVIDENCE_BUNDLES_DIR = MFAC_EVIDENCE_ROOT / "bundles"
MFAC_DIAGNOSTICS_ROOT = MFAC_OUTPUT_ROOT / "diagnostics"
MFAC_CONDITION_REPLAY_DIR = MFAC_DIAGNOSTICS_ROOT / "condition_majority_replay"
MFAC_CONDITION_ACTION_TIMING_DIR = MFAC_DIAGNOSTICS_ROOT / "condition_action_timing"
MFAC_DISTURBANCE_COUPLING_DIR = MFAC_DIAGNOSTICS_ROOT / "disturbance_slurry_coupling"
MFAC_EVIDENCE_ROLE_V2_1_DIR = MFAC_DIAGNOSTICS_ROOT / "historical_evidence_role_v2_1"

FAST_ROOT = MAP_CONTROL_ROOT / "fast_change_mode"
FAST_OUTPUT_ROOT = FAST_ROOT / "fast_change_output"
FAST_RUNTIME_ROOT = FAST_ROOT / "fast_change_runtime"


__all__ = [
    "PROJECT_ROOT",
    "MAP_CONTROL_ROOT",
    "CONDITION_ROOT",
    "CONDITION_SNAPSHOTS_DIR",
    "MODEL_CSV_ROOT",
    "MFAC_ROOT",
    "MFAC_OUTPUT_ROOT",
    "MFAC_SNAPSHOTS_DIR",
    "MFAC_ACTIVE_VERSION_FILE",
    "MFAC_RUNTIME_DIR",
    "MFAC_EVIDENCE_ROOT",
    "MFAC_EVIDENCE_OBJECTS_DIR",
    "MFAC_EVIDENCE_BUNDLES_DIR",
    "MFAC_DIAGNOSTICS_ROOT",
    "MFAC_CONDITION_REPLAY_DIR",
    "MFAC_CONDITION_ACTION_TIMING_DIR",
    "MFAC_DISTURBANCE_COUPLING_DIR",
    "MFAC_EVIDENCE_ROLE_V2_1_DIR",
    "FAST_ROOT",
    "FAST_OUTPUT_ROOT",
    "FAST_RUNTIME_ROOT",
]
