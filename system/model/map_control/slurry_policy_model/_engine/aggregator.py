from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import nullcontext
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

from .config_loader import all_valves, enabled_towers
from .reliability import calculate_reliability, profile_status
from .spatial_policy import (
    connected_grid_region_count,
    distance_mapping_weight,
    minimum_offsets_to_grids,
    parse_grid_id,
)
from .utils import normalize_condition_label


def _measure(performance_recorder: Any | None, name: str):
    if performance_recorder is None:
        return nullcontext()
    measure = getattr(performance_recorder, "measure", None)
    return measure(name) if callable(measure) else nullcontext()


def _add_counter(
    performance_recorder: Any | None, name: str, value: Any
) -> None:
    if performance_recorder is None:
        return
    add_counter = getattr(performance_recorder, "add_counter", None)
    if callable(add_counter):
        add_counter(name, value)


def _weight_series(group: pd.DataFrame) -> pd.Series:
    column = "aggregation_weight" if "aggregation_weight" in group.columns else "evidence_weight"
    if column not in group.columns:
        return pd.Series(1.0, index=group.index, dtype=float)
    values = pd.to_numeric(group[column], errors="coerce").fillna(0.0).clip(lower=0.0)
    if float(values.sum()) <= 0:
        return pd.Series(1.0, index=group.index, dtype=float)
    return values.astype(float)


def _weighted_bool_ratio(series: pd.Series, weights: pd.Series) -> float:
    values = (
        pd.Series(series, index=weights.index)
        .fillna(False)
        .astype(bool)
        .to_numpy(dtype=float, copy=False)
    )
    weight_values = weights.to_numpy(dtype=float, copy=False)
    total = float(weight_values.sum())
    return float(np.dot(values, weight_values) / total) if total > 0 else 0.0


def _weighted_category_totals(
    series: pd.Series, weights: pd.Series
) -> tuple[list[str], np.ndarray]:
    """Aggregate category weights without repeated pandas ``.loc`` calls."""
    clean = series.astype("object").fillna("UNKNOWN").astype(str)
    labels, inverse = np.unique(
        clean.to_numpy(dtype=object, copy=False), return_inverse=True
    )
    totals = np.bincount(
        inverse,
        weights=weights.to_numpy(dtype=float, copy=False),
        minlength=len(labels),
    )
    return [str(value) for value in labels.tolist()], totals.astype(float)


def _weighted_category_ratios(series: pd.Series, weights: pd.Series) -> dict[str, float]:
    total = float(weights.to_numpy(dtype=float, copy=False).sum())
    if total <= 0:
        return {}
    labels, totals = _weighted_category_totals(series, weights)
    return {
        label: float(value / total)
        for label, value in zip(labels, totals)
    }


def _weighted_quantiles(
    values: np.ndarray, weights: np.ndarray, quantiles: Iterable[float]
) -> list[float]:
    """Return weighted quantiles after one stable ordering pass.

    V1.8A called ``np.argsort`` separately for p25, p50 and p75.  The
    selected positions are identical when the same sorted arrays and
    ``searchsorted(..., side="left")`` rule are reused.
    """
    order = np.argsort(values)
    sorted_values = values[order]
    sorted_weights = weights[order]
    cumulative = np.cumsum(sorted_weights)
    total = cumulative[-1]
    output: list[float] = []
    for quantile in quantiles:
        cutoff = float(quantile) * total
        index = int(np.searchsorted(cumulative, cutoff, side="left"))
        output.append(float(sorted_values[min(index, len(sorted_values) - 1)]))
    return output


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, quantile: float) -> float:
    """Compatibility wrapper retained for tests and external callers."""
    return _weighted_quantiles(values, weights, [quantile])[0]


def _distribution(series: pd.Series, weights: pd.Series | None = None) -> dict[str, Any]:
    clean = pd.to_numeric(series, errors="coerce")
    mask = clean.notna()
    clean = clean[mask]
    if clean.empty:
        return {
            "median": None,
            "p25": None,
            "p75": None,
            "iqr": None,
            "minimum": None,
            "maximum": None,
        }
    if weights is None:
        weights = pd.Series(1.0, index=clean.index)
    else:
        weights = pd.to_numeric(weights.loc[clean.index], errors="coerce").fillna(0.0)
        if float(weights.sum()) <= 0:
            weights = pd.Series(1.0, index=clean.index)
    values_np = clean.to_numpy(dtype=float)
    weights_np = weights.to_numpy(dtype=float)
    p25, median, p75 = _weighted_quantiles(
        values_np, weights_np, (0.25, 0.50, 0.75)
    )
    return {
        "median": median,
        "p25": p25,
        "p75": p75,
        "iqr": p75 - p25,
        "minimum": float(np.min(values_np)),
        "maximum": float(np.max(values_np)),
    }


