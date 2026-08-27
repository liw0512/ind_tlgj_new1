"""P4PC -> condition_model -> MFAC bridge configuration.

``MFAC_CORE_BRIDGE_CONFIG`` is the canonical lifecycle configuration.  The
historical constant/key names are generated only as a compatibility view for
methods inherited from the large legacy P4PC shell; every target still points
exclusively to ``mfac_model``.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from system.model.config.standard_fields import TARGET_SO2_COLUMN


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONDITION_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "condition_model"
MFAC_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "mfac_model"
MODEL_CSV_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "model_csv"
MFAC_OUTPUT_ROOT = MFAC_ROOT / "mfac_model_output"


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
    # 正式第二模块 MFAC 生命周期。
    "mfac_initial_script": str(MFAC_ROOT / "initial_mfac_version_builder.py"),
    "mfac_incremental_script": str(MFAC_ROOT / "incremental_mfac_version_builder.py"),
    "mfac_activate_script": str(MFAC_ROOT / "activate_mfac_version.py"),
    "mfac_config": str(MFAC_ROOT / "mfac_primary_config.py"),
    "mfac_output_root": str(MFAC_OUTPUT_ROOT),
    "active_version_file": str(MFAC_OUTPUT_ROOT / "active_version.json"),
}


# Compatibility view for inherited legacy-shell method names.  New Scheme-2
# code must use MFAC_CORE_BRIDGE_CONFIG and the mfac_* keys above.
SLURRY_CORE_BRIDGE_CONFIG = deepcopy(MFAC_CORE_BRIDGE_CONFIG)
SLURRY_CORE_BRIDGE_CONFIG.update(
    {
        "slurry_policy_initial_script": MFAC_CORE_BRIDGE_CONFIG["mfac_initial_script"],
        "slurry_policy_incremental_script": MFAC_CORE_BRIDGE_CONFIG["mfac_incremental_script"],
        "slurry_policy_activate_script": MFAC_CORE_BRIDGE_CONFIG["mfac_activate_script"],
        "slurry_policy_config": MFAC_CORE_BRIDGE_CONFIG["mfac_config"],
        "slurry_policy_output_root": MFAC_CORE_BRIDGE_CONFIG["mfac_output_root"],
    }
)


__all__ = [
    "MFAC_CORE_BRIDGE_CONFIG",
    "SLURRY_CORE_BRIDGE_CONFIG",
]
