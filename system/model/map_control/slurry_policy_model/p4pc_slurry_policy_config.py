"""Process4MapControl integration override for the slurry-policy core.

Only project-path fields are overridden here.  Tower definitions, pH safety
ranges, action/response settings and online strategy parameters continue to
come from ``slurry_policy_config.py`` so there is still one algorithm config.
"""
from __future__ import annotations

import copy
from pathlib import Path

from system.model.map_control.slurry_policy_model import slurry_policy_config as _base

PROJECT_ROOT = Path(__file__).resolve().parents[4]
CONDITION_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "condition_model"
POLICY_OUTPUT_ROOT = PROJECT_ROOT / "files" / "slurry_policy_model_output"
MODEL_CSV_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "model_csv"

PLANT_CONFIG = copy.deepcopy(_base.PLANT_CONFIG)
TRAINING_CONFIG = copy.deepcopy(_base.TRAINING_CONFIG)
ONLINE_POLICY_CONFIG = copy.deepcopy(_base.ONLINE_POLICY_CONFIG)

PLANT_CONFIG["paths"].update(
    {
        "default_initial_input": str(MODEL_CSV_ROOT / "Initial_train_after_condition.csv"),
        "default_incremental_input": str(MODEL_CSV_ROOT / "Incremental_train_after_condition.csv"),
        "output_root": str(POLICY_OUTPUT_ROOT),
        "condition_snapshots_dir": str(CONDITION_ROOT / "snapshots"),
        "active_policy_version_file": str(POLICY_OUTPUT_ROOT / "active_version.json"),
        "online_runtime_dir": str(PROJECT_ROOT / "files" / "slurry_policy_online_runtime"),
    }
)