def _weighted_mode(series: pd.Series, weights: pd.Series, default: str = "UNKNOWN") -> str:
    ratios = _weighted_category_ratios(series, weights)
    return max(ratios, key=ratios.get) if ratios else default


def _weighted_counts(series: pd.Series, weights: pd.Series) -> dict[str, float]:
    labels, totals = _weighted_category_totals(series, weights)
    return {
        label: float(value)
        for label, value in zip(labels, totals)
    }


def _spatial_support(
    group: pd.DataFrame,
    weights: pd.Series,
    dominant_direction: str,
    training: dict[str, Any],
) -> dict[str, Any]:
    if "__normalized_condition_label" in group.columns:
        conditions = group["__normalized_condition_label"]
    else:
        conditions = group.get(
            "condition_label", pd.Series("UNKNOWN", index=group.index)
        ).map(normalize_condition_label)
    if "__grid_id_text" in group.columns:
        grids = group["__grid_id_text"]
    else:
        grids = group.get(
            "anchor_grid_id",
            group.get(
                "start_grid_id", pd.Series("UNKNOWN", index=group.index)
            ),
        ).astype(str)

    condition_weight = _weighted_counts(conditions, weights)
    grid_weight = _weighted_counts(grids, weights)
    total_weight = float(weights.sum())
    max_condition_share = max(condition_weight.values(), default=0.0) / total_weight if total_weight else 0.0
    max_grid_share = max(grid_weight.values(), default=0.0) / total_weight if total_weight else 0.0

    points = [parsed for parsed in (parse_grid_id(value) for value in grid_weight) if parsed]
    load_span = max((p for p, _ in points), default=None)
    load_min = min((p for p, _ in points), default=None)
    inlet_span = max((s for _, s in points), default=None)
    inlet_min = min((s for _, s in points), default=None)

    prior_cfg = training.get("plant_action_prior", {})
    minimum_per_grid = int(prior_cfg.get("minimum_events_per_source_grid", 2))
    supported_grid_directions: dict[str, str] = {}
    supported_grid_weights: dict[str, float] = {}
    for grid_id, grid_group in group.groupby(grids, dropna=False, observed=True):
        if len(grid_group) < minimum_per_grid:
            continue
        grid_weights = weights.loc[grid_group.index]
        supported_grid_directions[str(grid_id)] = _weighted_mode(
            grid_group["so2_effect_direction"], grid_weights
        )
        supported_grid_weights[str(grid_id)] = float(grid_weights.sum())
    direction_total = sum(supported_grid_weights.values())
    direction_match = sum(
        supported_grid_weights[grid]
        for grid, direction in supported_grid_directions.items()
        if direction == dominant_direction
    )
    cross_grid_consistency = direction_match / direction_total if direction_total else 0.0

    source_condition_count = len(condition_weight)
    source_grid_count = len(grid_weight)
    connected_regions = connected_grid_region_count(grid_weight.keys())
    direction_generalizable = bool(
        source_condition_count >= int(prior_cfg.get("minimum_source_conditions", 3))
        and source_grid_count >= int(prior_cfg.get("minimum_source_grids", 3))
        and max_condition_share <= float(prior_cfg.get("maximum_single_condition_share", 0.60))
        and max_grid_share <= float(prior_cfg.get("maximum_single_grid_share", 0.50))
        and cross_grid_consistency >= float(
            prior_cfg.get("minimum_cross_grid_direction_consistency", 0.70)
        )
    )
    if direction_generalizable:
        generalization_status = "CROSS_REGION_DIRECTION_SUPPORTED"
    elif source_grid_count <= 1 or source_condition_count <= 1:
        generalization_status = "LOCALIZED_EVIDENCE"
    else:
        generalization_status = "CROSS_REGION_LOW_GENERALIZATION"

    return {
        "source_condition_label_count": source_condition_count,
        "source_grid_count": source_grid_count,
        "source_condition_weight": condition_weight,
        "source_grid_weight": grid_weight,
        "load_grid_min": load_min,
        "load_grid_max": load_span,
        "load_grid_span": (load_span - load_min) if load_span is not None and load_min is not None else None,
        "inlet_so2_grid_min": inlet_min,
        "inlet_so2_grid_max": inlet_span,
        "inlet_so2_grid_span": (inlet_span - inlet_min) if inlet_span is not None and inlet_min is not None else None,
        "max_single_condition_share": float(max_condition_share),
        "max_single_grid_share": float(max_grid_share),
        "connected_region_count": connected_regions,
        "supported_grid_direction_count": len(supported_grid_directions),
        "cross_grid_direction_consistency": float(cross_grid_consistency),
        "direction_generalizable": direction_generalizable,
        "generalization_status": generalization_status,
    }


