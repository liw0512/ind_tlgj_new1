"""p4pc -> condition_model -> slurry_policy_model bridge configuration.

This file contains only fixed project wiring: script locations, fixed interface
CSV paths, snapshot roots and the integrated active-version pointer. Standard
process signal names are fixed in ``standard_fields.py`` and are not configured
again in this bridge.
"""
from __future__ import annotations

from pathlib import Path

from system.model.config.standard_fields import TARGET_SO2_COLUMN


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONDITION_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "condition_model"
POLICY_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "slurry_policy_model"
MODEL_CSV_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "model_csv"
POLICY_OUTPUT_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "slurry_policy_model" / "slurry_policy_model_output"

SLURRY_CORE_BRIDGE_CONFIG = {
    "target_column": TARGET_SO2_COLUMN,
    "initial_version": "v001",

    # 第一模块训练入口与产物。
    "condition_initial_script": str(CONDITION_ROOT / "initial_condition_builder.py"),
    "condition_incremental_script": str(CONDITION_ROOT / "incremental_condition_updater.py"),
    "condition_snapshots_dir": str(CONDITION_ROOT / "snapshots"),
    "condition_merge_statistics": str(CONDITION_ROOT / "condition_merge_statistics.json"),
    "initial_condition_output_csv": str(MODEL_CSV_ROOT / "Initial_train_after_condition.csv"),
    "incremental_condition_output_csv": str(MODEL_CSV_ROOT / "Incremental_train_after_condition.csv"),

    # 第二模块训练、同版本激活与在线配置。
    "slurry_policy_initial_script": str(POLICY_ROOT / "initial_slurry_policy_trainer.py"),
    "slurry_policy_incremental_script": str(POLICY_ROOT / "incremental_slurry_policy_trainer.py"),
    "slurry_policy_activate_script": str(POLICY_ROOT / "activate_policy_version.py"),
    # 第二模块唯一配置入口；P4PC、离线训练、激活和在线均使用同一文件。
    "slurry_policy_config": str(POLICY_ROOT / "slurry_policy_config.py"),
    "slurry_policy_output_root": str(POLICY_OUTPUT_ROOT),
    "active_version_file": str(POLICY_OUTPUT_ROOT / "active_version.json"),
}
