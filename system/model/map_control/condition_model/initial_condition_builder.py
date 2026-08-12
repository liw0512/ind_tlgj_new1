# -*- coding: utf-8 -*-
"""Initial V3 condition snapshot builder from normalized historical rows.

Coverage maturity depends only on sample-count thresholds. The model contains
no action-event, confidence, or slurry-flow logic.

Important topology rule:
- condition axes + liquid_gas_ratio + jyq_SO2 are the structural training inputs;
- enabled-tower pH columns are optional explanatory statistics;
- missing tower pH never changes grid mapping or condition_label.

This keeps the first module independent from single-/dual-tower plant topology.
The slurry-policy module owns the enabled tower/valve topology and validates
only the pH/valve fields of enabled towers.
"""

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from system.model.config.standard_fields import (
    LIQUID_GAS_RATIO_COLUMN,
    OUTLET_SO2_COLUMN,
)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from system.model.map_control.condition_model.condition_config import (
    INITIAL_CONDITION_TRAIN_CONFIG,
    MAX_SNAPSHOT_VERSIONS_TO_KEEP,
    ConditionModelConfig,
    default_config,
)
from system.model.map_control.condition_model.condition_schema import (
    ConditionSnapshot,
    PolicyRegion,
)
from system.model.map_control.condition_model.auto_merge_manager import (
    AutoMergeManager,
    write_auto_merge_report,
)
from system.model.map_control.condition_model.grid_definition import (
    build_fixed_adjacency,
    create_complete_grid,
    locate_grid,
)
from system.model.map_control.condition_model.snapshot_io import (
    cleanup_old_snapshot_versions,
    write_snapshot,
)




def normalize_and_validate_training_frame(
    frame: pd.DataFrame,
    config: ConditionModelConfig,
    context: str = "training",
) -> pd.DataFrame:
    """Validate the fixed standard interface plus configured condition axes."""
    normalized = frame.copy()
    normalized.columns = [str(column).replace("\ufeff", "").strip() for column in normalized.columns]
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


def pump_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, dict)):
        values = value.values() if isinstance(value, dict) else value
        return sum(
            1
            for item in values
            if str(item).strip().lower() in {"1", "true", "on"}
        )
    text = str(value).replace(",", "-")
    return sum(
        1
        for item in text.split("-")
        if item.strip().lower() in {"1", "true", "on"}
    )


def _safe_count(value: Any) -> int:
    try:
        if value is None:
            return 0
        number = float(value)
        if not math.isfinite(number):
            return 0
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return pump_count(value)


def build_state_key(row: Dict[str, Any]) -> str:
    xst = row.get("xst_circulation_pump_count")
    apt = row.get("apt_circulation_pump_count")
    if xst is None:
        xst = pump_count(
            row.get("xst_circulation_pump_status", row.get("xst_pump_status"))
        )
    if apt is None:
        apt = pump_count(
            row.get("apt_circulation_pump_status", row.get("apt_pump_status"))
        )
    mode = str(row.get("reaction_unit_mode", "NORMAL")).upper()
    supply = str(row.get("slurry_supply_capacity_state", "SUPPLY_NORMAL")).upper()
    return f"XP{_safe_count(xst)}-AP{_safe_count(apt)}-{mode}-{supply}"


def get_condition_axis_values(row: Dict[str, Any], config: ConditionModelConfig) -> tuple:
    first_value = _safe_float(row[config.axis_1.column])
    if first_value is None:
        raise ValueError(f"condition axis {config.axis_1.column!r} contains a missing or non-finite value")
    if config.single_axis_mode:
        return first_value, first_value
    second_value = _safe_float(row[config.axis_2.column])
    if second_value is None:
        raise ValueError(f"condition axis {config.axis_2.column!r} contains a missing or non-finite value")
    return first_value, second_value


def _empty_value_lists(config: ConditionModelConfig) -> Dict[str, List[Any]]:
    keys = [LIQUID_GAS_RATIO_COLUMN, *config.tower_ph_columns, OUTLET_SO2_COLUMN]
    result = {key: [] for key in dict.fromkeys(keys)}
    result["risk"] = []
    return result