def aggregate_action_profile(
    group: pd.DataFrame, plant: dict[str, Any], training: dict[str, Any]
) -> dict[str, Any]:
    weights = _weight_series(group)
    event_count = int(len(group))
    effective_event_count = float(weights.sum())
    direction_weight = _weighted_counts(group["so2_effect_direction"], weights)
    dominant = max(
        ["DECREASE", "NEUTRAL", "INCREASE"],
        key=lambda key: direction_weight.get(key, 0.0),
    )
    consistency = (
        float(direction_weight.get(dominant, 0.0) / effective_event_count)
        if effective_event_count > 0
        else 0.0
    )
    ratios = {
        key.lower() + "_ratio": float(direction_weight.get(key, 0.0) / effective_event_count)
        if effective_event_count > 0
        else 0.0
        for key in ["DECREASE", "NEUTRAL", "INCREASE", "UNKNOWN"]
    }

    if "__any_safety_violation" in group.columns:
        any_safety = group["__any_safety_violation"]
    else:
        safety_columns = ["outlet_so2_out_of_range"] + [
            f"ph_out_of_range__{tower['tower_id']}"
            for tower in enabled_towers(plant)
        ]
        any_safety = pd.Series(False, index=group.index)
        for column in safety_columns:
            if column in group:
                any_safety = any_safety | group[column].fillna(False).astype(bool)
    safety_ratio = _weighted_bool_ratio(any_safety, weights)
    stable_ratio = _weighted_bool_ratio(group["stable_response"], weights)

    segment_count = int(group["continuous_segment_id"].nunique())
    day_count = int(group["event_date"].nunique())
    reliability = calculate_reliability(
        effective_event_count,
        segment_count,
        day_count,
        consistency,
        stable_ratio,
        safety_ratio,
        training["reliability"],
    )

    representative_delta: dict[str, Any] = {}
    delta_distribution: dict[str, Any] = {}
    for valve in all_valves(plant):
        column = f"delta_valve__{valve['valve_id']}"
        dist = _distribution(group[column], weights)
        representative_delta[valve["valve_id"]] = dist["median"]
        delta_distribution[valve["valve_id"]] = dist

    ph_effect: dict[str, Any] = {}
    ph_safety: dict[str, Any] = {}
    dead = float(training["response"]["ph_direction_deadband"])
    for tower in enabled_towers(plant):
        tower_id = tower["tower_id"]
        column = f"delta_ph__{tower_id}"
        helper_column = f"__ph_direction__{tower_id}"
        if helper_column in group.columns:
            directions = group[helper_column]
        else:
            values = pd.to_numeric(group[column], errors="coerce")
            directions = pd.Series(
                np.where(
                    values > dead,
                    "INCREASE",
                    np.where(values < -dead, "DECREASE", "NEUTRAL"),
                ),
                index=group.index,
            )
        direction_ratio = _weighted_category_ratios(directions, weights)
        ph_effect[tower_id] = {
            "delta_distribution": _distribution(group[column], weights),
            "increase_ratio": direction_ratio.get("INCREASE", 0.0),
            "neutral_ratio": direction_ratio.get("NEUTRAL", 0.0),
            "decrease_ratio": direction_ratio.get("DECREASE", 0.0),
            "dominant_direction": _weighted_mode(directions, weights),
            "median_post_range": _distribution(group[f"post_ph_range__{tower_id}"], weights)["median"],
        }
        ph_safety[tower_id] = {
            "below_limit_count": int(group[f"ph_below_limit__{tower_id}"].fillna(False).sum()),
            "above_limit_count": int(group[f"ph_above_limit__{tower_id}"].fillna(False).sum()),
            "out_of_range_ratio": _weighted_bool_ratio(group[f"ph_out_of_range__{tower_id}"], weights),
        }

    attribution_counts = group.get(
        "attribution_source", pd.Series("UNKNOWN", index=group.index)
    ).astype("object").fillna("UNKNOWN").astype(str).value_counts().to_dict()
    route_counts = group.get(
        "training_route", pd.Series("UNKNOWN", index=group.index)
    ).astype("object").fillna("UNKNOWN").astype(str).value_counts().to_dict()
    coverage = pd.to_numeric(
        group.get("neighborhood_coverage_ratio", pd.Series(np.nan, index=group.index)),
        errors="coerce",
    )

    return {
        "action_profile": {
            "action_family": str(group["action_family"].iloc[0]),
            "direction": str(group["action_direction"].iloc[0]),
            "magnitude": str(group["action_magnitude"].iloc[0]),
            "representative_delta": representative_delta,
            "delta_distribution": delta_distribution,
        },
        "so2_effect": {
            "dominant_direction": dominant,
            "direction_consistency": consistency,
            "effect_strength_mode": _weighted_mode(group["so2_effect_strength"], weights),
            **ratios,
            "delta_distribution": _distribution(group["delta_outlet_so2"], weights),
        },
        "ph_effect": ph_effect,
        "stability": {
            "stable_response_ratio": stable_ratio,
            "oscillation_ratio": _weighted_bool_ratio(group["oscillation_detected"], weights),
            "short_reverse_action_ratio": _weighted_bool_ratio(group["short_reverse_action"], weights),
            "post_so2_range": _distribution(group["post_outlet_so2_range"], weights),
            "post_so2_std": _distribution(group["post_outlet_so2_std"], weights),
        },
        "safety": {
            "outlet_so2_out_of_range_count": int(group["outlet_so2_out_of_range"].fillna(False).sum()),
            "outlet_so2_over_hard_max_count": int(group["outlet_so2_over_hard_max"].fillna(False).sum()),
            "any_safety_violation_ratio": safety_ratio,
            "tower_ph": ph_safety,
        },
        "support": {
            "event_count": event_count,
            "effective_weighted_event_count": effective_event_count,
            "independent_segment_count": segment_count,
            "independent_day_count": day_count,
            "first_event_time": group["action_start_time"].min(),
            "last_event_time": group["action_start_time"].max(),
            "episode_type_counts": group["episode_type"].astype("object").value_counts().to_dict(),
            "disturbance_counts": group["disturbance_mode"].astype("object").value_counts().to_dict(),
            "attribution_source_counts": attribution_counts,
            "training_route_counts": route_counts,
            "mean_neighborhood_coverage_ratio": float(coverage.mean()) if coverage.notna().any() else None,
        },
        "spatial_support": _spatial_support(group, weights, dominant, training),
        "reliability": reliability,
        "profile_status": profile_status(
            effective_event_count, segment_count, day_count, training["reliability"]
        ),
    }


