# -*- coding: utf-8 -*-
"""Configuration for the fixed-grid condition model.

Plant-specific facts are no longer configured here.  ``condition_axes``, field
names, tower pH columns and the outlet-SO2 safety limit are derived from the
single authoritative ``system/model/config/plant_config.py``.

This file now contains only condition-model algorithm/lifecycle parameters and
compatibility adapters for historical snapshots.  The internal names
``load`` / ``inlet_so2`` and ``xst_ph`` / ``apt_ph`` are legacy slots only;
they no longer imply fixed physical meanings.
"""

import copy
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from system.model.config.plant_config import PLANT_CONFIG as SITE_PLANT_CONFIG


# ---------------------------------------------------------------------------
# 厂级事实全部从 plant_config.py 派生；这里保留同名常量只是为了兼容已有调用。
CONDITION_AXES: List[Dict[str, Any]] = copy.deepcopy(
    SITE_PLANT_CONFIG["condition_axes"]
)

_PROCESS_COLUMNS = SITE_PLANT_CONFIG["process_columns"]
_ENABLED_TOWER_PH_COLUMNS = [
    str(tower.get("ph_column", "")).strip()
    for tower in SITE_PLANT_CONFIG.get("towers", [])
    if tower.get("enabled", True) and str(tower.get("ph_column", "")).strip()
]

# 第一模块历史统计结构仍保留两个 pH 兼容槽位，但不再固定绑定“一级塔/二级塔”。
# 第一个启用塔映射到 xst_ph 槽，第二个启用塔映射到 apt_ph 槽；不存在的槽使用
# 一个不可能出现在现场 CSV 中的占位字段，因此 pH 继续保持“有则统计、无则忽略”。
_FIRST_PH_COLUMN = (
    _ENABLED_TOWER_PH_COLUMNS[0]
    if _ENABLED_TOWER_PH_COLUMNS
    else "__unused_condition_ph_1__"
)
_SECOND_PH_COLUMN = (
    _ENABLED_TOWER_PH_COLUMNS[1]
    if len(_ENABLED_TOWER_PH_COLUMNS) > 1
    else "__unused_condition_ph_2__"
)

DEFAULT_DATA_COLUMNS = {
    "outlet_so2": str(_PROCESS_COLUMNS["outlet_so2"]),
    "xst_ph": _FIRST_PH_COLUMN,
    "apt_ph": _SECOND_PH_COLUMN,
    "liquid_gas": str(_PROCESS_COLUMNS["liquid_gas"]),
}

# 第一模块 risk_rate 直接使用厂级 SO2 安全范围上限，不再单独维护“35”。
DEFAULT_EMISSION_LIMIT = float(SITE_PLANT_CONFIG["outlet_so2_safe_range"][1])


DEFAULT_MERGE_CONFIG = {
    "enabled": True,
    "mode": "evidence_only",
    "min_observed_samples": 10,
    "min_mature_samples": 30,
    "min_auto_merge_samples": 100,
    "min_auto_confirm_samples": 300,
    "min_common_state_samples": 10,
    "min_risk_samples": 30,
    "min_metric_coverage_ratio": 0.80,
    "min_consecutive_pass_snapshots": 3,
    "min_new_samples_per_member_for_confirmation": 10,
    "max_auto_region_cells": 8,
    "max_liquid_gas_relative_difference": 0.15,
    "max_pump_distribution_distance": 0.25,
    "max_risk_rate_difference": 0.10,
}


DEFAULT_ONLINE_CONFIG = {
    "stability_mode": "MAJORITY",
    "stability_window_size": 6,
    "majority_tie_policy": "KEEP_LAST_STABLE",
    "allow_provisional_region_fallback": True,
}


DEFAULT_CONDITION_MODEL_CONFIG = {
    "condition_axes": CONDITION_AXES,
    "data_columns": DEFAULT_DATA_COLUMNS,
    "emission_limit": DEFAULT_EMISSION_LIMIT,
    "out_of_range_policy": "clip",
    "merge": DEFAULT_MERGE_CONFIG,
    "online": DEFAULT_ONLINE_CONFIG,
    "artifact_dir": "artifacts/condition",
}


