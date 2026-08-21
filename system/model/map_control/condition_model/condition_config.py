# -*- coding: utf-8 -*-
"""Configuration for the fixed-grid condition model.

Plant-specific facts are no longer configured here. ``condition_axes``, tower pH
columns and the outlet-SO2 safety limit are derived from the single authoritative
``system/model/config/plant_config.py``. Standard process field names are fixed by
``system/model/config/standard_fields.py`` and are not configurable aliases.

This file now contains only condition-model algorithm/lifecycle parameters plus a
small read-time migration adapter for historical snapshots.
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
from system.model.config.standard_fields import TARGET_SO2_COLUMN


# ---------------------------------------------------------------------------
# 真正随厂变化的工况轴、塔 pH 与 SO2 安全上限只从 plant_config.py 派生。
CONDITION_AXES: List[Dict[str, Any]] = copy.deepcopy(
    SITE_PLANT_CONFIG["condition_axes"]
)
DEFAULT_TOWER_PH_COLUMNS: Tuple[str, ...] = tuple(
    str(tower.get("ph_column", "")).strip()
    for tower in SITE_PLANT_CONFIG.get("towers", [])
    if tower.get("enabled", True) and str(tower.get("ph_column", "")).strip()
)
DEFAULT_EMISSION_LIMIT = float(SITE_PLANT_CONFIG["outlet_so2_safe_range"][1])


DEFAULT_MERGE_CONFIG = {
    "enabled": True,
    "mode": "evidence_only",
    # 原始训练行已从30秒一条调整为10秒一条；样本门槛乘3以保持原观察时长。
    "min_observed_samples": 30,
    "min_mature_samples": 90,
    "min_auto_merge_samples": 300,
    "min_auto_confirm_samples": 900,
    "min_common_state_samples": 30,
    "min_risk_samples": 90,
    "min_metric_coverage_ratio": 0.80,
    "min_consecutive_pass_snapshots": 3,
    "min_new_samples_per_member_for_confirmation": 30,
    "max_auto_region_cells": 8,
    "max_liquid_gas_relative_difference": 0.15,
    "max_pump_distribution_distance": 0.25,
    "max_risk_rate_difference": 0.10,
}


DEFAULT_ONLINE_CONFIG = {
    "stability_mode": "MAJORITY",
    # 18个10秒决策帧仍对应原来的3分钟多数窗口。
    "stability_window_size": 18,
    "majority_tie_policy": "KEEP_LAST_STABLE",
    "allow_provisional_region_fallback": True,
}


DEFAULT_CONDITION_MODEL_CONFIG = {
    "condition_axes": CONDITION_AXES,
    "tower_ph_columns": list(DEFAULT_TOWER_PH_COLUMNS),
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
        "target_column": TARGET_SO2_COLUMN,
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
class ConditionAxisConfig:
    column: str
    minimum: float
    maximum: float
    step: float

    def validate(self) -> None:
        if not isinstance(self.column, str) or not self.column.strip():
            raise ValueError("condition axis column cannot be empty")
        if not all(math.isfinite(value) for value in (self.minimum, self.maximum, self.step)):
            raise ValueError(f"Non-finite {self.column} grid range")
        if self.step <= 0 or self.maximum <= self.minimum:
            raise ValueError(f"Invalid {self.column} grid range")

    @property
    def cell_count(self) -> int:
        return max(1, int((self.maximum - self.minimum) // self.step))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "column": self.column,
            "min": self.minimum,
            "max": self.maximum,
            "step": self.step,
        }





@dataclass(frozen=True)
class MergeConfig:
    """Automatic region merge policy."""

    enabled: bool = bool(DEFAULT_MERGE_CONFIG["enabled"])
    mode: str = str(DEFAULT_MERGE_CONFIG["mode"])
    min_observed_samples: int = int(DEFAULT_MERGE_CONFIG["min_observed_samples"])
    min_mature_samples: int = int(DEFAULT_MERGE_CONFIG["min_mature_samples"])
    min_auto_merge_samples: int = int(DEFAULT_MERGE_CONFIG["min_auto_merge_samples"])
    min_auto_confirm_samples: int = int(DEFAULT_MERGE_CONFIG["min_auto_confirm_samples"])
    min_common_state_samples: int = int(DEFAULT_MERGE_CONFIG["min_common_state_samples"])
    min_risk_samples: int = int(DEFAULT_MERGE_CONFIG["min_risk_samples"])
    min_metric_coverage_ratio: float = float(DEFAULT_MERGE_CONFIG["min_metric_coverage_ratio"])
    min_consecutive_pass_snapshots: int = int(DEFAULT_MERGE_CONFIG["min_consecutive_pass_snapshots"])
    min_new_samples_per_member_for_confirmation: int = int(DEFAULT_MERGE_CONFIG["min_new_samples_per_member_for_confirmation"])
    max_auto_region_cells: int = int(DEFAULT_MERGE_CONFIG["max_auto_region_cells"])
    max_liquid_gas_relative_difference: float = float(DEFAULT_MERGE_CONFIG["max_liquid_gas_relative_difference"])
    max_pump_distribution_distance: float = float(DEFAULT_MERGE_CONFIG["max_pump_distribution_distance"])
    max_risk_rate_difference: float = float(DEFAULT_MERGE_CONFIG["max_risk_rate_difference"])

    @property
    def auto_publication_sample_threshold(self) -> int:
        if self.mode == "conservative":
            return max(self.min_auto_merge_samples, self.min_auto_confirm_samples)
        return self.min_auto_merge_samples


@dataclass(frozen=True)
class OnlineConfig:
    stability_mode: str = str(DEFAULT_ONLINE_CONFIG["stability_mode"])
    stability_window_size: int = int(DEFAULT_ONLINE_CONFIG["stability_window_size"])
    majority_tie_policy: str = str(DEFAULT_ONLINE_CONFIG["majority_tie_policy"])
    allow_provisional_region_fallback: bool = bool(
        DEFAULT_ONLINE_CONFIG["allow_provisional_region_fallback"]
    )


_SINGLE_AXIS_PADDING = ConditionAxisConfig(
    column="__internal_axis_2__",
    minimum=-1.0e100,
    maximum=1.0e100,
    step=2.0e100,
)


@dataclass(frozen=True)
class ConditionModelConfig:
    condition_axes: Tuple[ConditionAxisConfig, ...]
    tower_ph_columns: Tuple[str, ...] = ()
    emission_limit: float = DEFAULT_EMISSION_LIMIT
    out_of_range_policy: str = "clip"
    merge: MergeConfig = field(default_factory=MergeConfig)
    online: OnlineConfig = field(default_factory=OnlineConfig)
    artifact_dir: str = "artifacts/condition"

    @property
    def condition_axis_columns(self) -> Tuple[str, ...]:
        return tuple(axis.column for axis in self.condition_axes)

    @property
    def condition_axis_count(self) -> int:
        return len(self.condition_axes)

    @property
    def single_axis_mode(self) -> bool:
        return len(self.condition_axes) == 1

    @property
    def axis_1(self) -> ConditionAxisConfig:
        return self.condition_axes[0]

    @property
    def axis_2(self) -> ConditionAxisConfig:
        return _SINGLE_AXIS_PADDING if self.single_axis_mode else self.condition_axes[1]

    def validate(self) -> None:
        if len(self.condition_axes) not in {1, 2}:
            raise ValueError("condition_axes must contain exactly 1 or 2 axes")
        seen = set()
        for axis in self.condition_axes:
            axis.validate()
            if axis.column in seen:
                raise ValueError(f"duplicate condition axis column: {axis.column}")
            seen.add(axis.column)
        for column in self.tower_ph_columns:
            if not isinstance(column, str) or not column.strip():
                raise ValueError("tower pH column cannot be empty")
        if len(set(self.tower_ph_columns)) != len(self.tower_ph_columns):
            raise ValueError("tower pH columns must be unique")
        if not math.isfinite(self.emission_limit) or self.emission_limit <= 0:
            raise ValueError("emission_limit must be a positive finite number")
        if self.out_of_range_policy != "clip":
            raise ValueError("condition model currently requires out_of_range_policy='clip'")
        if self.online.stability_mode.upper() != "MAJORITY":
            raise ValueError("online stability_mode currently requires 'MAJORITY'")
        if int(self.online.stability_window_size) < 1:
            raise ValueError("stability_window_size must be at least 1")
        if self.online.majority_tie_policy.upper() != "KEEP_LAST_STABLE":
            raise ValueError("online majority_tie_policy currently requires 'KEEP_LAST_STABLE'")
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
        return {
            "condition_axes": [axis.to_dict() for axis in self.condition_axes],
            "tower_ph_columns": list(self.tower_ph_columns),
            "emission_limit": self.emission_limit,
            "out_of_range_policy": self.out_of_range_policy,
            "merge": self.merge.__dict__.copy(),
            "online": self.online.__dict__.copy(),
            "artifact_dir": self.artifact_dir,
        }


def _normalize_condition_axes(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Read the current generic axis format and migrate historical snapshots."""
    configured = config.get("condition_axes")
    if configured is not None:
        if isinstance(configured, dict):
            axes = [{"column": str(column), **dict(spec or {})} for column, spec in configured.items()]
        elif isinstance(configured, (list, tuple)):
            axes = [dict(item or {}) for item in configured]
        else:
            raise TypeError("condition_axes must be a list/tuple or mapping")
    else:
        # Historical snapshot-only migration path. New snapshots never write grid_definition.
        grid = config.get("grid_definition", config.get("GRID_DEFINITION"))
        if not grid:
            raise KeyError("condition_axes is required")
        axis_columns = config.get("condition_axis_columns", config.get("axis_columns"))
        if axis_columns and len(grid) == 2:
            ordered_columns = [
                str(axis_columns.get("load", "jzfh")),
                str(axis_columns.get("inlet_so2", "yyq_SO2")),
            ]
            axes = [{"column": column, **dict(grid[column])} for column in ordered_columns if column in grid]
            if len(axes) != 2:
                raise KeyError("legacy grid_definition axis does not match axis_columns")
        else:
            axes = [{"column": str(column), **dict(spec or {})} for column, spec in grid.items()]

    if len(axes) not in {1, 2}:
        raise ValueError(f"condition_axes must contain exactly 1 or 2 axes; got {len(axes)}")

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
            raise KeyError(f"condition axis {column} is missing {exc.args[0]}") from exc
        ConditionAxisConfig(column, axis["min"], axis["max"], axis["step"]).validate()
        normalized.append(axis)
    return normalized


