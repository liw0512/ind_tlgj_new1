from __future__ import annotations

from typing import Any

import pandas as pd

from .reliability import calculate_reliability, profile_status
from .utils import bool_value, hash_text, quantiles


_LEARNABLE_SHAPES = {"STEP", "PULSE", "BOOST_STEP"}


def _distribution(series: pd.Series) -> dict[str, float]:
    q25, median, q75 = quantiles(series, (0.25, 0.50, 0.75))
    return {"p25": q25, "median": median, "p75": q75}


def _dominant_direction(series: pd.Series) -> tuple[str, float, dict[str, int]]:
    values = series.fillna("UNKNOWN").astype(str).str.upper()
    counts = values.value_counts().to_dict()
    if not counts:
        return "UNKNOWN", 0.0, {}
    dominant = max(sorted(counts), key=lambda value: counts[value])
    return dominant, float(counts[dominant] / len(values)), {
        str(key): int(value) for key, value in counts.items()
    }


def _eligible_flow_evidence(
    episodes: pd.DataFrame,
    training: dict[str, Any],
) -> pd.DataFrame:
    required = {
        "episode_type",
        "flow_context",
        "flow_learning_eligible",
        "flow_effect_complete",
        "flow_shape",
        "condition_label",
        "flow_event_tower_id",
    }
    if episodes.empty or not required.issubset(episodes.columns):
        return episodes.iloc[0:0].copy()
    eligible = (
        episodes["episode_type"].astype(str).isin(
            {"FLOW_ACTION", "FLOW_ACTION_CANDIDATE"}
        )
        & (episodes["flow_context"].astype(str) == "CLEAN")
        & episodes["flow_learning_eligible"].map(bool_value)
        & episodes["flow_effect_complete"].map(bool_value)
        & episodes["flow_shape"].astype(str).isin(_LEARNABLE_SHAPES)
        & ~episodes["condition_label"].fillna("UNKNOWN").astype(str).isin(
            ["", "UNKNOWN", "None", "nan"]
        )
        & ~episodes["flow_event_tower_id"].fillna("UNKNOWN").astype(str).isin(
            ["", "UNKNOWN", "None", "nan"]
        )
    )
    validity = training.get("validity", {})
    if validity.get("require_condition_valid", True):
        condition_valid = episodes.get(
            "condition_valid", pd.Series(False, index=episodes.index)
        ).map(bool_value)
        eligible &= condition_valid
    if not validity.get("allow_out_of_range_clipped", True):
        clipped = episodes.get(
            "out_of_range_clipped", pd.Series(False, index=episodes.index)
        ).map(bool_value)
        eligible &= ~clipped
    return episodes.loc[eligible].copy()


