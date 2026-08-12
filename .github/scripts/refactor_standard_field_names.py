from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _replace_node(source: str, name: str, replacement: str, class_name: str | None = None) -> str:
    tree = ast.parse(source)
    target = None
    nodes = tree.body
    if class_name is not None:
        cls = next((node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name), None)
        if cls is None:
            raise RuntimeError(f"class not found: {class_name}")
        nodes = cls.body
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            target = node
            break
    if target is None or target.end_lineno is None:
        raise RuntimeError(f"node not found: {class_name + '.' if class_name else ''}{name}")
    lines = source.splitlines(keepends=True)
    code = replacement.rstrip() + "\n"
    lines[target.lineno - 1:target.end_lineno] = [code]
    return "".join(lines)


def _remove_node(source: str, name: str) -> str:
    return _replace_node(source, name, "")


def _write(path: Path, content: str) -> None:
    ast.parse(content) if path.suffix == ".py" else None
    path.write_text(content, encoding="utf-8")


def write_standard_fields() -> None:
    path = ROOT / "system/model/config/standard_fields.py"
    path.write_text(
        '''"""供浆算法标准数据接口字段。

这些字段不是“厂级可配置映射”，而是 data_preprocessor1 之后的固定接口契约。
不同现场原始 DCS 点名如果不同，应在数据接入/预处理层映射成这些标准字段；
condition_model、slurry_policy_model、P4PC 和数据库之后都直接使用同名字段。
"""
from __future__ import annotations

TIME_COLUMN = "date"
OUTLET_SO2_COLUMN = "jyq_SO2"
LIQUID_GAS_RATIO_COLUMN = "liquid_gas_ratio"
TARGET_SO2_COLUMN = "outlet_so2_target"

STANDARD_PROCESS_FIELDS = (
    TIME_COLUMN,
    OUTLET_SO2_COLUMN,
    LIQUID_GAS_RATIO_COLUMN,
    TARGET_SO2_COLUMN,
)
''',
        encoding="utf-8",
    )


def rewrite_plant_config() -> None:
    path = ROOT / "system/model/config/plant_config.py"
    source = path.read_text(encoding="utf-8")
    source = source.replace(
        "- 时间、净烟气 SO2、液气比、在线目标等现场字段；\n",
        "- 工况轴选择及其范围；\n",
    )
    source = source.replace(
        "换厂时只修改本文件中的 ``PLANT_CONFIG``。第一模块 condition_model、第二模块\nslurry_policy_model 以及 P4PC 集成层都从这里读取厂级事实，不再分别维护重复配置。",
        "换厂时只修改本文件中的 ``PLANT_CONFIG``。第一模块 condition_model、第二模块\nslurry_policy_model 以及 P4PC 集成层都从这里读取真正随厂变化的物理事实。\n标准过程字段名已经固定在 ``standard_fields.py``，不再在这里做二次字段映射。",
    )
    source = re.sub(
        r'\n\s*# ------------------------------------------------------------------\n\s*# 现场公共信号字段。第一/第二模块需要同一物理量时都从这里读取。\n\s*"time_column": "date",\n\s*"process_columns": \{\n\s*"outlet_so2": "jyq_SO2",\n\s*"liquid_gas": "liquid_gas_ratio",\n\s*"target_so2": "outlet_so2_target",\n\s*\},\n',
        "\n",
        source,
        count=1,
    )
    _write(path, source)


