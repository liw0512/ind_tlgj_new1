from __future__ import annotations

from typing import Any

import pandas as pd

from system.model.map_control.fast_change_mode.fast_change_config import (
    FAST_CHANGE_CONFIG,
)

from .reliability import calculate_reliability, profile_status
from .utils import bool_value, hash_text, quantiles


_LEARNABLE_SHAPES = {"STEP", "PULSE", "BOOST_STEP"}
_FAST_PROFILE_KINDS = {
    "FAST_EXACT",
    "FAST_DIRECTION_SEVERITY_POOL",
    "FAST_PLANT_SAFE_BASELINE",
}


def _distribution(series: pd.Series) -> dict[str, float]:
    q25, median, q75 = quantiles(series, (0.25, 0.50, 0.75))
    return {"p25": q25, "median": median, "p75": q75}


def _numeric_quantile(series: pd.Series, q: float) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return float("nan")
    return float(values.quantile(float(q)))


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


def _fast_rate_thresholds() -> dict[str, float]:
    """Return the exact severity thresholds used by offline and online V1.

    The base rate comes from the configured FAST trigger.  Multipliers are
    stored in every learned FAST profile so a deployed snapshot remains
    self-describing even if later code defaults change.
    """
    trend = FAST_CHANGE_CONFIG.get("trend", {}) or {}
    overrides = trend.get("axis_overrides", {}) or {}
    configured_fast_rates = []
    for override in overrides.values():
        try:
            value = float((override or {}).get("fast_rate"))
        except (TypeError, ValueError):
            continue
        if value > 0:
            configured_fast_rates.append(value)
    base = min(configured_fast_rates) if configured_fast_rates else 120.0
    multipliers = (
        FAST_CHANGE_CONFIG.get("feedforward", {}).get(
            "severity_rate_multipliers", {}
        )
        or {}
    )
    return {
        "L1": float(base * float(multipliers.get("L1", 1.0))),
        "L2": float(base * float(multipliers.get("L2", 4.0 / 3.0))),
        "L3": float(base * float(multipliers.get("L3", 11.0 / 6.0))),
    }


def _fast_level(rate: Any, thresholds: dict[str, float]) -> str | None:
    try:
        magnitude = abs(float(rate))
    except (TypeError, ValueError):
        return None
    if magnitude >= float(thresholds["L3"]):
        return "L3"
    if magnitude >= float(thresholds["L2"]):
        return "L2"
    if magnitude >= float(thresholds["L1"]):
        return "L1"
    return None


def _ph_safe_rows(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype=bool)

    def safe(row: pd.Series) -> bool:
        tower_id = str(row.get("flow_event_tower_id", ""))
        column = f"ph_out_of_range__{tower_id}"
        return not bool_value(row.get(column), False)

    return frame.apply(safe, axis=1)


def _safe_increase_evidence(
    source: pd.DataFrame,
    *,
    fast_only: bool,
) -> pd.DataFrame:
    """Select history that is safe to teach a protective INCREASE action.

    Future response data is intentionally used only as the teacher/quality
    label (safe ratio, pH and emission hard limits).  Matching features remain
    strictly anchored at action_start_time, so no future disturbance endpoint
    leaks into online inference.
    """
    required = {
        "action_direction",
        "flow_event_final_delta_flow",
        "flow_event_peak_delta_flow",
        "post_outlet_so2_safe_ratio",
        "outlet_so2_over_hard_max",
        "flow_event_tower_id",
    }
    if source.empty or not required.issubset(source.columns):
        return source.iloc[0:0].copy()
    cfg = FAST_CHANGE_CONFIG.get("feedforward", {}) or {}
    minimum_safe_ratio = float(cfg.get("minimum_safe_ratio", 0.85))
    final_delta = pd.to_numeric(source["flow_event_final_delta_flow"], errors="coerce")
    safe_ratio = pd.to_numeric(source["post_outlet_so2_safe_ratio"], errors="coerce")
    mask = (
        source["action_direction"].astype(str).str.upper().eq("INCREASE")
        & final_delta.gt(0.0)
        & safe_ratio.ge(minimum_safe_ratio)
        & ~source["outlet_so2_over_hard_max"].map(bool_value)
        & _ph_safe_rows(source)
    )
    if fast_only:
        required_fast = {
            "fast_change_mode",
            "fast_change_direction",
            "fast_change_primary_axis_rate",
            "fast_change_exact_trend_mode",
        }
        if not required_fast.issubset(source.columns):
            return source.iloc[0:0].copy()
        mask &= (
            source["fast_change_mode"].astype(str).str.upper().eq("FAST_CHANGE")
            & source["fast_change_direction"].astype(str).str.upper().eq("RISE")
        )
    return source.loc[mask].copy()


