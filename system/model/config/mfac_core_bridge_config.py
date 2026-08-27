"""Canonical P4PC -> condition_model -> MFAC lifecycle configuration.

MFAC artifact/output/active-version paths are not redefined here. They are
imported from ``mfac_primary_config`` so activation and online loading cannot
drift to different directories.
"""
from __future__ import annotations

from pathlib import Path

from system.model.config.standard_fields import TARGET_SO2_COLUMN
from system.model.map_control.mfac_model.mfac_primary_config import (
    CONDITION_ROOT,
    MFAC_ACTIVE_VERSION_FILE,
    MFAC_OUTPUT_ROOT,
    MFAC_ROOT,
    PROJECT_ROOT,
)


MODEL_CSV_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "model_csv"


MFAC_CORE_BRIDGE_CONFIG = {
    "target_column": TARGET_SO2_COLUMN,
    "initial_version": "v001",
    "second_module_backend": "MFAC",
    # 第一模块训练入口与产物。
    "condition_initial_script": str(CONDITION_ROOT / "initial_condition_builder.py"),
    "condition_incremental_script": str(CONDITION_ROOT / "incremental_condition_updater.py"),
    "condition_snapshots_dir": str(CONDITION_ROOT / "snapshots"),
    "condition_merge_statistics": str(CONDITION_ROOT / "condition_merge_statistics.json"),
    "initial_condition_output_csv": str(MODEL_CSV_ROOT / "Initial_train_after_condition.csv"),
    "incremental_condition_output_csv": str(MODEL_CSV_ROOT / "Incremental_train_after_condition.csv"),
    # 正式第二模块 MFAC 生命周期。Artifact paths由mfac_primary_config唯一提供。
    "mfac_initial_script": str(MFAC_ROOT / "initial_mfac_version_builder.py"),
    "mfac_incremental_script": str(MFAC_ROOT / "incremental_mfac_version_builder.py"),
    "mfac_activate_script": str(MFAC_ROOT / "activate_mfac_version.py"),
    "mfac_config": str(MFAC_ROOT / "mfac_primary_config.py"),
    "mfac_output_root": str(MFAC_OUTPUT_ROOT),
    "active_version_file": str(MFAC_ACTIVE_VERSION_FILE),
}


__all__ = [
    "PROJECT_ROOT",
    "CONDITION_ROOT",
    "MFAC_ROOT",
    "MODEL_CSV_ROOT",
    "MFAC_OUTPUT_ROOT",
    "MFAC_CORE_BRIDGE_CONFIG",
]