class InitialConditionBuilder:
    def __init__(self, config: ConditionModelConfig):
        self.config = config

    def build(
        self,
        rows: Iterable[Dict[str, Any]],
        snapshot_version: str = "v001",
    ) -> ConditionSnapshot:
        catalog = create_complete_grid(self.config)
        values_by_grid = {
            grid_id: _empty_value_lists(self.config)
            for grid_id in catalog
        }

        for row in rows:
            self._add_row(catalog, values_by_grid, row)

        for grid_id, cell in catalog.items():
            self._finalize_cell(cell, values_by_grid[grid_id])

        regions = {
            cell.policy_region_id: PolicyRegion(
                region_id=cell.policy_region_id,
                member_grid_ids=[cell.grid_id],
                condition_label=_grid_id_to_base_id(cell.grid_id, self.config),
            )
            for cell in catalog.values()
        }

        return ConditionSnapshot(
            snapshot_version=snapshot_version,
            build_time=datetime.now(timezone.utc).isoformat(),
            grid_config=self.config.to_dict(),
            grid_catalog=catalog,
            grid_adjacency=build_fixed_adjacency(catalog),
            policy_regions=regions,
            metadata={"snapshot_schema_version": "6.0"},
        )

    def _add_row(
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

    @staticmethod
    def _append(target: List[float], value: Optional[Any]) -> None:
        number = _safe_float(value)
        if number is not None:
            target.append(number)

    def _finalize_cell(self, cell: Any, values: Dict[str, List[Any]]) -> None:
        _set_coverage_status(cell, self.config)
        cell.accumulators = build_cell_accumulators(values)
        finalize_cell_from_accumulators(cell, self.config)


def _grid_id_to_base_id(grid_id: str, config: ConditionModelConfig) -> str:
    first_part, second_part = grid_id.split("-")
    first_level = int(first_part[1:])
    second_level = int(second_part[1:])
    return str((first_level - 1) * config.axis_2.cell_count + second_level)


def _empty_numeric_accumulator() -> Dict[str, Any]:
    return {
        "count": 0,
        "sum": 0.0,
        "sum_square": 0.0,
        "minimum": None,
        "maximum": None,
    }


def _numeric_accumulator(values: List[float]) -> Dict[str, Any]:
    finite_values = [
        number
        for number in (_safe_float(value) for value in values)
        if number is not None
    ]
    return {
        "count": len(finite_values),
        "sum": float(sum(finite_values)) if finite_values else 0.0,
        "sum_square": (
            float(sum(value * value for value in finite_values))
            if finite_values
            else 0.0
        ),
        "minimum": min(finite_values) if finite_values else None,
        "maximum": max(finite_values) if finite_values else None,
    }


def build_cell_accumulators(values: Dict[str, List[Any]]) -> Dict[str, Any]:
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


def _safe_numeric_accumulator_from_legacy(
    value: Optional[Any],
    sample_count: int,
) -> Dict[str, Any]:
    number = _safe_float(value)
    count = int(sample_count) if number is not None and sample_count else 0
    return {
        "count": count,
        "sum": number * count if number is not None else 0.0,
        "sum_square": number * number * count if number is not None else 0.0,
        "minimum": number,
        "maximum": number,
    }


def _legacy_statistic(cell: Any, *names: str) -> Optional[float]:
    for name in names:
        value = _safe_float(cell.statistics.get(name))
        if value is not None:
            return value
    return None


def _sanitize_numeric_accumulator(value: Dict[str, Any]) -> Dict[str, Any]:
    accumulator = dict(value or {})
    count = max(0, int(_safe_float(accumulator.get("count")) or 0))
    total = _safe_float(accumulator.get("sum"))
    sum_square = _safe_float(accumulator.get("sum_square"))
    minimum = _safe_float(accumulator.get("minimum"))
    maximum = _safe_float(accumulator.get("maximum"))

    if count == 0 or total is None or sum_square is None:
        return _empty_numeric_accumulator()
    if minimum is not None and maximum is not None and minimum > maximum:
        minimum, maximum = maximum, minimum
    return {
        "count": count,
        "sum": total,
        "sum_square": sum_square,
        "minimum": minimum,
        "maximum": maximum,
    }


def ensure_cell_accumulators(cell: Any, config: ConditionModelConfig) -> None:
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


def update_numeric_accumulator(
    accumulator: Dict[str, Any],
    value: Optional[Any],
) -> None:
    number = _safe_float(value)
    if number is None:
        return

    sanitized = _sanitize_numeric_accumulator(accumulator)
    accumulator.clear()
    accumulator.update(sanitized)
    accumulator["count"] += 1
    accumulator["sum"] += number
    accumulator["sum_square"] += number * number
    accumulator["minimum"] = (
        number
        if accumulator["minimum"] is None
        else min(accumulator["minimum"], number)
    )
    accumulator["maximum"] = (
        number
        if accumulator["maximum"] is None
        else max(accumulator["maximum"], number)
    )


def update_risk_accumulator(
    accumulator: Dict[str, Any],
    outlet_so2: Optional[Any],
    emission_limit: float,
) -> None:
    value = _safe_float(outlet_so2)
    if value is None:
        return
    accumulator["valid_count"] = int(accumulator.get("valid_count", 0)) + 1
    accumulator["risk_count"] = int(accumulator.get("risk_count", 0)) + int(
        value > emission_limit
    )


def _set_coverage_status(cell: Any, config: ConditionModelConfig) -> None:
    thresholds = config.merge
    if cell.sample_count == 0:
        cell.coverage_status = "EMPTY"
    elif cell.sample_count < thresholds.min_observed_samples:
        cell.coverage_status = "OBSERVED"
    elif cell.sample_count < thresholds.min_mature_samples:
        cell.coverage_status = "LEARNING"
    else:
        cell.coverage_status = "MATURE"


def finalize_cell_from_accumulators(cell: Any, config: ConditionModelConfig) -> None:
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


CONDITION_OUTPUT_COLUMNS = (
    "condition_snapshot_version",
    "grid_id",
    "base_condition_id",
    "condition_label",
    "policy_region_id",
    "region_status",
    "region_member_count",
    "coverage_status",
    "state_key",
    "condition_experience_source",
    "condition_valid",
    "out_of_range_clipped",
    "clip_axis",
    "condition_reason",
)


def resolve_condition_experience_source(
    cell: Any,
    region: Optional[PolicyRegion],
    state_key: str,
    config: ConditionModelConfig,
) -> str:
    profile = (cell.state_profiles or {}).get(state_key)
    if (
        profile
        and int(profile.get("sample_count", 0))
        >= config.merge.min_observed_samples
    ):
        return "LOCAL_GRID"

    if region and len(region.member_grid_ids) > 1:
        if region.status == "AUTO_CONFIRMED_MERGE":
            return "MERGED_REGION"
        if (
            region.status == "AUTO_PROVISIONAL_MERGE"
            and config.online.allow_provisional_region_fallback
        ):
            return "MERGED_REGION"

    if int(cell.sample_count or 0) > 0:
        return "PLANT_GLOBAL"
    return "BASELINE_ONLY"


def condition_label_for_row(
    row: Dict[str, Any],
    config: ConditionModelConfig,
    statistics: Optional[Dict[str, Any]] = None,
    snapshot: Optional[ConditionSnapshot] = None,
) -> Dict[str, Any]:
    state_key = build_state_key(row)
    snapshot_version = snapshot.snapshot_version if snapshot else ""

    try:
        first_value, second_value = get_condition_axis_values(row, config)
        grid_id, clipped, clip_axis = locate_grid(
            first_value,
            second_value,
            config,
        )
        base_condition_id = _grid_id_to_base_id(grid_id, config)

        condition_label = base_condition_id
        policy_region_id = ""
        region_status = "INDEPENDENT"
        region_member_count = 1
        coverage_status = "UNKNOWN"
        condition_experience_source = "BASELINE_ONLY"

        if snapshot:
            cell = snapshot.grid_catalog.get(grid_id)
            if cell is None:
                raise KeyError(f"grid_id not found in snapshot: {grid_id}")

            region = snapshot.policy_regions.get(cell.policy_region_id)
            condition_label = condition_label_from_snapshot(
                grid_id,
                snapshot,
                config,
            )
            policy_region_id = cell.policy_region_id
            coverage_status = cell.coverage_status

            if region is not None:
                region_status = region.status or "INDEPENDENT"
                region_member_count = len(region.member_grid_ids)

            condition_experience_source = resolve_condition_experience_source(
                cell,
                region,
                state_key,
                config,
            )
        elif statistics:
            condition_label = str(
                statistics.get("base_to_label", {}).get(
                    base_condition_id,
                    base_condition_id,
                )
            )
            policy_region_id = _region_id_for_label(condition_label)

        return {
            "condition_snapshot_version": snapshot_version,
            "grid_id": grid_id,
            "base_grid_id": grid_id,
            "base_condition_id": base_condition_id,
            "condition_label": condition_label,
            "policy_region_id": policy_region_id,
            "region_status": region_status,
            "region_member_count": region_member_count,
            "coverage_status": coverage_status,
            "state_key": state_key,
            "condition_experience_source": condition_experience_source,
            "condition_valid": True,
            "out_of_range_clipped": clipped,
            "clip_axis": clip_axis,
            "condition_reason": "OK",
        }
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return {
            "condition_snapshot_version": snapshot_version,
            "grid_id": "",
            "base_grid_id": "",
            "base_condition_id": "",
            "condition_label": "",
            "policy_region_id": "",
            "region_status": "INVALID",
            "region_member_count": 0,
            "coverage_status": "INVALID",
            "state_key": state_key,
            "condition_experience_source": "BASELINE_ONLY",
            "condition_valid": False,
            "out_of_range_clipped": False,
            "clip_axis": "none",
            "condition_reason": str(exc),
        }


def append_condition_columns(
    frame: pd.DataFrame,
    snapshot: ConditionSnapshot,
    config: ConditionModelConfig,
    statistics: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    rows = []
    for row in frame.to_dict(orient="records"):
        context = condition_label_for_row(
            row,
            config,
            statistics,
            snapshot,
        )
        enriched = dict(row)
        for column in CONDITION_OUTPUT_COLUMNS:
            enriched[column] = context[column]
        rows.append(enriched)
    return pd.DataFrame(rows)


def condition_label_from_snapshot(
    grid_id: str,
    snapshot: ConditionSnapshot,
    config: ConditionModelConfig,
) -> str:
    cell = snapshot.grid_catalog.get(grid_id)
    if not cell:
        return _grid_id_to_base_id(grid_id, config)
    region = snapshot.policy_regions.get(cell.policy_region_id)
    if region and region.condition_label:
        return str(region.condition_label)
    return _grid_id_to_base_id(grid_id, config)


def _region_id_for_label(label: str) -> str:
    return f"R_{int(label):03d}" if str(label).isdigit() else f"R_{label}"


def publish_labels_to_snapshot(
    snapshot: ConditionSnapshot,
    statistics: Dict[str, Any],
    config: ConditionModelConfig,
) -> ConditionSnapshot:
    base_to_label = statistics.setdefault("base_to_label", {})
    grouped_grid_ids: Dict[str, List[str]] = {}
    for grid_id, cell in snapshot.grid_catalog.items():
        base_id = _grid_id_to_base_id(grid_id, config)
        label = str(base_to_label.setdefault(base_id, base_id))
        grouped_grid_ids.setdefault(label, []).append(grid_id)

    regions: Dict[str, PolicyRegion] = {}
    sort_key = lambda item: (
        (0, int(item[0])) if item[0].isdigit() else (1, item[0])
    )
    for label, grid_ids in sorted(grouped_grid_ids.items(), key=sort_key):
        region_id = _region_id_for_label(label)
        member_grid_ids = sorted(
            grid_ids,
            key=lambda item: int(_grid_id_to_base_id(item, config)),
        )
        status = "INDEPENDENT" if len(member_grid_ids) == 1 else "LEGACY_UNVERIFIED_MERGE"
        regions[region_id] = PolicyRegion(
            region_id=region_id,
            member_grid_ids=member_grid_ids,
            status=status,
            evidence={"source": "condition_merge_statistics.base_to_label"},
            condition_label=label,
        )
        for grid_id in member_grid_ids:
            snapshot.grid_catalog[grid_id].policy_region_id = region_id
    snapshot.policy_regions = regions
    return snapshot


def sync_statistics_compatibility_from_snapshot(
    statistics: Dict[str, Any],
    snapshot: ConditionSnapshot,
    config: ConditionModelConfig,
) -> Dict[str, Any]:
    base_to_label = {}
    for grid_id in snapshot.grid_catalog:
        base_id = _grid_id_to_base_id(grid_id, config)
        base_to_label[base_id] = condition_label_from_snapshot(
            grid_id,
            snapshot,
            config,
        )
    statistics["base_to_label"] = base_to_label
    return rebuild_condition_regions(statistics)


def snapshot_has_published_labels(snapshot: ConditionSnapshot) -> bool:
    return any(
        region.condition_label
        for region in snapshot.policy_regions.values()
    )


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        return number
    except (TypeError, ValueError, OverflowError):
        return None


def _empty_merge_statistics() -> Dict[str, Any]:
    return {
        "description": (
            "Cumulative condition-label statistics for merge and inheritance "
            "judgement. Slurry-flow, action-event, and confidence fields are "
            "not part of this model."
        ),
        "base_conditions": {},
        "condition_regions": {},
        "base_to_label": {},
    }


def load_merge_statistics(path: Optional[str]) -> Dict[str, Any]:
    if not path or not Path(path).exists():
        return _empty_merge_statistics()
    with open(path, "r", encoding="utf-8") as stream:
        statistics = json.load(stream)
    statistics.setdefault("base_conditions", {})
    statistics.setdefault("condition_regions", {})
    statistics.setdefault("base_to_label", {})
    statistics.pop("action_event_registry", None)
    for group in ("base_conditions", "condition_regions"):
        for item in statistics[group].values():
            if isinstance(item, dict):
                item.pop("action_event_count", None)
                item.pop("confidence", None)
                item.pop("slurry_flow", None)
                item.pop("slurry_flow_mean", None)
    return statistics


def _new_condition_item(grid_id: str) -> Dict[str, Any]:
    return {
        "base_grid_id": grid_id,
        "sample_count": 0,
        "liquid_gas_sum": 0.0,
        "liquid_gas_count": 0,
        "liquid_gas_mean": None,
        "pump_distribution": {},
        "state_profile_distribution": {},
    }


def _merge_counter(target: Dict[str, int], source: Dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + int(value)


def rebuild_condition_regions(statistics: Dict[str, Any]) -> Dict[str, Any]:
    regions: Dict[str, Dict[str, Any]] = {}
    for base_id, item in statistics.get("base_conditions", {}).items():
        label = str(
            statistics.setdefault("base_to_label", {}).get(base_id, base_id)
        )
        region = regions.setdefault(
            label,
            {
                "member_base_conditions": [],
                "sample_count": 0,
                "liquid_gas_sum": 0.0,
                "liquid_gas_count": 0,
                "liquid_gas_mean": None,
                "pump_distribution": {},
                "state_profile_distribution": {},
            },
        )
        region["member_base_conditions"].append(base_id)
        region["sample_count"] += int(item.get("sample_count", 0))
        liquid_gas_sum = _safe_float(item.get("liquid_gas_sum")) or 0.0
        region["liquid_gas_sum"] += liquid_gas_sum
        region["liquid_gas_count"] += int(item.get("liquid_gas_count", 0))
        _merge_counter(
            region["pump_distribution"],
            item.get("pump_distribution", {}),
        )
        _merge_counter(
            region["state_profile_distribution"],
            item.get("state_profile_distribution", {}),
        )

    for region in regions.values():
        region["member_base_conditions"] = sorted(
            region["member_base_conditions"],
            key=lambda value: int(value),
        )
        if region["liquid_gas_count"]:
            region["liquid_gas_mean"] = (
                region["liquid_gas_sum"] / region["liquid_gas_count"]
            )
    statistics["condition_regions"] = dict(
        sorted(regions.items(), key=lambda item: int(item[0]))
    )
    return statistics


def merge_condition_regions(
    statistics: Dict[str, Any],
    first_label: Any,
    second_label: Any,
) -> Dict[str, Any]:
    first = str(first_label)
    second = str(second_label)
    kept = str(min(int(first), int(second)))
    removed = str(max(int(first), int(second)))
    for base_id, label in list(
        statistics.setdefault("base_to_label", {}).items()
    ):
        if str(label) == removed:
            statistics["base_to_label"][base_id] = kept
    return rebuild_condition_regions(statistics)


def apply_merge_pairs(
    statistics: Dict[str, Any],
    merge_pairs: Iterable[Iterable[Any]],
) -> Dict[str, Any]:
    if list(merge_pairs or []):
        raise RuntimeError(
            "Direct merge_condition_label_pairs publication is disabled. "
            "Use AutoMergeManager evidence evaluation instead."
        )
    return statistics


def update_merge_statistics(
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


def write_merge_statistics(
    statistics: Dict[str, Any],
    path: Optional[str],
) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as stream:
        json.dump(
            statistics,
            stream,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )


def build_initial_condition_csv(
    input_csv_path: str,
    output_csv_path: str,
    snapshot_output_path: Optional[str] = None,
    merge_statistics_json_path: Optional[str] = None,
    auto_merge_report_path: Optional[str] = None,
    snapshot_version: str = "v001",
    encoding: str = "utf-8-sig",
    config: Optional[ConditionModelConfig] = None,
) -> str:
    config = config or default_config()
    source = Path(input_csv_path)
    target = Path(output_csv_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    frame = pd.read_csv(source, encoding=encoding)
    frame = normalize_and_validate_training_frame(
        frame,
        config,
        context="initial training",
    )
    rows = frame.to_dict(orient="records")

    snapshot = InitialConditionBuilder(config).build(rows, snapshot_version)
    snapshot, auto_merge_report = AutoMergeManager(config).apply(
        snapshot,
        previous_snapshot=None,
    )

    statistics = update_merge_statistics(
        _empty_merge_statistics(),
        rows,
        config,
    )
    statistics = sync_statistics_compatibility_from_snapshot(
        statistics,
        snapshot,
        config,
    )

    if snapshot_output_path:
        write_snapshot(snapshot, snapshot_output_path)
        cleanup_old_snapshot_versions(
            snapshot_output_path,
            MAX_SNAPSHOT_VERSIONS_TO_KEEP,
        )
    write_merge_statistics(statistics, merge_statistics_json_path)
    write_auto_merge_report(auto_merge_report, auto_merge_report_path)

    result = append_condition_columns(frame, snapshot, config)
    result.to_csv(target, index=False, encoding="utf-8-sig")
    summary = auto_merge_report.get("summary", {})
    print(
        f"初次工况训练标注完成: input={source}, output={target}, "
        f"rows={len(result)}, "
        f"provisional_regions={summary.get('provisional_region_count', 0)}, "
        f"confirmed_regions={summary.get('confirmed_region_count', 0)}"
    )
    return str(target)


def run_configured_initial_train() -> str:
    return build_initial_condition_csv(
        input_csv_path=INITIAL_CONDITION_TRAIN_CONFIG["input_csv_path"],
        output_csv_path=INITIAL_CONDITION_TRAIN_CONFIG["output_csv_path"],
        snapshot_output_path=INITIAL_CONDITION_TRAIN_CONFIG.get(
            "snapshot_output_path"
        ),
        merge_statistics_json_path=INITIAL_CONDITION_TRAIN_CONFIG.get(
            "merge_statistics_json_path"
        ),
        auto_merge_report_path=INITIAL_CONDITION_TRAIN_CONFIG.get(
            "auto_merge_report_path"
        ),
        snapshot_version=INITIAL_CONDITION_TRAIN_CONFIG.get(
            "snapshot_version",
            "v001",
        ),
        encoding=INITIAL_CONDITION_TRAIN_CONFIG.get("encoding", "utf-8-sig"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build initial condition labels for a CSV dataset"
    )
    parser.add_argument("--input", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--snapshot-output", default=None)
    parser.add_argument("--merge-statistics-output", default=None)
    parser.add_argument("--auto-merge-report", default=None)
    parser.add_argument("--snapshot-version", default=None)
    parser.add_argument("--encoding", default=None)
    args = parser.parse_args()

    if args.input or args.output:
        output = build_initial_condition_csv(
            input_csv_path=(
                args.input
                or INITIAL_CONDITION_TRAIN_CONFIG["input_csv_path"]
            ),
            output_csv_path=(
                args.output
                or INITIAL_CONDITION_TRAIN_CONFIG["output_csv_path"]
            ),
            snapshot_output_path=(
                args.snapshot_output
                or INITIAL_CONDITION_TRAIN_CONFIG.get("snapshot_output_path")
            ),
            merge_statistics_json_path=(
                args.merge_statistics_output
                or INITIAL_CONDITION_TRAIN_CONFIG.get(
                    "merge_statistics_json_path"
                )
            ),
            auto_merge_report_path=(
                args.auto_merge_report
                or INITIAL_CONDITION_TRAIN_CONFIG.get(
                    "auto_merge_report_path"
                )
            ),
            snapshot_version=(
                args.snapshot_version
                or INITIAL_CONDITION_TRAIN_CONFIG.get(
                    "snapshot_version",
                    "v001",
                )
            ),
            encoding=(
                args.encoding
                or INITIAL_CONDITION_TRAIN_CONFIG.get(
                    "encoding",
                    "utf-8-sig",
                )
            ),
        )
    else:
        output = run_configured_initial_train()
    print(output)


if __name__ == "__main__":
    main()
