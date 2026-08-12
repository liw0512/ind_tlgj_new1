# -*- coding: utf-8 -*-
"""Configuration for the fixed-grid condition model.

Only ``CONDITION_AXES`` decides which process variables define the operating
condition grid.  One or two numeric axes are supported.  The rest of the
condition-model pipeline (initial training, incremental training, online
classification, automatic merge and snapshot reload) consumes the frozen axis
configuration from the snapshot and does not need plant-specific code changes.

The internal names ``load`` / ``inlet_so2`` that still appear in the dataclass
are compatibility slots for the historical two-axis implementation.  They no
longer imply physical meanings: slot 1 may be any configured process variable,
and slot 2 may be any second configured process variable.  In one-axis mode the
second slot is an internal singleton and requires no additional source field.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# 唯一需要按现场修改的“工况轴”配置。
#
# 支持 1 个或 2 个数值型工况变量。顺序决定内部固定网格顺序：
#   第 1 个轴 -> P1/P2/P3/...（P 仅表示第一轴，不再表示 Power）
#   第 2 个轴 -> S1/S2/S3/...（S 仅表示第二轴，不再表示 SO2）
#
# 电厂默认：机组负荷 + 原烟气 SO2。
# 如果某厂只有原烟气 SO2，可改成：
# CONDITION_AXES = [
#     {"column": "yyq_SO2", "min": 500.0, "max": 7000.0, "step": 200.0},
# ]
#
# 钢厂示例（仅示意字段名）：
# CONDITION_AXES = [
#     {"column": "blast_furnace_load", "min": 100.0, "max": 600.0, "step": 20.0},
#     {"column": "inlet_sulfur", "min": 200.0, "max": 3000.0, "step": 100.0},
# ]
#
# 不建议超过 2 个轴：三个月左右历史数据下，多维笛卡尔网格会迅速造成经验稀疏。
# 若未来确实需要 3 个及以上工况轴，应重新评估工况表达方式，而不是直接继续加维度。
CONDITION_AXES: List[Dict[str, Any]] = [
    {
        "column": "jzfh",
        "min": 100.0,
        "max": 660.0,
        "step": 10.0,
    },
    {
        "column": "yyq_SO2",
        "min": 500.0,
        "max": 7000.0,
        "step": 200.0,
    },
]


DEFAULT_DATA_COLUMNS = {
    "outlet_so2": "jyq_SO2",  # 净烟气 SO2，用于统计超排风险 risk_rate。
    "xst_ph": "xstjy_PH",  # 一级塔浆液 pH，用于工况解释统计 mean_xst_ph。
    "apt_ph": "aptjy_PH",  # 二级塔浆液 pH，用于工况解释统计 mean_apt_ph。
    "liquid_gas": "liquid_gas_ratio",  # 液气比，用于工况合并相似性判断。
}


# 净烟气 SO2 排放限值。risk_rate = jyq_SO2 > DEFAULT_EMISSION_LIMIT 的样本占比。
DEFAULT_EMISSION_LIMIT = 35.0


DEFAULT_MERGE_CONFIG = {
    "enabled": True,  # 是否启用自动合并评估。
    # 合并模式：
    # disabled：关闭自动合并；
    # evidence_only：满足自动合并证据后发布临时合并；
    # conservative：必须达到确认样本门槛后才发布合并。
    "mode": "evidence_only",

    "min_observed_samples": 10,  # 单格样本数达到该值后标记为 OBSERVED。
    "min_mature_samples": 30,  # 单格样本数达到该值后标记为 MATURE。

    "min_auto_merge_samples": 100,  # 相邻工况参与自动合并判断的最低样本数。
    "min_auto_confirm_samples": 300,  # 自动合并从临时状态进入确认状态的最低样本数。
    "min_common_state_samples": 10,  # 泵组合/状态分布对比时，共同状态的最低样本数。
    "min_risk_samples": 30,  # risk_rate 参与合并判断前要求的最低净烟气 SO2 有效样本数。
    "min_metric_coverage_ratio": 0.80,  # 两个工况参与合并的统计项覆盖率下限。
    "min_consecutive_pass_snapshots": 3,  # 连续多少个快照均满足条件后可确认合并。
    "min_new_samples_per_member_for_confirmation": 10,  # 每轮确认要求每个成员新增的最低样本数。
    "max_auto_region_cells": 8,  # 一个自动合并区域最多允许包含的基础工况格数量。

    "max_liquid_gas_relative_difference": 0.15,  # 液气比均值相对差异上限。
    "max_pump_distribution_distance": 0.25,  # 泵组合分布差异上限，越小要求越相似。
    "max_risk_rate_difference": 0.10,  # 超排风险率差异上限。
}


DEFAULT_ONLINE_CONFIG = {
    # 在线稳定方式只保留 condition_label 滑动窗口众数，不再使用轴滞回或连续命中。
    "stability_mode": "MAJORITY",
    "stability_window_size": 6,  # 最近 6 次瞬时 condition_label 取众数。
    "majority_tie_policy": "KEEP_LAST_STABLE",  # 众数并列时优先保持上一稳定标签。
    "allow_provisional_region_fallback": True,  # 是否允许临时合并区域提供经验回退。
}


DEFAULT_CONDITION_MODEL_CONFIG = {
    "condition_axes": CONDITION_AXES,  # 唯一工况轴来源；支持 1 或 2 个任意数值字段。
    "data_columns": DEFAULT_DATA_COLUMNS,  # 工况统计用字段映射。
    "emission_limit": DEFAULT_EMISSION_LIMIT,  # 净烟气 SO2 排放限值。
    "out_of_range_policy": "clip",  # 越界样本处理策略；clip 表示裁剪到边界工况。
    "merge": DEFAULT_MERGE_CONFIG,  # 相邻工况自动合并策略。
    "online": DEFAULT_ONLINE_CONFIG,  # 在线判定防抖和回退策略。
    "artifact_dir": "artifacts/condition",  # 兼容字段，当前主要产物路径由外层配置指定。
}


MAX_SNAPSHOT_VERSIONS_TO_KEEP = 5  # 快照最多保留版本数，超过后自动删除最旧版本。


INITIAL_CONDITION_TRAIN_CONFIG = {
    "input_csv_path": r"F:\tlgj\files\data_preprocessor_test_output_p1_60.csv",  # 单独运行第一模块时的初次训练输入 CSV；P4PC 启动时会由命令行参数覆盖。
    "output_csv_path": r"F:\tlgj\files\Initial_train_after_condition.csv",  # 单独运行时的标注输出 CSV；P4PC 会覆盖。
    "merge_statistics_json_path": r"F:\tlgj\system\model\map_control\condition_model\condition_merge_statistics.json",  # 工况合并累计统计 JSON。
    "auto_merge_report_path": r"F:\tlgj\system\model\map_control\condition_model\snapshots\v001\auto_merge_report.json",  # 初次自动合并评估报告。
    "snapshot_output_path": r"F:\tlgj\system\model\map_control\condition_model\snapshots\v001\condition_snapshot.json",  # 初次快照输出路径。
    "snapshot_version": "v001",  # 初次训练固定发布为 v001。
    "encoding": "utf-8-sig",  # CSV 读写编码。
}


INCREMENTAL_CONDITION_TRAIN_CONFIG = {
    "base_snapshot_path": "latest",  # 增量基准快照；latest 表示自动读取最新可用版本。
    "input_csv_path": r"F:\tlgj\files\data_preprocessor_test_output_p2_30.csv",  # 单独运行第一模块时的增量训练输入 CSV；P4PC 会覆盖。
    "output_csv_path": r"F:\tlgj\files\Incremental_train_after_condition.csv",  # 单独运行时的标注输出 CSV；P4PC 会覆盖。
    "merge_statistics_json_path": r"F:\tlgj\system\model\map_control\condition_model\condition_merge_statistics.json",  # 在已有统计上继续累计。
    "auto_merge_report_path": "auto",  # auto 表示自动写到新快照同级目录。
    "snapshot_output_path": "auto",  # auto 表示基于最新快照自动生成下一版本目录。
    "snapshot_version": "auto",  # auto 表示 v001 后生成 v002，v002 后生成 v003。
    "encoding": "utf-8-sig",  # CSV 读写编码。
}


ONLINE_CONDITION_CLASSIFY_CONFIG = {
    # 集成在线运行不再读取 snapshots 目录中的 latest，而是读取第二模块训练完成后
    # 由 activate_policy_version.py 原子发布的统一 active_version.json。
    # 显式传 --snapshot 时仍可进行静态 CSV/单版本测试。
    "snapshot_path": "active",
    "merge_statistics_json_path": r"F:\tlgj\system\model\map_control\condition_model\condition_merge_statistics.json",  # 兼容保留，在线正式标签以快照为准。
    "input_csv_path": r"F:\tlgj\files\data_preprocessor_test_output_p3_10.csv",  # 在线 CSV 测试输入路径。

    # 最终输出保留：原始输入全部字段 + 第一模块全部工况字段 + 第二模块全部决策字段。
    "output_csv_path": r"F:\tlgj\files\Online_after_condition_and_policy.csv",
    "encoding": "utf-8-sig",  # CSV 读写编码。

    # 第一模块在线输出调用第二模块在线策略的桥接配置。
    "slurry_policy_online": {
        "enabled": True,

        # None 表示直接使用 slurry_policy_model/slurry_policy_config.py。
        # 第二模块仍根据 active_version.json 加载正式激活的同版本策略。
        "config_spec": None,

        # 第一模块负责第一/第二模块同版本原子切换。第二模块在集成模式下禁止
        # 自行抢先热加载 active_version.json，避免 condition vN + policy vN+1。
        "external_version_management": True,

        # 统一版本指针配置。第一模块候选快照和第二模块候选策略全部准备成功后
        # 才在同一在线管线锁内提交。第一模块训练完成但第二模块仍训练时，
        # active_version.json 不变，在线系统继续使用旧版本对。
        "integrated_version": {
            "enabled": True,
            "active_version_file": (
                r"F:\tlgj\files\slurry_policy_model_output"
                r"\active_version.json"
            ),
            "hot_reload_enabled": True,
            "reload_check_interval_seconds": 30.0,
            "verify_condition_snapshot_hash": True,
            "require_atomic_pair_switch": True,
            "reset_condition_stability_window": True,
            "preserve_runtime_control_state": True,
            "keep_current_version_on_failure": True,
        },

        # True：程序启动时初始化第二模块；加载失败时按 failure_mode 处理。
        "initialize_on_start": True,

        # BLOCKED_OUTPUT：第二模块加载/推理失败时保留第一模块结果，并追加安全 HOLD/BLOCKED 输出。
        # RAISE：直接抛出异常并中止处理。生产联调初期建议保留 BLOCKED_OUTPUT。
        "failure_mode": "BLOCKED_OUTPUT",

        # 第二模块字段统一增加该前缀，避免覆盖第一模块的 condition_label、raw_grid_id 等字段。
        "output_prefix": "slurry_policy_",

        # 运行时目标优先级：调用 process(..., target=...) > 本列 > fixed_target > 第二模块默认目标。
        # CSV 中没有该列时自动跳过。
        "target_column": "outlet_so2_target",
        "fixed_target": None,

        # 没有现场执行权限字段时，默认只生成推荐，不代表自动下发。
        "default_execution_context": {
            "automatic_control_allowed": False,
            "manual_valves": [],
            "faulted_valves": [],
            "supply_pump_state_changing": False,
        },

        # 可选：从第一模块原始输入行中读取执行上下文。字段不存在时使用上面的默认值。
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
    outlet_so2: str = "jyq_SO2"
    xst_ph: str = "xstjy_PH"
    apt_ph: str = "aptjy_PH"
    liquid_gas: str = "liquid_gas_ratio"

    def validate(self) -> None:
        for name, value in self.__dict__.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"data column {name} cannot be empty")


@dataclass(frozen=True)
class MergeConfig:
    """Automatic region merge policy.

    ``evidence_only`` publishes provisional merges after the automatic
    thresholds pass. ``conservative`` requires the confirmation sample
    threshold even for provisional publication. Both modes require complete
    liquid-gas and risk evidence; missing risk is never treated as compatible.
    """

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


# 单轴模式下使用的内部第二槽。它只为复用已经稳定的二维网格/合并代码，
# 不对应任何现场测点，也不会造成额外工况切分或越界。
_SINGLE_AXIS_PADDING = AxisConfig(
    minimum=-1.0e100,
    maximum=1.0e100,
    step=2.0e100,
)


@dataclass(frozen=True)
class ConditionModelConfig:
    # 下面四个字段是内部稳定槽位，不再代表固定物理变量。
    # load/load_column = 第一个配置工况轴；
    # inlet_so2/inlet_so2_column = 第二个配置工况轴，单轴模式下为内部占位。
    load: AxisConfig
    inlet_so2: AxisConfig
    load_column: str = "jzfh"
    inlet_so2_column: str = "yyq_SO2"
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
        # grid_definition 仅作为旧工具读取兼容副本，由 condition_axes 同源生成，
        # 不允许用户同时维护两份配置。
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
            # 兼容有人用 {column: {min,max,step}} 的写法。
            axes = [
                {"column": str(column), **dict(spec or {})}
                for column, spec in configured.items()
            ]
        elif isinstance(configured, (list, tuple)):
            axes = [dict(item or {}) for item in configured]
        else:
            raise TypeError("condition_axes must be a list/tuple or mapping")
    else:
        # 旧 snapshot / 旧配置迁移入口。新项目不再要求维护 grid_definition。
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
    # 兼容旧快照中的已退役在线参数；它们不再影响工况切换。
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
        # 单轴时故意复用第一轴字段值；第二槽本身是无限宽单格，不参与划分。
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