def _fast_profile(
    group: pd.DataFrame,
    *,
    kind: str,
    identity_parts: tuple[Any, ...],
    quantile: float,
    thresholds: dict[str, float],
    minimum_events: int,
    condition_label: str | None = None,
    fast_level: str | None = None,
    exact_mode: str | None = None,
) -> dict[str, Any] | None:
    if len(group) < int(minimum_events):
        return None
    tower_id = str(group["flow_event_tower_id"].iloc[0])
    final_delta = pd.to_numeric(group["flow_event_final_delta_flow"], errors="coerce")
    peak_delta = pd.to_numeric(group["flow_event_peak_delta_flow"], errors="coerce")
    final_delta = final_delta[final_delta > 0].dropna()
    peak_delta = peak_delta[peak_delta > 0].dropna()
    if final_delta.empty or peak_delta.empty:
        return None
    recommended = _numeric_quantile(final_delta, quantile)
    if not pd.notna(recommended) or recommended <= 0:
        return None

    event_dates = group.get("event_date", pd.Series(dtype="object")).dropna().astype(str)
    segment_ids = group.get("continuous_segment_id", pd.Series(dtype="object")).dropna().astype(str)
    safe_ratio = pd.to_numeric(
        group.get("post_outlet_so2_safe_ratio", pd.Series(dtype=float)),
        errors="coerce",
    ).dropna()
    effect_direction, effect_consistency, effect_counts = _dominant_direction(
        group.get("flow_effect_outlet_so2_direction", pd.Series("UNKNOWN", index=group.index))
    )
    identity = "|".join(map(str, (kind,) + identity_parts))
    profile_id = "FAST_FLOW_" + hash_text(identity, 24)
    return {
        "prototype_id": profile_id,
        "profile_kind": kind,
        "fast_feedforward_semantics": str(
            FAST_CHANGE_CONFIG.get("feedforward", {}).get(
                "semantics_version", "CAUSAL_FAST_FEEDFORWARD_V1"
            )
        ),
        "condition_label": condition_label or "*",
        "tower_id": tower_id,
        "action_direction": "INCREASE",
        "flow_shape": "FAST_FEEDFORWARD",
        "flow_execution_profile": "CAUSAL_FAST_BASELINE",
        "fast_direction": "RISE",
        "fast_level": fast_level or "*",
        "fast_exact_trend_mode": exact_mode or "*",
        "fast_rate_thresholds": dict(thresholds),
        "event_count": int(len(group)),
        "independent_segment_count": int(segment_ids.nunique()),
        "independent_day_count": int(event_dates.nunique()),
        "recommended_delta_flow": float(recommended),
        "selection_quantile": float(quantile),
        "target_flow": {
            "final_delta_flow": _distribution(final_delta),
            "peak_delta_flow": _distribution(peak_delta),
            "max_abs_delta_flow": _distribution(
                group.get("flow_event_max_abs_delta_flow", final_delta)
            ),
        },
        "effect": {
            "outlet_so2_direction": effect_direction,
            "outlet_so2_direction_consistency": effect_consistency,
            "outlet_so2_direction_counts": effect_counts,
            "post_outlet_so2_safe_ratio": _distribution(safe_ratio),
        },
        "timing": {
            "settled_ratio": float(
                group.get("flow_timing_settled", pd.Series(False, index=group.index))
                .map(bool_value)
                .mean()
            ),
        },
        "safety": {
            "minimum_training_safe_ratio": float(
                FAST_CHANGE_CONFIG.get("feedforward", {}).get(
                    "minimum_safe_ratio", 0.85
                )
            ),
            "observed_safe_ratio_median": (
                float(safe_ratio.median()) if not safe_ratio.empty else None
            ),
            "hard_violation_event_count": 0,
        },
        "evidence": {
            "status": "FAST_SUPPORTED",
            "episode_ids": sorted(group["episode_id"].astype(str).tolist()),
            "event_count": int(len(group)),
            "source": kind,
        },
    }