def rewrite_condition_config() -> None:
    path = ROOT / "system/model/map_control/condition_model/condition_config.py"
    source = path.read_text(encoding="utf-8")
    source = source.replace(
        "Plant-specific facts are no longer configured here.  ``condition_axes``, field\nnames, tower pH columns and the outlet-SO2 safety limit are derived from the\nsingle authoritative ``system/model/config/plant_config.py``.\n\nThis file now contains only condition-model algorithm/lifecycle parameters and\ncompatibility adapters for historical snapshots.  The internal names\n``load`` / ``inlet_so2`` and ``xst_ph`` / ``apt_ph`` are legacy slots only;\nthey no longer imply fixed physical meanings.",
        "Plant-specific facts are no longer configured here. ``condition_axes``, tower pH\ncolumns and the outlet-SO2 safety limit are derived from the single authoritative\n``system/model/config/plant_config.py``. Standard process field names are fixed by\n``system/model/config/standard_fields.py`` and are not configurable aliases.\n\nThis file now contains only condition-model algorithm/lifecycle parameters plus a\nsmall read-time migration adapter for historical snapshots.",
    )

    start = source.index("# ---------------------------------------------------------------------------\n# 厂级事实")
    end = source.index("DEFAULT_MERGE_CONFIG = {")
    prefix = '''# ---------------------------------------------------------------------------
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


'''
    source = source[:start] + prefix + source[end:]

    old_default_start = source.index("DEFAULT_CONDITION_MODEL_CONFIG = {")
    old_default_end = source.index("\n\n\nMAX_SNAPSHOT_VERSIONS_TO_KEEP", old_default_start)
    new_default = '''DEFAULT_CONDITION_MODEL_CONFIG = {
    "condition_axes": CONDITION_AXES,
    "tower_ph_columns": list(DEFAULT_TOWER_PH_COLUMNS),
    "emission_limit": DEFAULT_EMISSION_LIMIT,
    "out_of_range_policy": "clip",
    "merge": DEFAULT_MERGE_CONFIG,
    "online": DEFAULT_ONLINE_CONFIG,
    "artifact_dir": "artifacts/condition",
}'''
    source = source[:old_default_start] + new_default + source[old_default_end:]

    source = _replace_node(source, "AxisConfig", '''@dataclass(frozen=True)
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
''')
    source = _remove_node(source, "DataColumnConfig")

    source = re.sub(
        r'_SINGLE_AXIS_PADDING = AxisConfig\(\n\s*minimum=-1\.0e100,\n\s*maximum=1\.0e100,\n\s*step=2\.0e100,\n\)',
        '_SINGLE_AXIS_PADDING = ConditionAxisConfig(\n    column="__internal_axis_2__",\n    minimum=-1.0e100,\n    maximum=1.0e100,\n    step=2.0e100,\n)',
        source,
        count=1,
    )

    source = _replace_node(source, "ConditionModelConfig", '''@dataclass(frozen=True)
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
''')

    source = _replace_node(source, "_normalize_condition_axes", '''def _normalize_condition_axes(config: Dict[str, Any]) -> List[Dict[str, Any]]:
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
''')

    source = _replace_node(source, "from_dict", '''def from_dict(config: Dict[str, Any]) -> ConditionModelConfig:
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
''')

    source = source.replace("AxisConfig(", "ConditionAxisConfig(")
    # Undo the constructor replacements already intentionally using the 4-argument new class is not needed.
    _write(path, source)


def rewrite_condition_schema() -> None:
    path = ROOT / "system/model/map_control/condition_model/condition_schema.py"
    source = path.read_text(encoding="utf-8")
    import_marker = "from typing import Any, Dict, List, Optional, Tuple\n"
    addition = '''from system.model.config.plant_config import PLANT_CONFIG
from system.model.config.standard_fields import LIQUID_GAS_RATIO_COLUMN, OUTLET_SO2_COLUMN

_ENABLED_PH_COLUMNS = tuple(
    str(tower.get("ph_column", "")).strip()
    for tower in PLANT_CONFIG.get("towers", [])
    if tower.get("enabled", True) and str(tower.get("ph_column", "")).strip()
)
'''
    if addition not in source:
        source = source.replace(import_marker, import_marker + "\n" + addition)

    source = _replace_node(source, "_migrate_statistics", '''def _migrate_statistics(value: Dict[str, Any]) -> Dict[str, Any]:
    """Convert retired snapshot statistic aliases to standard field-based keys."""
    statistics = dict(_finite_or_none(value or {}))

    fallback_map = {
        f"mean_{LIQUID_GAS_RATIO_COLUMN}": ("mean_liquid_gas", "median_liquid_gas"),
        f"mean_{OUTLET_SO2_COLUMN}": ("mean_net_so2", "median_net_so2"),
    }
    if _ENABLED_PH_COLUMNS:
        fallback_map[f"mean_{_ENABLED_PH_COLUMNS[0]}"] = ("mean_xst_ph", "mean_ph", "median_ph")
    if len(_ENABLED_PH_COLUMNS) > 1:
        fallback_map[f"mean_{_ENABLED_PH_COLUMNS[1]}"] = ("mean_apt_ph", "median_apt_ph")

    for target, sources in fallback_map.items():
        if statistics.get(target) is None:
            for source in sources:
                if statistics.get(source) is not None:
                    statistics[target] = statistics[source]
                    break

    for retired in (
        "median_liquid_gas", "mean_liquid_gas", "median_ph", "mean_ph",
        "mean_xst_ph", "mean_apt_ph", "median_apt_ph", "mean_total_coal",
        "median_total_coal", "median_net_so2", "mean_net_so2",
        "median_slurry_flow", "mean_slurry_flow",
    ):
        statistics.pop(retired, None)
    return statistics
''')

    source = _replace_node(source, "_migrate_accumulators", '''def _migrate_accumulators(value: Dict[str, Any]) -> Dict[str, Any]:
    """Convert retired accumulator aliases while loading historical snapshots."""
    accumulators = dict(_finite_or_none(value or {}))
    numeric = dict(accumulators.get("numeric") or {})

    alias_map = {
        "liquid_gas": LIQUID_GAS_RATIO_COLUMN,
        "net_so2": OUTLET_SO2_COLUMN,
    }
    if _ENABLED_PH_COLUMNS:
        alias_map["xst_ph"] = _ENABLED_PH_COLUMNS[0]
        alias_map["ph"] = _ENABLED_PH_COLUMNS[0]
    if len(_ENABLED_PH_COLUMNS) > 1:
        alias_map["apt_ph"] = _ENABLED_PH_COLUMNS[1]

    for old, new in alias_map.items():
        if new not in numeric and old in numeric:
            numeric[new] = numeric[old]
    for retired in ("liquid_gas", "net_so2", "xst_ph", "apt_ph", "ph", "slurry_flow", "total_coal"):
        numeric.pop(retired, None)

    accumulators["numeric"] = numeric
    return accumulators
''')

    source = _replace_node(source, "GridCell", '''@dataclass
class GridCell:
    grid_id: str
    axis_1_level: int
    axis_2_level: int
    axis_1_range: Tuple[float, float]
    axis_2_range: Tuple[float, float]
    validity: str = "VALID"
    coverage_status: str = "EMPTY"
    policy_region_id: str = ""
    sample_count: int = 0
    clipped_count: int = 0
    state_profiles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    pump_distribution: Dict[str, int] = field(default_factory=dict)
    statistics: Dict[str, Any] = field(default_factory=dict)
    accumulators: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["axis_1_range"] = list(self.axis_1_range)
        value["axis_2_range"] = list(self.axis_2_range)
        return value

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "GridCell":
        data = dict(value)
        data.pop("action_event_count", None)
        data.pop("confidence", None)

        # Historical snapshot-only axis migration. New snapshots only write axis_1/axis_2 names.
        legacy_axis_fields = {
            "load_level": "axis_1_level",
            "inlet_so2_level": "axis_2_level",
            "load_range": "axis_1_range",
            "inlet_so2_range": "axis_2_range",
        }
        for old, new in legacy_axis_fields.items():
            if new not in data and old in data:
                data[new] = data[old]
            data.pop(old, None)

        data["axis_1_range"] = tuple(data["axis_1_range"])
        data["axis_2_range"] = tuple(data["axis_2_range"])
        clean_profiles: Dict[str, Dict[str, Any]] = {}
        for state_key, profile in (data.get("state_profiles") or {}).items():
            clean_profile = dict(_finite_or_none(profile or {}))
            clean_profile.pop("action_event_count", None)
            clean_profiles[str(state_key)] = clean_profile
        data["state_profiles"] = clean_profiles
        data["statistics"] = _migrate_statistics(data.get("statistics") or {})
        data["accumulators"] = _migrate_accumulators(data.get("accumulators") or {})
        return cls(**data)
''')
    _write(path, source)