def aggregate_plant_action_prior(
    group: pd.DataFrame, plant: dict[str, Any], training: dict[str, Any]
) -> dict[str, Any]:
    """全厂层只保留动作方向、稳定性、安全性和空间推广性，不提供阀位命令。"""
    profile = aggregate_action_profile(group, plant, training)
    raw_action = profile.pop("action_profile")
    profile["action_prior"] = {
        "action_family": raw_action["action_family"],
        "direction": raw_action["direction"],
        "magnitude": raw_action["magnitude"],
        "usage_constraint": "DIRECTION_AND_SAFETY_PRIOR_ONLY",
        "representative_delta_available": False,
    }
    profile["generalization_status"] = profile["spatial_support"]["generalization_status"]
    return profile


def build_nested_profiles(
    episodes: pd.DataFrame,
    owner_column: str | None,
    state_column: str,
    plant: dict[str, Any],
    training: dict[str, Any],
    progress: Callable[[float, str], None] | None = None,
    profile_builder: Callable[[pd.DataFrame, dict[str, Any], dict[str, Any]], dict[str, Any]] = aggregate_action_profile,
) -> dict[str, dict[str, Any]]:
    if episodes.empty:
        if progress:
            progress(1.0, "没有可聚合的有效片段")
        return {}
    owner_values: Iterable[Any] = ["PLANT"] if owner_column is None else episodes[owner_column].dropna().unique()
    owner_list = list(owner_values)
    output: dict[str, dict[str, Any]] = {}
    for owner_index, owner in enumerate(owner_list, start=1):
        owner_text = str(owner)
        if progress:
            progress((owner_index - 1) / max(len(owner_list), 1), f"聚合对象 {owner_index}/{len(owner_list)}：{owner_text}")
        subset = episodes if owner_column is None else episodes[episodes[owner_column] == owner]
        states: dict[str, Any] = {}
        for state, state_group in subset.groupby(state_column, dropna=False, observed=True):
            actions: dict[str, Any] = {}
            for action_id, action_group in state_group.groupby("action_id", dropna=False, observed=True):
                actions[str(action_id)] = profile_builder(action_group, plant, training)
            states[str(state)] = actions
        output[owner_text] = states
    if progress:
        progress(1.0, f"聚合完成，共 {len(output)} 个对象")
    return output


def _normalize_episode_labels(episodes: pd.DataFrame) -> pd.DataFrame:
    result = episodes.copy()
    if "condition_label" not in result.columns:
        result["condition_label"] = "UNKNOWN"
    result["condition_label"] = result["condition_label"].map(normalize_condition_label)
    if "anchor_condition_label" not in result.columns:
        result["anchor_condition_label"] = result["condition_label"]
    else:
        result["anchor_condition_label"] = result["anchor_condition_label"].map(normalize_condition_label)
    if "anchor_grid_id" not in result.columns:
        result["anchor_grid_id"] = result.get("start_grid_id", "UNKNOWN")
    if "training_route" not in result.columns:
        transient = (
            result["is_transient"].fillna(False).astype(bool)
            if "is_transient" in result.columns
            else pd.Series(False, index=result.index)
        )
        result["training_route"] = np.where(transient, "TRANSIENT", "LOCAL_REGULAR")
    if "evidence_weight" not in result.columns:
        result["evidence_weight"] = 1.0
    return result