def _add_fast_feedforward_profiles(
    profiles: dict[str, dict[str, Any]],
    source: pd.DataFrame,
) -> None:
    """Append EXACT -> POOL -> PLANT_BASELINE profiles to the same snapshot.

    Reusing supply_flow_prototypes.pkl means the new fallback path automatically
    inherits the existing version pointer, manifest hash, rollback and
    incremental cumulative-episode machinery.
    """
    cfg = FAST_CHANGE_CONFIG.get("feedforward", {}) or {}
    thresholds = _fast_rate_thresholds()
    safe_fast = _safe_increase_evidence(source, fast_only=True)
    safe_all = _safe_increase_evidence(source, fast_only=False)

    if not safe_fast.empty:
        safe_fast["__fast_level"] = safe_fast["fast_change_primary_axis_rate"].map(
            lambda value: _fast_level(value, thresholds)
        )
        safe_fast = safe_fast[safe_fast["__fast_level"].notna()].copy()

    # 1) Exact: current condition + FAST level + exact trend mode + tower.
    if not safe_fast.empty:
        exact_columns = [
            "condition_label",
            "flow_event_tower_id",
            "__fast_level",
            "fast_change_exact_trend_mode",
        ]
        for keys, group in safe_fast.groupby(exact_columns, dropna=False, sort=True):
            condition_label, tower_id, fast_level, exact_mode = map(str, keys)
            profile = _fast_profile(
                group,
                kind="FAST_EXACT",
                identity_parts=(condition_label, tower_id, fast_level, exact_mode),
                quantile=float(cfg.get("exact_action_quantile", 0.50)),
                thresholds=thresholds,
                minimum_events=int(cfg.get("minimum_exact_events", 2)),
                condition_label=condition_label,
                fast_level=fast_level,
                exact_mode=exact_mode,
            )
            if profile:
                profiles[profile["prototype_id"]] = profile

        # 2) Direction/severity pool: ignore condition_label and exact path.
        pool_columns = ["flow_event_tower_id", "__fast_level"]
        for keys, group in safe_fast.groupby(pool_columns, dropna=False, sort=True):
            tower_id, fast_level = map(str, keys)
            profile = _fast_profile(
                group,
                kind="FAST_DIRECTION_SEVERITY_POOL",
                identity_parts=(tower_id, fast_level),
                quantile=float(cfg.get("pool_action_quantile", 0.25)),
                thresholds=thresholds,
                minimum_events=int(cfg.get("minimum_pool_events", 3)),
                fast_level=fast_level,
            )
            if profile:
                profiles[profile["prototype_id"]] = profile

    # 3) Plant-safe baseline: deliberately independent of FAST history.
    # Any historically safe positive supply-flow action can teach a conservative
    # first protective step. This is what prevents NO_FAST_HISTORY -> no action.
    if not safe_all.empty:
        for tower_id, group in safe_all.groupby("flow_event_tower_id", dropna=False, sort=True):
            tower_id = str(tower_id)
            profile = _fast_profile(
                group,
                kind="FAST_PLANT_SAFE_BASELINE",
                identity_parts=(tower_id,),
                quantile=float(cfg.get("baseline_action_quantile", 0.25)),
                thresholds=thresholds,
                minimum_events=int(cfg.get("minimum_baseline_events", 5)),
            )
            if profile:
                profiles[profile["prototype_id"]] = profile


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
            "profile_kind": "REGULAR_SUPPLY_FLOW",
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

    _add_fast_feedforward_profiles(profiles, source)
    return profiles