def rewrite_grid_definition() -> None:
    path = ROOT / "system/model/map_control/condition_model/grid_definition.py"
    path.write_text('''# -*- coding: utf-8 -*-
"""Generic one/two-axis fixed-grid creation, mapping and adjacency.

P/S remain stable grid-id slot codes only. All runtime/config names are generic
axis_1/axis_2 and never imply load or inlet SO2 semantics.
"""

import math
from typing import Dict, List, Tuple

from system.model.map_control.condition_model.condition_config import (
    ConditionAxisConfig,
    ConditionModelConfig,
)
from system.model.map_control.condition_model.condition_schema import GridCell


def create_complete_grid(config: ConditionModelConfig) -> Dict[str, GridCell]:
    config.validate()
    axis_1 = config.axis_1
    axis_2 = config.axis_2
    catalog = {}
    for p_index in range(axis_1.cell_count):
        first_low = axis_1.minimum + p_index * axis_1.step
        first_high = axis_1.maximum if p_index == axis_1.cell_count - 1 else first_low + axis_1.step
        for s_index in range(axis_2.cell_count):
            second_low = axis_2.minimum + s_index * axis_2.step
            second_high = axis_2.maximum if s_index == axis_2.cell_count - 1 else second_low + axis_2.step
            grid_id = f"P{p_index + 1}-S{s_index + 1}"
            catalog[grid_id] = GridCell(
                grid_id=grid_id,
                axis_1_level=p_index + 1,
                axis_2_level=s_index + 1,
                axis_1_range=(first_low, first_high),
                axis_2_range=(second_low, second_high),
                policy_region_id=f"R_{grid_id.replace('-', '_')}",
            )
    return catalog


def _locate(value: float, axis: ConditionAxisConfig) -> Tuple[int, bool]:
    clipped = value < axis.minimum or value > axis.maximum
    bounded = min(max(value, axis.minimum), axis.maximum)
    if math.isclose(bounded, axis.maximum):
        return axis.cell_count, clipped
    return min(axis.cell_count, int((bounded - axis.minimum) // axis.step) + 1), clipped


def locate_grid(first_axis_value: float, second_axis_value: float, config: ConditionModelConfig) -> Tuple[str, bool, str]:
    """Locate one row in the configured one/two-axis fixed grid."""
    p_level, first_clipped = _locate(float(first_axis_value), config.axis_1)
    s_level, second_clipped = _locate(float(second_axis_value), config.axis_2)

    if first_clipped and second_clipped and not config.single_axis_mode:
        clip_axis = ",".join(config.condition_axis_columns)
    elif first_clipped:
        clip_axis = config.axis_1.column
    elif second_clipped and not config.single_axis_mode:
        clip_axis = config.axis_2.column
    else:
        clip_axis = "none"

    return (
        f"P{p_level}-S{s_level}",
        first_clipped or (second_clipped and not config.single_axis_mode),
        clip_axis,
    )


def build_fixed_adjacency(catalog: Dict[str, GridCell]) -> Dict[str, List[str]]:
    by_coordinate = {(cell.axis_1_level, cell.axis_2_level): cell.grid_id for cell in catalog.values()}
    result = {}
    for cell in catalog.values():
        neighbors = []
        for delta_p, delta_s in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            neighbor = by_coordinate.get((cell.axis_1_level + delta_p, cell.axis_2_level + delta_s))
            if neighbor:
                neighbors.append(neighbor)
        result[cell.grid_id] = sorted(neighbors)
    return result
''', encoding="utf-8")