MAX_SNAPSHOT_VERSIONS_TO_KEEP = 5


# ---------------------------------------------------------------------------
# 单独运行第一模块时使用的项目内默认路径。P4PC 会显式传参覆盖这些路径。
# 不再维护 F:\\tlgj / F:\\tlgj_new 等机器绝对路径。
CONDITION_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "condition_model"
MODEL_CSV_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "model_csv"
POLICY_OUTPUT_ROOT = PROJECT_ROOT / "files" / "slurry_policy_model_output"

INITIAL_CONDITION_TRAIN_CONFIG = {
    "input_csv_path": str(MODEL_CSV_ROOT / "Initial_train.csv"),
    "output_csv_path": str(MODEL_CSV_ROOT / "Initial_train_after_condition.csv"),
    "merge_statistics_json_path": str(CONDITION_ROOT / "condition_merge_statistics.json"),
    "auto_merge_report_path": str(
        CONDITION_ROOT / "snapshots" / "v001" / "auto_merge_report.json"
    ),
    "snapshot_output_path": str(
        CONDITION_ROOT / "snapshots" / "v001" / "condition_snapshot.json"
    ),
    "snapshot_version": "v001",
    "encoding": "utf-8-sig",
}


INCREMENTAL_CONDITION_TRAIN_CONFIG = {
    "base_snapshot_path": "latest",
    "input_csv_path": str(MODEL_CSV_ROOT / "Update_train.csv"),
    "output_csv_path": str(MODEL_CSV_ROOT / "Incremental_train_after_condition.csv"),
    "merge_statistics_json_path": str(CONDITION_ROOT / "condition_merge_statistics.json"),
    "auto_merge_report_path": "auto",
    "snapshot_output_path": "auto",
    "snapshot_version": "auto",
    "encoding": "utf-8-sig",
}


ONLINE_CONDITION_CLASSIFY_CONFIG = {
    "snapshot_path": "active",
    "merge_statistics_json_path": str(CONDITION_ROOT / "condition_merge_statistics.json"),
    "input_csv_path": str(MODEL_CSV_ROOT / "Incremental_train_after_condition.csv"),
    "output_csv_path": str(MODEL_CSV_ROOT / "Online_after_condition_and_policy.csv"),
    "encoding": "utf-8-sig",
    "slurry_policy_online": {
        "enabled": True,
        "config_spec": None,
        "external_version_management": True,
        "integrated_version": {
            "enabled": True,
            "active_version_file": str(POLICY_OUTPUT_ROOT / "active_version.json"),
            "hot_reload_enabled": True,
            "reload_check_interval_seconds": 30.0,
            "verify_condition_snapshot_hash": True,
            "require_atomic_pair_switch": True,
            "reset_condition_stability_window": True,
            "preserve_runtime_control_state": True,
            "keep_current_version_on_failure": True,
        },
        "initialize_on_start": True,
        "failure_mode": "BLOCKED_OUTPUT",
        "output_prefix": "slurry_policy_",
        "target_column": str(_PROCESS_COLUMNS["target_so2"]),
        "fixed_target": None,
        "default_execution_context": {
            "automatic_control_allowed": False,
            "manual_valves": [],
            "faulted_valves": [],
            "supply_pump_state_changing": False,
        },
        "execution_context_columns": {
            "automatic_control_allowed": "automatic_control_allowed",
            "manual_valves": "manual_valves",
            "faulted_valves": "faulted_valves",
            "supply_pump_state_changing": "supply_pump_state_changing",
        },
    },
}


