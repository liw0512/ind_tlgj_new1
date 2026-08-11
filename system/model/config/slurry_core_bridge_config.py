"""p4pc -> condition_model -> slurry_policy_model bridge configuration.

The old industrial p4pc lifecycle configuration remains in
``process4map_config.py``.  This file only contains the new core's fixed
project paths and online interface fields.  The online judgement cadence is
NOT duplicated here: p4pc continues to read
``PROCESS4MAP_CONFIG.runtime.snapshot_interval_seconds`` so changing that one
value changes the model judgement interval.
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONDITION_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "condition_model"
POLICY_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "slurry_policy_model"
MODEL_CSV_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "model_csv"
POLICY_OUTPUT_ROOT = PROJECT_ROOT / "files" / "slurry_policy_model_output"

SLURRY_CORE_BRIDGE_CONFIG = {
    # p4pc 的 snapshot_interval_seconds 就是新的在线模型判定周期。
    "target_column": "outlet_so2_target",
    "initial_version": "v001",
    "activate_after_training": True,

    # 第一模块训练入口与产物。
    "condition_initial_script": str(CONDITION_ROOT / "initial_condition_builder.py"),
    "condition_incremental_script": str(CONDITION_ROOT / "incremental_condition_updater.py"),
    "condition_snapshots_dir": str(CONDITION_ROOT / "snapshots"),
    "condition_merge_statistics": str(CONDITION_ROOT / "condition_merge_statistics.json"),
    "initial_condition_output_csv": str(MODEL_CSV_ROOT / "Initial_train_after_condition.csv"),
    "incremental_condition_output_csv": str(MODEL_CSV_ROOT / "Incremental_train_after_condition.csv"),

    # 第二模块训练、激活与在线配置。
    "slurry_policy_initial_script": str(POLICY_ROOT / "initial_slurry_policy_trainer.py"),
    "slurry_policy_incremental_script": str(POLICY_ROOT / "incremental_slurry_policy_trainer.py"),
    "slurry_policy_activate_script": str(POLICY_ROOT / "activate_policy_version.py"),
    "slurry_policy_config": str(POLICY_ROOT / "p4pc_slurry_policy_config.py"),
    "slurry_policy_output_root": str(POLICY_OUTPUT_ROOT),
    "active_version_file": str(POLICY_OUTPUT_ROOT / "active_version.json"),
}

# clean_data 必须保留/生成这些字段，才能完整调用新的两模块核心。
# 数据库表本轮不改；这里只定义进入实时算法的数据接口。
SLURRY_INPUT_FIELDS = (
    "id",
    "date",
    "jzfh",
    "zml",
    "yyq_SO2",
    "jyq_SO2",
    "yyq_O2",
    "yyq_LL",
    "jyq_LL",
    "xstshsjy_MD",
    "aptshsjy_MD",
    "xstshsjy_LL",
    "aptshsjy_LL",
    "xst_YW",
    "apt_YW",
    "xstjy_PH1",
    "xstjy_PH2",
    "aptjy_PH1",
    "aptjy_PH2",
    "xstjy_PH",
    "aptjy_PH",
    "xst_FMKD1",
    "xst_FMKD2",
    "apt_FMKD",
    "xstjyxhb_ADL",
    "xstjyxhb_BDL",
    "xstjyxhb_CDL",
    "xstjyxhb_DDL",
    "xstjyxhb_EDL",
    "aptjyxhb_ADL",
    "aptjyxhb_BDL",
    "aptjyxhb_CDL",
    "xstyhfj_ADL",
    "aptyhfj_ADL",
    "xst_circulation_pump_count",
    "apt_circulation_pump_count",
    "liquid_gas_ratio",
    "desulfurization_efficiency",
    "jym",
    "connection_status",
    "outlet_so2_target",
)