def rewrite_initial_builder() -> None:
    path = ROOT / "system/model/map_control/condition_model/initial_condition_builder.py"
    source = path.read_text(encoding="utf-8")
    import_marker = "import pandas as pd\n"
    addition = '''\nfrom system.model.config.standard_fields import (\n    LIQUID_GAS_RATIO_COLUMN,\n    OUTLET_SO2_COLUMN,\n)\n'''
    if addition not in source:
        source = source.replace(import_marker, import_marker + addition)

    source = re.sub(r'\nNUMERIC_STATISTIC_KEYS = \(.*?\n\)\n', '\n', source, count=1, flags=re.S)
    source = source.replace("- condition axes + liquid/gas + outlet SO2 are the structural training inputs;", "- condition axes + liquid_gas_ratio + jyq_SO2 are the structural training inputs;")
    source = source.replace("- tower pH columns are optional explanatory statistics for this first module;\n- missing XST/APT pH never changes grid mapping or condition_label.", "- enabled-tower pH columns are optional explanatory statistics;\n- missing tower pH never changes grid mapping or condition_label.")

    source = _replace_node(source, "normalize_and_validate_training_frame", '''def normalize_and_validate_training_frame(
    frame: pd.DataFrame,
    config: ConditionModelConfig,
    context: str = "training",
) -> pd.DataFrame:
    """Validate the fixed standard interface plus configured condition axes."""
    normalized = frame.copy()
    normalized.columns = [str(column).replace("\\ufeff", "").strip() for column in normalized.columns]
    duplicates = normalized.columns[normalized.columns.duplicated()].tolist()
    if duplicates:
        raise ValueError(f"{context} CSV contains duplicate columns after normalization: {duplicates}")

    required: Dict[str, str] = {
        f"condition_axis_{index}": column
        for index, column in enumerate(config.condition_axis_columns, start=1)
    }
    required.update({
        LIQUID_GAS_RATIO_COLUMN: LIQUID_GAS_RATIO_COLUMN,
        OUTLET_SO2_COLUMN: OUTLET_SO2_COLUMN,
    })

    missing = [f"{logical_name}={column}" for logical_name, column in required.items() if column not in normalized.columns]
    if missing:
        raise ValueError(
            f"{context} CSV is missing required standard/configured columns: {', '.join(missing)}; "
            f"actual columns={list(normalized.columns)}"
        )

    invalid = []
    for logical_name, column in required.items():
        numeric = pd.to_numeric(normalized[column], errors="coerce")
        finite_count = int(numeric.map(lambda value: bool(pd.notna(value) and math.isfinite(float(value)))).sum())
        if finite_count == 0:
            invalid.append(f"{logical_name}={column}")
    if invalid:
        raise ValueError(f"{context} CSV has no finite numeric values in: {', '.join(invalid)}")
    return normalized
''')

    source = _replace_node(source, "get_condition_axis_values", '''def get_condition_axis_values(row: Dict[str, Any], config: ConditionModelConfig) -> tuple:
    first_value = _safe_float(row[config.axis_1.column])
    if first_value is None:
        raise ValueError(f"condition axis {config.axis_1.column!r} contains a missing or non-finite value")
    if config.single_axis_mode:
        return first_value, first_value
    second_value = _safe_float(row[config.axis_2.column])
    if second_value is None:
        raise ValueError(f"condition axis {config.axis_2.column!r} contains a missing or non-finite value")
    return first_value, second_value
''')

    source = _replace_node(source, "_empty_value_lists", '''def _empty_value_lists(config: ConditionModelConfig) -> Dict[str, List[Any]]:
    keys = [LIQUID_GAS_RATIO_COLUMN, *config.tower_ph_columns, OUTLET_SO2_COLUMN]
    result = {key: [] for key in dict.fromkeys(keys)}
    result["risk"] = []
    return result
''')
    source = source.replace("grid_id: _empty_value_lists()", "grid_id: _empty_value_lists(self.config)")

    source = _replace_node(source, "_add_row", '''    def _add_row(
        self,
        catalog: Dict[str, Any],
        values_by_grid: Dict[str, Dict[str, List[Any]]],
        row: Dict[str, Any],
    ) -> None:
        if row.get("condition_mapping_ok", True) is False:
            return
        try:
            first_value, second_value = get_condition_axis_values(row, self.config)
            grid_id, clipped, _ = locate_grid(first_value, second_value, self.config)
        except (KeyError, TypeError, ValueError, OverflowError):
            return

        cell = catalog[grid_id]
        cell.sample_count += 1
        cell.clipped_count += int(clipped)
        state_key = build_state_key(row)
        profile = cell.state_profiles.setdefault(state_key, {"sample_count": 0})
        profile["sample_count"] += 1
        pump_key = "-".join(state_key.split("-")[:2])
        cell.pump_distribution[pump_key] = cell.pump_distribution.get(pump_key, 0) + 1

        values = values_by_grid[grid_id]
        self._append(values[LIQUID_GAS_RATIO_COLUMN], row.get(LIQUID_GAS_RATIO_COLUMN))
        for ph_column in self.config.tower_ph_columns:
            if ph_column in values:
                self._append(values[ph_column], row.get(ph_column))
        self._append(values[OUTLET_SO2_COLUMN], row.get(OUTLET_SO2_COLUMN))
        outlet_so2 = _safe_float(row.get(OUTLET_SO2_COLUMN))
        if outlet_so2 is not None:
            values["risk"].append(outlet_so2 > self.config.emission_limit)
''', class_name="InitialConditionBuilder")

    source = _replace_node(source, "_grid_id_to_base_id", '''def _grid_id_to_base_id(grid_id: str, config: ConditionModelConfig) -> str:
    first_part, second_part = grid_id.split("-")
    first_level = int(first_part[1:])
    second_level = int(second_part[1:])
    return str((first_level - 1) * config.axis_2.cell_count + second_level)
''')

    source = _replace_node(source, "build_cell_accumulators", '''def build_cell_accumulators(values: Dict[str, List[Any]]) -> Dict[str, Any]:
    risk_values = values.get("risk", [])
    numeric_keys = [key for key in values if key != "risk"]
    return {
        "numeric": {key: _numeric_accumulator(values.get(key, [])) for key in numeric_keys},
        "risk": {
            "valid_count": len(risk_values),
            "risk_count": sum(1 for value in risk_values if bool(value)),
        },
        "statistics_quality": "EXACT_INITIAL",
    }
''')

    source = _replace_node(source, "ensure_cell_accumulators", '''def ensure_cell_accumulators(cell: Any, config: ConditionModelConfig) -> None:
    """Create current standard-field accumulators and migrate retired snapshot aliases."""
    sample_count = int(cell.sample_count or 0)
    existing = dict(cell.accumulators or {})
    numeric = dict(existing.get("numeric") or {})

    alias_map = {
        "liquid_gas": LIQUID_GAS_RATIO_COLUMN,
        "net_so2": OUTLET_SO2_COLUMN,
    }
    if config.tower_ph_columns:
        alias_map["xst_ph"] = config.tower_ph_columns[0]
        alias_map["ph"] = config.tower_ph_columns[0]
    if len(config.tower_ph_columns) > 1:
        alias_map["apt_ph"] = config.tower_ph_columns[1]
    for old, new in alias_map.items():
        if new not in numeric and old in numeric:
            numeric[new] = numeric[old]
    for retired in ("liquid_gas", "net_so2", "xst_ph", "apt_ph", "ph", "slurry_flow", "total_coal"):
        numeric.pop(retired, None)

    statistic_aliases: Dict[str, tuple[str, ...]] = {
        LIQUID_GAS_RATIO_COLUMN: (f"mean_{LIQUID_GAS_RATIO_COLUMN}", "mean_liquid_gas", "median_liquid_gas"),
        OUTLET_SO2_COLUMN: (f"mean_{OUTLET_SO2_COLUMN}", "mean_net_so2", "median_net_so2"),
    }
    if config.tower_ph_columns:
        statistic_aliases[config.tower_ph_columns[0]] = (
            f"mean_{config.tower_ph_columns[0]}", "mean_xst_ph", "mean_ph", "median_ph"
        )
    if len(config.tower_ph_columns) > 1:
        statistic_aliases[config.tower_ph_columns[1]] = (
            f"mean_{config.tower_ph_columns[1]}", "mean_apt_ph", "median_apt_ph"
        )

    migrated_numeric: Dict[str, Dict[str, Any]] = {}
    for name in dict.fromkeys([LIQUID_GAS_RATIO_COLUMN, *config.tower_ph_columns, OUTLET_SO2_COLUMN]):
        if name in numeric:
            migrated_numeric[name] = _sanitize_numeric_accumulator(numeric[name])
        else:
            migrated_numeric[name] = _safe_numeric_accumulator_from_legacy(
                _legacy_statistic(cell, *statistic_aliases.get(name, (f"mean_{name}",))),
                sample_count,
            )

    risk_rate = _safe_float(cell.statistics.get("risk_rate"))
    old_risk = dict(existing.get("risk") or {})
    risk_valid_count = max(0, int(_safe_float(old_risk.get("valid_count")) or 0))
    risk_count = max(0, int(_safe_float(old_risk.get("risk_count")) or 0))
    if risk_valid_count == 0 and risk_rate is not None:
        risk_valid_count = sample_count
        risk_count = int(round(risk_rate * risk_valid_count))
    risk_count = min(risk_count, risk_valid_count)

    cell.accumulators = {
        "numeric": migrated_numeric,
        "risk": {"valid_count": risk_valid_count, "risk_count": risk_count},
        "statistics_quality": existing.get("statistics_quality", "MIGRATED_LEGACY_APPROX"),
    }
''')

    source = _replace_node(source, "finalize_cell_from_accumulators", '''def finalize_cell_from_accumulators(cell: Any, config: ConditionModelConfig) -> None:
    ensure_cell_accumulators(cell, config)
    numeric = cell.accumulators.get("numeric", {})
    risk = cell.accumulators.get("risk", {})

    def mean(name: str) -> Optional[float]:
        accumulator = _sanitize_numeric_accumulator(numeric.get(name, {}))
        numeric[name] = accumulator
        count = int(accumulator["count"])
        return accumulator["sum"] / count if count else None

    _set_coverage_status(cell, config)
    risk_valid_count = int(risk.get("valid_count", 0))
    risk_count = int(risk.get("risk_count", 0))
    statistics = {
        f"mean_{LIQUID_GAS_RATIO_COLUMN}": mean(LIQUID_GAS_RATIO_COLUMN),
        f"mean_{OUTLET_SO2_COLUMN}": mean(OUTLET_SO2_COLUMN),
        "risk_rate": risk_count / risk_valid_count if risk_valid_count else None,
    }
    for ph_column in config.tower_ph_columns:
        statistics[f"mean_{ph_column}"] = mean(ph_column)
    cell.statistics = statistics
''')

    source = _replace_node(source, "update_merge_statistics", '''def update_merge_statistics(
    statistics: Dict[str, Any],
    rows: Iterable[Dict[str, Any]],
    config: ConditionModelConfig,
) -> Dict[str, Any]:
    base_conditions = statistics.setdefault("base_conditions", {})
    base_to_label = statistics.setdefault("base_to_label", {})
    for row in rows:
        if row.get("condition_mapping_ok", True) is False:
            continue
        label = condition_label_for_row(row, config)
        base_id = label["base_condition_id"]
        if not base_id:
            continue
        base_to_label.setdefault(base_id, base_id)
        item = base_conditions.setdefault(base_id, _new_condition_item(label["base_grid_id"]))
        item["sample_count"] += 1

        liquid_gas = _safe_float(row.get(LIQUID_GAS_RATIO_COLUMN))
        if liquid_gas is not None:
            item["liquid_gas_sum"] = (_safe_float(item.get("liquid_gas_sum")) or 0.0) + liquid_gas
            item["liquid_gas_count"] = int(item.get("liquid_gas_count", 0)) + 1
            item["liquid_gas_mean"] = item["liquid_gas_sum"] / item["liquid_gas_count"]

        state_key = label["state_key"]
        item["state_profile_distribution"][state_key] = item["state_profile_distribution"].get(state_key, 0) + 1
        pump_key = "-".join(state_key.split("-")[:2])
        item["pump_distribution"][pump_key] = item["pump_distribution"].get(pump_key, 0) + 1
    return rebuild_condition_regions(statistics)
''')

    source = source.replace('metadata={"snapshot_schema_version": "5.0"}', 'metadata={"snapshot_schema_version": "6.0"}')
    _write(path, source)