def from_dict(config: Dict[str, Any]) -> ConditionModelConfig:
    axes = _normalize_condition_axes(config)
    axis_models = tuple(
        ConditionAxisConfig(
            column=item["column"],
            minimum=item["min"],
            maximum=item["max"],
            step=item["step"],
        )
        for item in axes
    )

    merge_config = dict(DEFAULT_MERGE_CONFIG)
    merge_config.update(config.get("merge", {}))
    for retired in ("min_action_events", "manual_approval_required", "merge_condition_label_pairs"):
        merge_config.pop(retired, None)

    online_config = dict(DEFAULT_ONLINE_CONFIG)
    online_config.update(config.get("online", {}))
    for retired in ("load_hysteresis", "inlet_so2_hysteresis", "minimum_dwell_cycles"):
        online_config.pop(retired, None)
    online_config["stability_mode"] = str(online_config.get("stability_mode", "MAJORITY")).upper()
    online_config["majority_tie_policy"] = str(online_config.get("majority_tie_policy", "KEEP_LAST_STABLE")).upper()

    raw_ph_columns = config.get("tower_ph_columns")
    if raw_ph_columns is None:
        # Historical snapshot-only migration path for the retired data_columns alias map.
        legacy = dict(config.get("data_columns") or {})
        raw_ph_columns = [legacy.get("xst_ph"), legacy.get("apt_ph")]
        raw_ph_columns = [
            value for value in raw_ph_columns
            if value and not str(value).startswith("__unused_condition_ph_")
        ]
        if not raw_ph_columns:
            raw_ph_columns = list(DEFAULT_TOWER_PH_COLUMNS)
    tower_ph_columns = tuple(str(value).strip() for value in raw_ph_columns if str(value).strip())

    result = ConditionModelConfig(
        condition_axes=axis_models,
        tower_ph_columns=tower_ph_columns,
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