def _categorize_groupby_keys(
    episodes: pd.DataFrame, training: dict[str, Any]
) -> pd.DataFrame:
    if not bool(
        training.get("performance", {}).get("categorical_groupby_keys", True)
    ):
        return episodes
    result = episodes.copy()
    keys = (
        "condition_label",
        "anchor_condition_label",
        "anchor_grid_id",
        "action_id",
        "policy_state_key",
        "policy_state_key_no_grid",
        "training_route",
        "disturbance_mode",
        "so2_effect_direction",
        "event_date",
        "episode_type",
        "attribution_source",
    )
    for column in keys:
        if column in result.columns and not isinstance(
            result[column].dtype, pd.CategoricalDtype
        ):
            result[column] = result[column].astype("category")
    return result


def build_neighbor_mapping_table(
    source_grid_ids: Iterable[Any],
    target_grids: dict[str, list[str]],
    training: dict[str, Any],
) -> pd.DataFrame:
    """Build the exact V1.8A source-grid → target-condition mapping faster.

    The old implementation evaluated every source grid against every target
    condition.  This version indexes target member cells by coordinate and
    inspects only the square whose Chebyshev radius can still contain the
    globally selected member cell.

    The square radius deliberately uses ``max(max_load, max_inlet)`` rather
    than the final axis-specific rectangle.  This preserves the legacy rule in
    ``minimum_offsets_to_grids``: an out-of-axis member may be the globally
    nearest member and therefore reject a condition even when another member
    lies inside the final rectangle.
    """

    cfg = training.get("neighbor_policy", {})
    max_load = int(training["condition_attribution"]["max_load_grid_offset"])
    max_inlet = int(
        training["condition_attribution"]["max_inlet_so2_grid_offset"]
    )
    minimum_weight = float(cfg.get("minimum_mapping_weight", 0.10))
    mode = str(cfg.get("distance_weight_mode", "LINEAR_AXIS"))
    columns = [
        "anchor_grid_id",
        "neighbor_target_condition_label",
        "neighbor_load_grid_offset",
        "neighbor_inlet_so2_grid_offset",
        "neighbor_mapping_weight",
    ]

    # Preserve insertion order because it keeps generated artifacts stable and
    # makes old/new diagnostics easier to compare.
    target_order = {str(label): index for index, label in enumerate(target_grids)}
    coordinate_targets: dict[tuple[int, int], list[str]] = defaultdict(list)
    seen_members: set[tuple[str, int, int]] = set()
    for target_label, member_grid_ids in target_grids.items():
        label = str(target_label)
        for member_grid_id in member_grid_ids:
            parsed = parse_grid_id(member_grid_id)
            if parsed is None:
                continue
            key = (label, parsed[0], parsed[1])
            if key in seen_members:
                continue
            seen_members.add(key)
            coordinate_targets[parsed].append(label)

    radius = max(max_load, max_inlet)
    rows: list[dict[str, Any]] = []
    source_ids = list(dict.fromkeys(str(value) for value in source_grid_ids))
    for source_grid_id in source_ids:
        source = parse_grid_id(source_grid_id)
        if source is None:
            continue

        best_by_target: dict[str, tuple[int, int]] = {}
        source_p, source_s = source
        for p_level in range(source_p - radius, source_p + radius + 1):
            for s_level in range(source_s - radius, source_s + radius + 1):
                labels = coordinate_targets.get((p_level, s_level))
                if not labels:
                    continue
                offsets = (abs(source_p - p_level), abs(source_s - s_level))
                candidate_key = (
                    max(offsets),
                    sum(offsets),
                    offsets[0],
                    offsets[1],
                )
                for label in labels:
                    current = best_by_target.get(label)
                    if current is None:
                        best_by_target[label] = offsets
                        continue
                    current_key = (
                        max(current),
                        sum(current),
                        current[0],
                        current[1],
                    )
                    if candidate_key < current_key:
                        best_by_target[label] = offsets

        for target_label in sorted(
            best_by_target, key=lambda value: target_order.get(value, 10**12)
        ):
            load_offset, inlet_offset = best_by_target[target_label]
            if load_offset > max_load or inlet_offset > max_inlet:
                continue
            mapping_weight = distance_mapping_weight(
                load_offset,
                inlet_offset,
                max_load,
                max_inlet,
                mode,
            )
            if mapping_weight < minimum_weight:
                continue
            rows.append(
                {
                    "anchor_grid_id": source_grid_id,
                    "neighbor_target_condition_label": target_label,
                    "neighbor_load_grid_offset": int(load_offset),
                    "neighbor_inlet_so2_grid_offset": int(inlet_offset),
                    "neighbor_mapping_weight": float(mapping_weight),
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _neighbor_projection_columns(
    source: pd.DataFrame, plant: dict[str, Any]
) -> list[str]:
    """Return only columns consumed by neighbor profile aggregation."""
    required = {
        "anchor_grid_id",
        "start_grid_id",
        "condition_label",
        "policy_state_key_no_grid",
        "action_id",
        "action_family",
        "action_direction",
        "action_magnitude",
        "evidence_weight",
        "so2_effect_direction",
        "so2_effect_strength",
        "delta_outlet_so2",
        "stable_response",
        "oscillation_detected",
        "short_reverse_action",
        "post_outlet_so2_range",
        "post_outlet_so2_std",
        "outlet_so2_out_of_range",
        "outlet_so2_over_hard_max",
        "continuous_segment_id",
        "event_date",
        "action_start_time",
        "episode_type",
        "disturbance_mode",
        "attribution_source",
        "training_route",
        "neighborhood_coverage_ratio",
    }
    for valve in all_valves(plant):
        required.add(f"delta_valve__{valve['valve_id']}")
    for tower in enabled_towers(plant):
        tower_id = tower["tower_id"]
        required.update(
            {
                f"delta_ph__{tower_id}",
                f"post_ph_range__{tower_id}",
                f"ph_below_limit__{tower_id}",
                f"ph_above_limit__{tower_id}",
                f"ph_out_of_range__{tower_id}",
            }
        )
    return [column for column in source.columns if column in required]


def _prepare_neighbor_profile_helpers(
    mapped: pd.DataFrame,
    plant: dict[str, Any],
    training: dict[str, Any],
) -> pd.DataFrame:
    """Precompute values that V1.8A recalculated inside every action group."""
    safety_columns = ["outlet_so2_out_of_range"] + [
        f"ph_out_of_range__{tower['tower_id']}"
        for tower in enabled_towers(plant)
    ]
    any_safety = pd.Series(False, index=mapped.index)
    for column in safety_columns:
        if column in mapped.columns:
            any_safety = any_safety | mapped[column].fillna(False).astype(bool)
    mapped["__any_safety_violation"] = any_safety
    mapped["__normalized_condition_label"] = mapped["condition_label"].map(
        normalize_condition_label
    )
    mapped["__grid_id_text"] = mapped["anchor_grid_id"].astype(str)

    dead = float(training["response"]["ph_direction_deadband"])
    for tower in enabled_towers(plant):
        tower_id = tower["tower_id"]
        column = f"delta_ph__{tower_id}"
        if column not in mapped.columns:
            continue
        values = pd.to_numeric(mapped[column], errors="coerce")
        mapped[f"__ph_direction__{tower_id}"] = np.where(
            values > dead,
            "INCREASE",
            np.where(values < -dead, "DECREASE", "NEUTRAL"),
        )
    return mapped


def _target_batches(
    labels: list[str],
    mapping_groups: dict[str, pd.DataFrame],
    source_counts: pd.Series,
    batch_size: int,
    max_expanded_rows: int,
) -> list[list[str]]:
    """Create deterministic target batches with a conservative row estimate."""
    batches: list[list[str]] = []
    current: list[str] = []
    current_rows = 0
    for label in labels:
        mapping = mapping_groups.get(label)
        if mapping is None or mapping.empty:
            estimated = 0
        else:
            estimated = int(
                mapping["anchor_grid_id"]
                .map(source_counts)
                .fillna(0)
                .sum()
            )
        would_exceed_size = bool(current and len(current) >= batch_size)
        would_exceed_rows = bool(
            current
            and max_expanded_rows > 0
            and current_rows + estimated > max_expanded_rows
        )
        if would_exceed_size or would_exceed_rows:
            batches.append(current)
            current = []
            current_rows = 0
        current.append(label)
        current_rows += estimated
    if current:
        batches.append(current)
    return batches


def _build_neighbor_state_profiles(
    episodes: pd.DataFrame,
    target_grids: dict[str, list[str]],
    plant: dict[str, Any],
    training: dict[str, Any],
    progress: Callable[[float, str], None] | None = None,
    performance_recorder: Any | None = None,
) -> dict[str, dict[str, Any]]:
    cfg = training.get("neighbor_policy", {})
    if not cfg.get("enabled", True) or episodes.empty:
        return {}
    include_same = bool(cfg.get("include_same_condition", True))
    include_global_only = bool(cfg.get("include_global_only", False))
    allowed_routes = {"LOCAL_REGULAR"}
    if include_global_only:
        allowed_routes.add("GLOBAL_ONLY")
    source = episodes[episodes["training_route"].isin(allowed_routes)].copy()
    labels = [str(label) for label in target_grids]
    output: dict[str, dict[str, Any]] = {label: {} for label in labels}
    if source.empty:
        if progress:
            progress(1.0, "没有可构建临近工况策略的片段")
        return output

    source["anchor_grid_id"] = source["anchor_grid_id"].astype(str)
    _add_counter(performance_recorder, "neighbor_source_episode_count", len(source))
    _add_counter(performance_recorder, "neighbor_target_condition_count", len(labels))
    with _measure(performance_recorder, "neighbor_build_mapping_table"):
        mapping_table = build_neighbor_mapping_table(
            source["anchor_grid_id"].dropna().unique(), target_grids, training
        )
    _add_counter(performance_recorder, "neighbor_mapping_row_count", len(mapping_table))
    if mapping_table.empty:
        if progress:
            progress(1.0, "空间半径内没有可映射的临近工况证据")
        return output

    # Keep only profile inputs before expansion.  This avoids duplicating large
    # audit-only strings and path fields for every neighboring target.
    with _measure(performance_recorder, "neighbor_project_source_columns"):
        source = source[_neighbor_projection_columns(source, plant)].copy()
        source_counts = source["anchor_grid_id"].value_counts(dropna=False)
        mapping_groups = {
            str(label): group
            for label, group in mapping_table.groupby(
                "neighbor_target_condition_label", sort=False, observed=True
            )
        }

    perf = training.get("performance", {})
    batch_size = max(1, int(perf.get("neighbor_target_condition_batch_size", 64)))
    max_expanded_rows = max(
        0, int(perf.get("neighbor_max_expanded_rows_per_batch", 500_000))
    )
    batches = _target_batches(
        labels,
        mapping_groups,
        source_counts,
        batch_size,
        max_expanded_rows,
    )
    _add_counter(performance_recorder, "neighbor_batch_count", len(batches))

    processed_targets = 0
    total_expanded_rows = 0
    maximum_batch_expanded_rows = 0
    profile_count = 0
    for batch_index, batch_labels in enumerate(batches, start=1):
        batch_parts = [
            mapping_groups[label]
            for label in batch_labels
            if label in mapping_groups and not mapping_groups[label].empty
        ]
        if not batch_parts:
            processed_targets += len(batch_labels)
            continue
        with _measure(performance_recorder, "neighbor_batch_merge"):
            batch_mapping = pd.concat(batch_parts, ignore_index=True, copy=False)
            mapped = source.merge(
                batch_mapping,
                on="anchor_grid_id",
                how="inner",
                sort=False,
                copy=False,
            )
            if not include_same and not mapped.empty:
                mapped = mapped[
                    mapped["condition_label"].astype("object").astype(str)
                    != mapped["neighbor_target_condition_label"].astype(str)
                ].copy()
        if mapped.empty:
            processed_targets += len(batch_labels)
            continue

        total_expanded_rows += len(mapped)
        maximum_batch_expanded_rows = max(maximum_batch_expanded_rows, len(mapped))
        with _measure(performance_recorder, "neighbor_prepare_batch_columns"):
            mapped["aggregation_weight"] = (
                pd.to_numeric(mapped.get("evidence_weight", 1.0), errors="coerce")
                .fillna(1.0)
                .astype(float)
                * pd.to_numeric(
                    mapped["neighbor_mapping_weight"], errors="coerce"
                )
                .fillna(0.0)
                .astype(float)
            )
            mapped = _prepare_neighbor_profile_helpers(mapped, plant, training)
        if bool(perf.get("categorical_groupby_keys", True)):
            for column in (
                "neighbor_target_condition_label",
                "policy_state_key_no_grid",
                "action_id",
            ):
                if column in mapped.columns and not isinstance(
                    mapped[column].dtype, pd.CategoricalDtype
                ):
                    mapped[column] = mapped[column].astype("category")

        # One groupby replaces target → state → action nested groupby objects.
        with _measure(performance_recorder, "neighbor_aggregate_profiles"):
            for (target_label, state, action_id), action_group in mapped.groupby(
                [
                    "neighbor_target_condition_label",
                    "policy_state_key_no_grid",
                    "action_id",
                ],
                dropna=False,
                observed=True,
                sort=True,
            ):
                target_text = str(target_label)
                state_text = str(state)
                output.setdefault(target_text, {}).setdefault(state_text, {})[
                    str(action_id)
                ] = aggregate_action_profile(action_group, plant, training)
                profile_count += 1

        processed_targets += len(batch_labels)
        if progress:
            progress(
                min(0.999, processed_targets / max(len(labels), 1)),
                (
                    f"构建临近工况策略批次 {batch_index}/{len(batches)}："
                    f"目标工况={len(batch_labels)}，展开行={len(mapped)}"
                ),
            )
        del mapped, batch_mapping

    _add_counter(
        performance_recorder, "neighbor_total_expanded_row_count", total_expanded_rows
    )
    _add_counter(
        performance_recorder,
        "neighbor_maximum_batch_expanded_row_count",
        maximum_batch_expanded_rows,
    )
    _add_counter(performance_recorder, "neighbor_action_profile_count", profile_count)
    if progress:
        progress(1.0, f"临近工况策略完成，共 {len(output)} 个目标工况")
    return output

def aggregate_all_levels(
    valid_episodes: pd.DataFrame,
    plant: dict[str, Any],
    training: dict[str, Any],
    progress: Callable[[float, str], None] | None = None,
    condition_members: dict[str, list[str]] | None = None,
    performance_recorder: Any | None = None,
) -> dict[str, Any]:
    """V1.8B：严格等价加速后的多层策略聚合。"""
    empty = {
        "conditions": {},
        "condition_grids": {},
        "neighbor_state": {},
        "plant_action_prior": {},
        "transients": {},
    }
    condition_members = {
        str(label): list(grid_ids)
        for label, grid_ids in (condition_members or {}).items()
    }
    if valid_episodes.empty:
        empty["conditions"] = {label: {} for label in condition_members}
        empty["condition_grids"] = {
            label: {grid_id: {} for grid_id in grid_ids}
            for label, grid_ids in condition_members.items()
        }
        empty["neighbor_state"] = {label: {} for label in condition_members}
        return empty

    episodes = _categorize_groupby_keys(
        _normalize_episode_labels(valid_episodes), training
    )
    local = episodes[episodes["training_route"] == "LOCAL_REGULAR"].copy()
    transient = episodes[episodes["training_route"] == "TRANSIENT"].copy()
    prior_source = episodes[episodes["training_route"].isin(["LOCAL_REGULAR", "GLOBAL_ONLY"])].copy()

    def emit(start: float, end: float):
        if not progress:
            return None
        return lambda value, message: progress(start + (end - start) * value, message)

    local["aggregation_weight"] = pd.to_numeric(local["evidence_weight"], errors="coerce").fillna(1.0)
    conditions = build_nested_profiles(
        local, "condition_label", "policy_state_key_no_grid", plant, training, emit(0.00, 0.24)
    )
    for label in condition_members:
        conditions.setdefault(label, {})

    condition_grids: dict[str, dict[str, dict[str, Any]]] = {}
    labels = list(local["condition_label"].dropna().unique())
    for index, label in enumerate(labels, start=1):
        if progress:
            progress(0.24 + 0.18 * (index - 1) / max(len(labels), 1), f"聚合工况 {label} 的锚点基础格")
        subset = local[local["condition_label"] == label]
        condition_grids[str(label)] = build_nested_profiles(
            subset, "anchor_grid_id", "policy_state_key", plant, training
        )

    # 当前第一模块快照是成员格的唯一事实源。没有传入时仅用于兼容旧调用。
    target_grids: dict[str, list[str]] = dict(condition_members)
    if not target_grids:
        for label, group in episodes.groupby("condition_label", dropna=False, observed=True):
            target_grids[str(label)] = sorted(set(group["anchor_grid_id"].dropna().astype(str)))
    for label, grid_ids in target_grids.items():
        condition_grids.setdefault(label, {})
        for grid_id in grid_ids:
            condition_grids[label].setdefault(grid_id, {})
    neighbor_state = _build_neighbor_state_profiles(
        episodes,
        target_grids,
        plant,
        training,
        emit(0.42, 0.68),
        performance_recorder=performance_recorder,
    )

    plant_action_prior: dict[str, Any] = {}
    if training.get("plant_action_prior", {}).get("enabled", True) and not prior_source.empty:
        prior_source["aggregation_weight"] = pd.to_numeric(
            prior_source["evidence_weight"], errors="coerce"
        ).fillna(1.0)
        # 全厂先验仅按动作聚合；外层固定 ALL_ACTIONS，便于保持统一读取结构。
        prior_source["__prior_state"] = "ALL_ACTIONS"
        plant_action_prior = build_nested_profiles(
            prior_source,
            None,
            "__prior_state",
            plant,
            training,
            emit(0.68, 0.82),
            profile_builder=aggregate_plant_action_prior,
        ).get("PLANT", {})

    transient["aggregation_weight"] = 1.0
    transients = build_nested_profiles(
        transient,
        "disturbance_mode",
        "policy_state_key_no_grid",
        plant,
        training,
        emit(0.82, 1.00),
    )
    if progress:
        progress(1.0, f"全部层级聚合完成：本地工况={len(conditions)}，临近策略={len(neighbor_state)}")
    return {
        "conditions": conditions,
        "condition_grids": condition_grids,
        "neighbor_state": neighbor_state,
        "plant_action_prior": plant_action_prior,
        "transients": transients,
    }