def rewrite_incremental() -> None:
    path = ROOT / "system/model/map_control/condition_model/incremental_condition_updater.py"
    source = path.read_text(encoding="utf-8")
    import_marker = "import pandas as pd\n"
    addition = '''\nfrom system.model.config.standard_fields import (\n    LIQUID_GAS_RATIO_COLUMN,\n    OUTLET_SO2_COLUMN,\n)\n'''
    if addition not in source:
        source = source.replace(import_marker, import_marker + addition)

    old_check = '''        if (\n            frozen_config.load != self.config.load\n            or frozen_config.inlet_so2 != self.config.inlet_so2\n            or frozen_config.load_column != self.config.load_column\n            or frozen_config.inlet_so2_column != self.config.inlet_so2_column\n            or frozen_config.data_columns != self.config.data_columns\n            or frozen_config.emission_limit != self.config.emission_limit\n        ):'''
    new_check = '''        if (\n            frozen_config.condition_axes != self.config.condition_axes\n            or frozen_config.tower_ph_columns != self.config.tower_ph_columns\n            or frozen_config.emission_limit != self.config.emission_limit\n        ):'''
    if old_check not in source:
        raise RuntimeError("incremental config equality block not found")
    source = source.replace(old_check, new_check)
    source = source.replace('"Incremental update cannot change the published grid, field "\n                "mapping, or emission limit"', '"Incremental update cannot change the published condition axes, tower pH fields, or emission limit"')
    source = source.replace("ensure_cell_accumulators(cell)", "ensure_cell_accumulators(cell, self.config)")

    source = _replace_node(source, "_add_incremental_row", '''    def _add_incremental_row(self, snapshot: ConditionSnapshot, row: Dict[str, Any]) -> None:
        if row.get("condition_mapping_ok", True) is False:
            return
        try:
            first_value, second_value = get_condition_axis_values(row, self.config)
            grid_id, clipped, _ = locate_grid(first_value, second_value, self.config)
        except (KeyError, TypeError, ValueError, OverflowError):
            return

        cell = snapshot.grid_catalog[grid_id]
        ensure_cell_accumulators(cell, self.config)
        cell.sample_count += 1
        cell.clipped_count += int(clipped)
        state_key = build_state_key(row)
        profile = cell.state_profiles.setdefault(state_key, {"sample_count": 0})
        profile["sample_count"] += 1
        pump_key = "-".join(state_key.split("-")[:2])
        cell.pump_distribution[pump_key] = cell.pump_distribution.get(pump_key, 0) + 1

        numeric = cell.accumulators.setdefault("numeric", {})
        update_numeric_accumulator(
            numeric.setdefault(LIQUID_GAS_RATIO_COLUMN, {}),
            row.get(LIQUID_GAS_RATIO_COLUMN),
        )
        for ph_column in self.config.tower_ph_columns:
            update_numeric_accumulator(numeric.setdefault(ph_column, {}), row.get(ph_column))
        update_numeric_accumulator(
            numeric.setdefault(OUTLET_SO2_COLUMN, {}),
            row.get(OUTLET_SO2_COLUMN),
        )
        risk = cell.accumulators.setdefault("risk", {"valid_count": 0, "risk_count": 0})
        update_risk_accumulator(risk, row.get(OUTLET_SO2_COLUMN), self.config.emission_limit)
''', class_name="IncrementalConditionUpdater")
    _write(path, source)