def build_supply_flow_prototypes(
    episodes: pd.DataFrame,
    training: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Aggregate clean actual-flow episodes without using legacy valve deltas."""
    source = _eligible_flow_evidence(episodes, training)
    if source.empty:
        return {}

    group_columns = [
        "condition_label",
        "flow_event_tower_id",
        "action_direction",
        "flow_shape",
        "flow_execution_profile",
    ]
    for column in group_columns:
        if column not in source.columns:
            source[column] = "UNKNOWN"
        source[column] = source[column].fillna("UNKNOWN").astype(str)

    profiles: dict[str, dict[str, Any]] = {}
    reliability_config = training["reliability"]
    for keys, group in source.groupby(group_columns, dropna=False, sort=True):
        condition_label, tower_id, direction, shape, execution_profile = keys
        source_files = group.get(
            "source_files", pd.Series("", index=group.index)
        ).fillna("").astype(str)
        segment_ids = group.get(
            "continuous_segment_id", pd.Series(pd.NA, index=group.index)
        )
        valid_segments = segment_ids.notna() & ~segment_ids.astype(str).isin(
            ["", "UNKNOWN", "None", "nan"]
        )
        segment_keys = (
            source_files[valid_segments]
            + "|"
            + segment_ids[valid_segments].astype(str)
        )
        event_dates = group.get(
            "event_date", pd.Series(pd.NA, index=group.index)
        ).dropna().astype(str)
        event_dates = event_dates[
            ~event_dates.isin(["", "UNKNOWN", "None", "nan"])
        ]
        so2_direction, direction_consistency, so2_direction_counts = (
            _dominant_direction(group["flow_effect_outlet_so2_direction"])
        )
        ph_direction, ph_direction_consistency, ph_direction_counts = (
            _dominant_direction(group["flow_effect_tower_ph_direction"])
        )
        stable_ratio = float(group["flow_timing_settled"].map(bool_value).mean())
        ph_out_column = f"ph_out_of_range__{tower_id}"
        ph_out = (
            group[ph_out_column].map(bool_value)
            if ph_out_column in group.columns
            else pd.Series(False, index=group.index)
        )
        so2_out = group.get(
            "outlet_so2_over_hard_max", pd.Series(False, index=group.index)
        ).map(bool_value)
        safety_violation_ratio = float((ph_out | so2_out).mean())
        event_count = int(len(group))
        segment_count = int(segment_keys.nunique())
        day_count = int(event_dates.nunique())
        reliability = calculate_reliability(
            event_count,
            segment_count,
            day_count,
            direction_consistency,
            stable_ratio,
            safety_violation_ratio,
            reliability_config,
        )
        identity = "|".join(map(str, keys))
        prototype_id = "FLOW_PROTO_" + hash_text(identity, 24)
        ph_delta_column = f"delta_ph__{tower_id}"
        profiles[prototype_id] = {
            "prototype_id": prototype_id,
            "condition_label": condition_label,
            "tower_id": tower_id,
            "action_direction": direction,
            "flow_shape": shape,
            "flow_execution_profile": execution_profile,
            "event_count": event_count,
            "independent_segment_count": segment_count,
            "independent_day_count": day_count,
            "target_flow": {
                "final_delta_flow": _distribution(group["flow_event_final_delta_flow"]),
                "peak_delta_flow": _distribution(group["flow_event_peak_delta_flow"]),
                "max_abs_delta_flow": _distribution(
                    group["flow_event_max_abs_delta_flow"]
                ),
            },
            "execution": {
                "active_duration_minutes": _distribution(
                    group["flow_event_active_duration_minutes"]
                ),
                "signed_slurry_volume": _distribution(
                    group["flow_event_signed_slurry_volume"]
                ),
                "temporary_plateau_ratio": float(
                    group["flow_temporary_plateau"].map(bool_value).mean()
                ),
            },
            "effect": {
                "outlet_so2_direction": so2_direction,
                "outlet_so2_direction_consistency": direction_consistency,
                "outlet_so2_direction_counts": so2_direction_counts,
                "delta_outlet_so2": _distribution(group["delta_outlet_so2"]),
                "tower_ph_direction": ph_direction,
                "tower_ph_direction_consistency": ph_direction_consistency,
                "tower_ph_direction_counts": ph_direction_counts,
                "delta_tower_ph": _distribution(
                    group.get(ph_delta_column, pd.Series(dtype=float))
                ),
            },
            "timing": {
                "observed_response_delay_minutes": _distribution(
                    group["flow_timing_observed_response_delay_minutes"]
                ),
                "time_to_extreme_minutes": _distribution(
                    group["flow_timing_time_to_extreme_minutes"]
                ),
                "time_to_stable_minutes": _distribution(
                    group["flow_timing_time_to_stable_minutes"]
                ),
                "settled_ratio": stable_ratio,
            },
            "safety": {
                "violation_ratio": safety_violation_ratio,
                "outlet_so2_hard_max_event_count": int(so2_out.sum()),
                "tower_ph_out_of_range_event_count": int(ph_out.sum()),
            },
            "evidence": {
                "episode_ids": sorted(group["episode_id"].astype(str).tolist()),
                "status": profile_status(
                    event_count,
                    segment_count,
                    day_count,
                    reliability_config,
                ),
                "reliability": reliability,
            },
        }
    return profiles