@dataclass(frozen=True)
class AxisConfig:
    minimum: float
    maximum: float
    step: float

    def validate(self, name: str) -> None:
        if not all(math.isfinite(value) for value in (self.minimum, self.maximum, self.step)):
            raise ValueError(f"Non-finite {name} grid range")
        if self.step <= 0 or self.maximum <= self.minimum:
            raise ValueError(f"Invalid {name} grid range")

    @property
    def cell_count(self) -> int:
        return max(1, int((self.maximum - self.minimum) // self.step))


@dataclass(frozen=True)
class DataColumnConfig:
    outlet_so2: str = DEFAULT_DATA_COLUMNS["outlet_so2"]
    xst_ph: str = DEFAULT_DATA_COLUMNS["xst_ph"]
    apt_ph: str = DEFAULT_DATA_COLUMNS["apt_ph"]
    liquid_gas: str = DEFAULT_DATA_COLUMNS["liquid_gas"]

    def validate(self) -> None:
        for name, value in self.__dict__.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"data column {name} cannot be empty")


@dataclass(frozen=True)
class MergeConfig:
    """Automatic region merge policy."""

    enabled: bool = True
    mode: str = "evidence_only"
    min_observed_samples: int = 10
    min_mature_samples: int = 30
    min_auto_merge_samples: int = 100
    min_auto_confirm_samples: int = 300
    min_common_state_samples: int = 10
    min_risk_samples: int = 30
    min_metric_coverage_ratio: float = 0.80
    min_consecutive_pass_snapshots: int = 3
    min_new_samples_per_member_for_confirmation: int = 10
    max_auto_region_cells: int = 8
    max_liquid_gas_relative_difference: float = 0.15
    max_pump_distribution_distance: float = 0.25
    max_risk_rate_difference: float = 0.10

    @property
    def auto_publication_sample_threshold(self) -> int:
        if self.mode == "conservative":
            return max(self.min_auto_merge_samples, self.min_auto_confirm_samples)
        return self.min_auto_merge_samples


@dataclass(frozen=True)
class OnlineConfig:
    stability_mode: str = "MAJORITY"
    stability_window_size: int = 6
    majority_tie_policy: str = "KEEP_LAST_STABLE"
    allow_provisional_region_fallback: bool = True


_SINGLE_AXIS_PADDING = AxisConfig(
    minimum=-1.0e100,
    maximum=1.0e100,
    step=2.0e100,
)


@dataclass(frozen=True)
class ConditionModelConfig:
    # Legacy internal slots: first configured axis / optional second configured axis.
    load: AxisConfig
    inlet_so2: AxisConfig
    load_column: str = CONDITION_AXES[0]["column"]
    inlet_so2_column: str = (
        CONDITION_AXES[1]["column"]
        if len(CONDITION_AXES) > 1
        else CONDITION_AXES[0]["column"]
    )
    single_axis_mode: bool = False
    data_columns: DataColumnConfig = field(default_factory=DataColumnConfig)
    emission_limit: float = DEFAULT_EMISSION_LIMIT
    out_of_range_policy: str = "clip"
    merge: MergeConfig = field(default_factory=MergeConfig)
    online: OnlineConfig = field(default_factory=OnlineConfig)
    artifact_dir: str = "artifacts/condition"

    @property
    def condition_axis_columns(self) -> Tuple[str, ...]:
        if self.single_axis_mode:
            return (self.load_column,)
        return (self.load_column, self.inlet_so2_column)

    @property
    def condition_axis_count(self) -> int:
        return len(self.condition_axis_columns)

    @property
    def condition_axes(self) -> Tuple[Tuple[str, AxisConfig], ...]:
        if self.single_axis_mode:
            return ((self.load_column, self.load),)
        return (
            (self.load_column, self.load),
            (self.inlet_so2_column, self.inlet_so2),
        )

    def validate(self) -> None:
        self.load.validate(self.load_column)
        self.inlet_so2.validate(
            "internal_single_axis_padding"
            if self.single_axis_mode
            else self.inlet_so2_column
        )
        self.data_columns.validate()
        if not self.load_column:
            raise ValueError("first condition axis column cannot be empty")
        if not self.single_axis_mode and not self.inlet_so2_column:
            raise ValueError("second condition axis column cannot be empty")
        if not self.single_axis_mode and self.load_column == self.inlet_so2_column:
            raise ValueError("two condition axes must use different source columns")
        if not math.isfinite(self.emission_limit) or self.emission_limit <= 0:
            raise ValueError("emission_limit must be a positive finite number")
        if self.out_of_range_policy != "clip":
            raise ValueError("condition model currently requires out_of_range_policy='clip'")
        if self.online.stability_mode.upper() != "MAJORITY":
            raise ValueError("online stability_mode currently requires 'MAJORITY'")
        if int(self.online.stability_window_size) < 1:
            raise ValueError("stability_window_size must be at least 1")
        if self.online.majority_tie_policy.upper() != "KEEP_LAST_STABLE":
            raise ValueError(
                "online majority_tie_policy currently requires 'KEEP_LAST_STABLE'"
            )
        if self.merge.mode not in {"disabled", "evidence_only", "conservative"}:
            raise ValueError("Unsupported merge mode")
        if self.merge.min_observed_samples < 1:
            raise ValueError("min_observed_samples must be at least 1")
        if self.merge.min_mature_samples < self.merge.min_observed_samples:
            raise ValueError("min_mature_samples must be >= min_observed_samples")
        if self.merge.min_auto_merge_samples < self.merge.min_mature_samples:
            raise ValueError("min_auto_merge_samples must be >= min_mature_samples")
        if self.merge.min_auto_confirm_samples < self.merge.min_auto_merge_samples:
            raise ValueError("min_auto_confirm_samples must be >= min_auto_merge_samples")
        for name in (
            "min_common_state_samples",
            "min_risk_samples",
            "min_consecutive_pass_snapshots",
            "min_new_samples_per_member_for_confirmation",
            "max_auto_region_cells",
        ):
            if int(getattr(self.merge, name)) < 1:
                raise ValueError(f"{name} must be at least 1")
        if not 0 < self.merge.min_metric_coverage_ratio <= 1:
            raise ValueError("min_metric_coverage_ratio must be in (0, 1]")
        for name in (
            "max_liquid_gas_relative_difference",
            "max_pump_distribution_distance",
            "max_risk_rate_difference",
        ):
            value = float(getattr(self.merge, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a non-negative finite number")

    def to_dict(self) -> Dict[str, Any]:
        axes = [
            {
                "column": column,
                "min": axis.minimum,
                "max": axis.maximum,
                "step": axis.step,
            }
            for column, axis in self.condition_axes
        ]
        grid_definition = {
            item["column"]: {
                "min": item["min"],
                "max": item["max"],
                "step": item["step"],
            }
            for item in axes
        }
        return {
            "condition_axes": axes,
            "grid_definition": grid_definition,
            "data_columns": self.data_columns.__dict__.copy(),
            "emission_limit": self.emission_limit,
            "out_of_range_policy": self.out_of_range_policy,
            "merge": self.merge.__dict__.copy(),
            "online": self.online.__dict__.copy(),
            "artifact_dir": self.artifact_dir,
        }


def _normalize_condition_axes(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Load new one/two-axis config and migrate historical two-axis config."""

    configured = config.get("condition_axes")
    if configured is not None:
        if isinstance(configured, dict):
            axes = [
                {"column": str(column), **dict(spec or {})}
                for column, spec in configured.items()
            ]
        elif isinstance(configured, (list, tuple)):
            axes = [dict(item or {}) for item in configured]
        else:
            raise TypeError("condition_axes must be a list/tuple or mapping")
    else:
        grid = config.get("grid_definition", config.get("GRID_DEFINITION"))
        if not grid:
            raise KeyError("condition_axes is required")
        axis_columns = config.get("condition_axis_columns", config.get("axis_columns"))
        if axis_columns and len(grid) == 2:
            ordered_columns = [
                str(axis_columns.get("load", "jzfh")),
                str(axis_columns.get("inlet_so2", "yyq_SO2")),
            ]
            axes = [
                {"column": column, **dict(grid[column])}
                for column in ordered_columns
                if column in grid
            ]
            if len(axes) != 2:
                raise KeyError("legacy grid_definition axis does not match axis_columns")
        else:
            axes = [
                {"column": str(column), **dict(spec or {})}
                for column, spec in grid.items()
            ]

    if len(axes) not in {1, 2}:
        raise ValueError(
            "condition_axes must contain exactly 1 or 2 axes; "
            f"got {len(axes)}"
        )

    normalized: List[Dict[str, Any]] = []
    seen = set()
    for index, raw in enumerate(axes, start=1):
        column = str(raw.get("column", "")).strip()
        if not column:
            raise ValueError(f"condition axis {index} column cannot be empty")
        if column in seen:
            raise ValueError(f"duplicate condition axis column: {column}")
        seen.add(column)
        try:
            axis = {
                "column": column,
                "min": float(raw["min"]),
                "max": float(raw["max"]),
                "step": float(raw["step"]),
            }
        except KeyError as exc:
            raise KeyError(
                f"condition axis {column} is missing {exc.args[0]}"
            ) from exc
        AxisConfig(axis["min"], axis["max"], axis["step"]).validate(column)
        normalized.append(axis)
    return normalized


def from_dict(config: Dict[str, Any]) -> ConditionModelConfig:
    axes = _normalize_condition_axes(config)
    first = axes[0]
    first_axis = AxisConfig(first["min"], first["max"], first["step"])
    single_axis_mode = len(axes) == 1

    if single_axis_mode:
        second = first
        second_axis = _SINGLE_AXIS_PADDING
    else:
        second = axes[1]
        second_axis = AxisConfig(second["min"], second["max"], second["step"])

    merge_config = dict(DEFAULT_MERGE_CONFIG)
    merge_config.update(config.get("merge", {}))
    for retired in (
        "min_action_events",
        "manual_approval_required",
        "merge_condition_label_pairs",
    ):
        merge_config.pop(retired, None)

    data_columns = dict(DEFAULT_DATA_COLUMNS)
    data_columns.update(config.get("data_columns", {}))
    for retired in (
        "slurry_flow",
        "xst_slurry_flow",
        "apt_slurry_flow",
        "total_coal",
    ):
        data_columns.pop(retired, None)

    online_config = dict(DEFAULT_ONLINE_CONFIG)
    online_config.update(config.get("online", {}))
    for retired in (
        "load_hysteresis",
        "inlet_so2_hysteresis",
        "minimum_dwell_cycles",
    ):
        online_config.pop(retired, None)
    online_config["stability_mode"] = str(
        online_config.get("stability_mode", "MAJORITY")
    ).upper()
    online_config["majority_tie_policy"] = str(
        online_config.get("majority_tie_policy", "KEEP_LAST_STABLE")
    ).upper()

    result = ConditionModelConfig(
        load=first_axis,
        inlet_so2=second_axis,
        load_column=first["column"],
        inlet_so2_column=second["column"],
        single_axis_mode=single_axis_mode,
        data_columns=DataColumnConfig(**data_columns),
        emission_limit=float(config.get("emission_limit", DEFAULT_EMISSION_LIMIT)),
        out_of_range_policy=str(config.get("out_of_range_policy", "clip")).lower(),
        merge=MergeConfig(**merge_config),
        online=OnlineConfig(**online_config),
        artifact_dir=str(config.get("artifact_dir", "artifacts/condition")),
    )
    result.validate()
    return result


def from_project_config(config: Dict[str, Any]) -> ConditionModelConfig:
    return from_dict(config.get("condition_model_v3", config))


def default_config() -> ConditionModelConfig:
    return from_dict(DEFAULT_CONDITION_MODEL_CONFIG)