def rewrite_axis_consumers() -> None:
    replacements = {
        "system/model/map_control/condition_model/auto_merge_manager.py": [
            ("cell.load_level", "cell.axis_1_level"),
            ("cell.inlet_so2_level", "cell.axis_2_level"),
            ("config.inlet_so2.cell_count", "config.axis_2.cell_count"),
        ],
        "system/model/map_control/condition_model/online_condition_classifier.py": [
            ("cell.load_level", "cell.axis_1_level"),
            ("cell.inlet_so2_level", "cell.axis_2_level"),
            ("config.inlet_so2.cell_count", "config.axis_2.cell_count"),
        ],
        "system/model/map_control/condition_model/condition_merger.py": [
            ("first.load_level", "first.axis_1_level"),
            ("second.load_level", "second.axis_1_level"),
            ("first.inlet_so2_level", "first.axis_2_level"),
            ("second.inlet_so2_level", "second.axis_2_level"),
            ("cell.load_level", "cell.axis_1_level"),
            ("cell.inlet_so2_level", "cell.axis_2_level"),
        ],
    }
    for rel, pairs in replacements.items():
        path = ROOT / rel
        source = path.read_text(encoding="utf-8")
        for old, new in pairs:
            source = source.replace(old, new)
        if rel.endswith("condition_merger.py"):
            import_marker = "import math\n"
            addition = "from system.model.config.standard_fields import LIQUID_GAS_RATIO_COLUMN\n"
            if addition not in source:
                source = source.replace(import_marker, import_marker + addition)
            source = source.replace('self._numeric_count(first, "liquid_gas")', 'self._numeric_count(first, LIQUID_GAS_RATIO_COLUMN)')
            source = source.replace('self._numeric_count(second, "liquid_gas")', 'self._numeric_count(second, LIQUID_GAS_RATIO_COLUMN)')
            source = source.replace('value = cell.statistics.get("mean_liquid_gas")', 'value = cell.statistics.get(f"mean_{LIQUID_GAS_RATIO_COLUMN}")')
            source = source.replace('value = cell.statistics.get("median_liquid_gas")', 'value = cell.statistics.get("mean_liquid_gas") or cell.statistics.get("median_liquid_gas")')
        _write(path, source)

    # New snapshots publish the new schema version.
    auto_path = ROOT / "system/model/map_control/condition_model/auto_merge_manager.py"
    auto_source = auto_path.read_text(encoding="utf-8").replace('SNAPSHOT_SCHEMA_VERSION = "5.1"', 'SNAPSHOT_SCHEMA_VERSION = "6.0"')
    _write(auto_path, auto_source)


def rewrite_policy_standard_fields() -> None:
    path = ROOT / "system/model/map_control/slurry_policy_model/_engine/schema.py"
    source = path.read_text(encoding="utf-8")
    import_marker = "from typing import Any\n"
    addition = '''\nfrom system.model.config.standard_fields import OUTLET_SO2_COLUMN, TIME_COLUMN\n'''
    if addition not in source:
        source = source.replace(import_marker, import_marker + addition)
    source = re.sub(
        r'\n_SITE_PLANT_CONFIG = _load_site_plant_config\(\)\nOUTLET_SO2_COLUMN = str\(\n\s*_SITE_PLANT_CONFIG\["process_columns"\]\["outlet_so2"\]\n\)\nLEGACY_CONDITION_AXIS_COLUMNS = \("jzfh", "yyq_SO2"\)\n',
        '\n_SITE_PLANT_CONFIG = _load_site_plant_config()\n',
        source,
        count=1,
    )
    source = _replace_node(source, "time_column", '''def time_column(plant: dict[str, Any] | None = None) -> str:
    """Return the fixed post-preprocessor timestamp field."""
    del plant
    return TIME_COLUMN
''')
    _write(path, source)

    config_loader = ROOT / "system/model/map_control/slurry_policy_model/_engine/config_loader.py"
    cl = config_loader.read_text(encoding="utf-8")
    cl = cl.replace("from .schema import time_column\n", "")
    cl = cl.replace('    if not time_column(plant):\n        raise ConfigurationError("PLANT_CONFIG.time_column 不能为空")\n\n', "")
    _write(config_loader, cl)

    online = ROOT / "system/model/map_control/slurry_policy_model/slurry_policy_online/online_slurry_policy.py"
    text = online.read_text(encoding="utf-8")
    marker = "from __future__ import annotations\n"
    standard_import = "\nfrom system.model.config.standard_fields import TIME_COLUMN\n"
    if standard_import not in text:
        text = text.replace(marker, marker + standard_import)
    text = re.sub(r'self\.plant\.get\("time_column",\s*"date"\)', 'TIME_COLUMN', text)
    _write(online, text)

    config_file = ROOT / "system/model/map_control/slurry_policy_model/slurry_policy_config.py"
    text = config_file.read_text(encoding="utf-8")
    text = text.replace("工况轴、现场字段、SO2安全范围、", "工况轴、SO2安全范围、")
    text = text.replace("# towers / valves / supply_pumps / pH / SO2 / time_column 等都不在这里再写一份。", "# towers / valves / supply_pumps / pH / SO2 等都不在这里再写一份；标准过程字段名固定。")
    _write(config_file, text)


def rewrite_bridge_and_database() -> None:
    bridge = ROOT / "system/model/config/slurry_core_bridge_config.py"
    source = bridge.read_text(encoding="utf-8")
    import_marker = "from system.model.config.plant_config import PLANT_CONFIG\n"
    addition = "from system.model.config.standard_fields import TARGET_SO2_COLUMN\n"
    if addition not in source:
        source = source.replace(import_marker, import_marker + addition)
    source = source.replace('"target_column": str(PLANT_CONFIG["process_columns"]["target_so2"]),', '"target_column": TARGET_SO2_COLUMN,')
    _write(bridge, source)

    db = ROOT / "system/model/config/database_schema.py"
    source = db.read_text(encoding="utf-8")
    import_marker = "from system.model.config.plant_config import PLANT_CONFIG\n"
    addition = "from system.model.config.standard_fields import TARGET_SO2_COLUMN\n"
    if addition not in source:
        source = source.replace(import_marker, import_marker + addition)
    source = source.replace('(str(PLANT_CONFIG["process_columns"]["target_so2"]), "float8"),', '(TARGET_SO2_COLUMN, "float8"),')
    _write(db, source)


def cleanup_obsolete_validation() -> None:
    for rel in (
        "system/model/config/test_unified_plant_config.py",
        ".github/workflows/core-condition-axes-tests.yml",
    ):
        path = ROOT / rel
        if path.exists():
            path.unlink()


def update_docs() -> None:
    replacements = {
        ROOT / "system/model/map_control/condition_model/README.md": [
            ("load / inlet_so2", "axis_1 / axis_2"),
            ("xst_ph / apt_ph", "各启用塔实际 pH 字段"),
        ],
        ROOT / "system/model/map_control/slurry_policy_model/README.md": [
            ("PLANT_CONFIG.time_column", "标准字段 date"),
        ],
    }
    for path, pairs in replacements.items():
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for old, new in pairs:
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")


def main() -> None:
    write_standard_fields()
    rewrite_plant_config()
    rewrite_condition_config()
    rewrite_condition_schema()
    rewrite_grid_definition()
    rewrite_initial_builder()
    rewrite_incremental()
    rewrite_axis_consumers()
    rewrite_policy_standard_fields()
    rewrite_bridge_and_database()
    cleanup_obsolete_validation()
    update_docs()

    # Parse every touched Python file and fail before commit on syntax errors.
    for path in (ROOT / "system/model").rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


if __name__ == "__main__":
    main()
