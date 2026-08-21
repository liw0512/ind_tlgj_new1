from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .config_loader import enabled_towers
from .schema import OUTLET_SO2_COLUMN, time_column
from .supply_flow_event_detector import SupplyFlowEvent
from .time_index import TimeWindowIndexer
from .utils import direction_from_delta, sign_change_count, window_coverage_ratio


@dataclass(frozen=True)
class SupplyFlowEffectProfile:
    baseline_start_time: pd.Timestamp
    response_start_time: pd.Timestamp
    response_end_time: pd.Timestamp
    baseline_coverage_ratio: float
    response_coverage_ratio: float
    baseline_outlet_so2: float
    response_outlet_so2: float
    delta_outlet_so2: float
    outlet_so2_direction: str
    response_outlet_so2_min: float
    response_outlet_so2_max: float
    response_outlet_so2_range: float
    outlet_so2_safe_ratio: float
    outlet_so2_over_hard_max: bool
    baseline_tower_ph: float
    response_tower_ph: float
    delta_tower_ph: float
    tower_ph_direction: str
    response_tower_ph_min: float
    response_tower_ph_max: float
    tower_ph_out_of_range: bool
    oscillation_sign_changes: int
    complete: bool
    reason: str


@dataclass(frozen=True)
class SupplyFlowTimingProfile:
    first_effect_time: pd.Timestamp | None
    observed_response_delay_minutes: float
    extreme_effect_time: pd.Timestamp | None
    time_to_extreme_minutes: float
    stable_time: pd.Timestamp | None
    time_to_stable_minutes: float
    settled: bool


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    ).dropna()


def _median(series: pd.Series) -> float:
    values = _numeric(series)
    return float(values.median()) if not values.empty else float("nan")


def summarize_supply_flow_response(
    baseline_outlet_so2: float,
    baseline_tower_ph: float,
    response_outlet_so2: pd.Series,
    response_tower_ph: pd.Series,
    outlet_so2_safe_range: tuple[float, float],
    tower_ph_safe_range: tuple[float, float],
    response_config: dict[str, Any],
) -> dict[str, Any]:
    """One metric definition shared by offline profiles and online effect QA."""
    so2 = _numeric(response_outlet_so2)
    ph = _numeric(response_tower_ph)
    response_so2 = float(so2.median()) if not so2.empty else float("nan")
    response_ph = float(ph.median()) if not ph.empty else float("nan")
    delta_so2 = response_so2 - baseline_outlet_so2
    delta_ph = response_ph - baseline_tower_ph
    so2_low, so2_high = map(float, outlet_so2_safe_range)
    ph_low, ph_high = map(float, tower_ph_safe_range)
    return {
        "response_outlet_so2": response_so2,
        "delta_outlet_so2": delta_so2,
        "outlet_so2_direction": direction_from_delta(
            delta_so2, float(response_config["so2_direction_deadband"])
        ),
        "response_outlet_so2_min": (
            float(so2.min()) if not so2.empty else float("nan")
        ),
        "response_outlet_so2_max": (
            float(so2.max()) if not so2.empty else float("nan")
        ),
        "response_outlet_so2_range": (
            float(so2.max() - so2.min()) if not so2.empty else float("nan")
        ),
        "outlet_so2_safe_ratio": (
            float(((so2 >= so2_low) & (so2 <= so2_high)).mean())
            if not so2.empty
            else 0.0
        ),
        "outlet_so2_over_hard_max": bool(
            not so2.empty and (so2 > so2_high).any()
        ),
        "response_tower_ph": response_ph,
        "delta_tower_ph": delta_ph,
        "tower_ph_direction": direction_from_delta(
            delta_ph, float(response_config["ph_direction_deadband"])
        ),
        "response_tower_ph_min": (
            float(ph.min()) if not ph.empty else float("nan")
        ),
        "response_tower_ph_max": (
            float(ph.max()) if not ph.empty else float("nan")
        ),
        "tower_ph_out_of_range": bool(
            not ph.empty and ((ph < ph_low) | (ph > ph_high)).any()
        ),
        "oscillation_sign_changes": sign_change_count(
            so2, float(response_config["oscillation_diff_deadband"])
        ),
    }


def _event_segment(
    frame: pd.DataFrame,
    timestamp_column: str,
    event: SupplyFlowEvent,
) -> pd.DataFrame:
    result = frame.sort_values(timestamp_column, kind="stable").reset_index(drop=True)
    if "continuous_segment_id" not in result.columns:
        return result
    timestamps = pd.to_datetime(result[timestamp_column], errors="coerce")
    anchor = result.loc[timestamps >= pd.Timestamp(event.start_time)]
    if anchor.empty:
        return result.iloc[0:0]
    segment_id = anchor.iloc[0]["continuous_segment_id"]
    return result.loc[result["continuous_segment_id"] == segment_id].reset_index(drop=True)


def _timing_profile(
    event: SupplyFlowEvent,
    indexer: TimeWindowIndexer,
    timestamp_column: str,
    outlet_column: str,
    baseline_outlet_so2: float,
    response_outlet_so2: float,
    response_start: pd.Timestamp,
    response_end: pd.Timestamp,
    direction: str,
    deadband: float,
    stable_minutes: float,
    stable_range: float,
) -> SupplyFlowTimingProfile:
    post_action = indexer.slice(pd.Timestamp(event.end_time), response_end)
    first_effect_time: pd.Timestamp | None = None
    if outlet_column in post_action.columns and np.isfinite(baseline_outlet_so2):
        values = pd.to_numeric(post_action[outlet_column], errors="coerce")
        delta = values - baseline_outlet_so2
        if direction == "DECREASE":
            mask = delta <= -deadband
        elif direction == "INCREASE":
            mask = delta >= deadband
        else:
            mask = delta.abs() >= deadband
        matches = post_action.loc[mask.fillna(False)]
        if not matches.empty:
            first_effect_time = pd.Timestamp(matches.iloc[0][timestamp_column])

    response = indexer.slice(response_start, response_end)
    extreme_time: pd.Timestamp | None = None
    if outlet_column in response.columns:
        values = pd.to_numeric(response[outlet_column], errors="coerce")
        finite = values.dropna()
        if not finite.empty:
            if direction == "DECREASE":
                extreme_index = finite.idxmin()
            elif direction == "INCREASE":
                extreme_index = finite.idxmax()
            else:
                extreme_index = (finite - baseline_outlet_so2).abs().idxmax()
            extreme_time = pd.Timestamp(response.loc[extreme_index, timestamp_column])

    stable_time: pd.Timestamp | None = None
    if outlet_column in response.columns and np.isfinite(response_outlet_so2):
        for row in response.itertuples(index=False):
            current_time = pd.Timestamp(getattr(row, timestamp_column))
            if current_time - response_start < pd.Timedelta(minutes=stable_minutes):
                continue
            window = indexer.slice(
                current_time - pd.Timedelta(minutes=stable_minutes),
                current_time,
            )
            values = _numeric(window[outlet_column])
            if len(values) < 2:
                continue
            observed_range = float(values.max() - values.min())
            if (
                observed_range <= stable_range
                and abs(float(values.median()) - response_outlet_so2) <= deadband
            ):
                stable_time = current_time
                break

    def elapsed(value: pd.Timestamp | None) -> float:
        if value is None:
            return float("nan")
        return max(
            0.0,
            (value - pd.Timestamp(event.end_time)).total_seconds() / 60.0,
        )

    return SupplyFlowTimingProfile(
        first_effect_time=first_effect_time,
        observed_response_delay_minutes=elapsed(first_effect_time),
        extreme_effect_time=extreme_time,
        time_to_extreme_minutes=elapsed(extreme_time),
        stable_time=stable_time,
        time_to_stable_minutes=elapsed(stable_time),
        settled=stable_time is not None,
    )


def profile_supply_flow_effect(
    event: SupplyFlowEvent,
    frame: pd.DataFrame,
    plant: dict[str, Any],
    training: dict[str, Any],
) -> tuple[SupplyFlowEffectProfile, SupplyFlowTimingProfile]:
    """Describe SO2/pH effect after one actual slurry-flow trajectory."""
    timestamp_column = time_column(plant)
    segment = _event_segment(frame, timestamp_column, event)
    indexer = TimeWindowIndexer(segment, timestamp_column)
    episode = training["episode"]
    response_config = training["response"]
    baseline_minutes = float(episode["baseline_minutes"])
    delay_minutes = float(episode["response_delay_minutes"])
    response_minutes = float(episode["response_window_minutes"])
    stable_minutes = float(episode["action_end_stable_minutes"])
    minimum_coverage = float(episode["minimum_window_coverage_ratio"])
    so2_deadband = float(response_config["so2_direction_deadband"])
    configured_stable_range = response_config["stable_so2_range_max"]
    stable_range = (
        float(configured_stable_range)
        if configured_stable_range is not None
        else max(2.0 * so2_deadband, 1e-12)
    )

    baseline_start = pd.Timestamp(event.start_time) - pd.Timedelta(
        minutes=baseline_minutes
    )
    response_start = pd.Timestamp(event.end_time) + pd.Timedelta(
        minutes=delay_minutes
    )
    response_end = response_start + pd.Timedelta(minutes=response_minutes)
    baseline = indexer.slice(baseline_start, pd.Timestamp(event.start_time))
    response = indexer.slice(response_start, response_end)
    baseline_coverage = window_coverage_ratio(
        baseline, timestamp_column, baseline_minutes
    )
    response_coverage = window_coverage_ratio(
        response, timestamp_column, response_minutes
    )

    outlet_column = OUTLET_SO2_COLUMN
    baseline_so2 = (
        _median(baseline[outlet_column])
        if outlet_column in baseline.columns
        else float("nan")
    )
    response_so2_values = (
        _numeric(response[outlet_column])
        if outlet_column in response.columns
        else pd.Series(dtype=float)
    )
    tower = next(
        (
            item
            for item in enabled_towers(plant)
            if str(item.get("tower_id")) == str(event.tower_id)
        ),
        None,
    )
    ph_column = str((tower or {}).get("ph_column", ""))
    baseline_ph = (
        _median(baseline[ph_column])
        if ph_column and ph_column in baseline.columns
        else float("nan")
    )
    response_ph_values = (
        _numeric(response[ph_column])
        if ph_column and ph_column in response.columns
        else pd.Series(dtype=float)
    )
    safe_so2_low, safe_so2_high = map(float, plant["outlet_so2_safe_range"])
    ph_range = (tower or {}).get("ph_safe_range", [float("-inf"), float("inf")])
    ph_low, ph_high = map(float, ph_range)
    metrics = summarize_supply_flow_response(
        baseline_so2,
        baseline_ph,
        response_so2_values,
        response_ph_values,
        (safe_so2_low, safe_so2_high),
        (ph_low, ph_high),
        response_config,
    )
    reasons: list[str] = []
    if baseline_coverage < minimum_coverage:
        reasons.append("BASELINE_WINDOW_INCOMPLETE")
    if response_coverage < minimum_coverage:
        reasons.append("RESPONSE_WINDOW_INCOMPLETE")
    if not np.isfinite(baseline_so2):
        reasons.append("BASELINE_SO2_MISSING")
    if response_so2_values.empty:
        reasons.append("RESPONSE_SO2_MISSING")
    if not np.isfinite(baseline_ph) or response_ph_values.empty:
        reasons.append("TOWER_PH_MISSING")

    effect = SupplyFlowEffectProfile(
        baseline_start_time=baseline_start,
        response_start_time=response_start,
        response_end_time=response_end,
        baseline_coverage_ratio=baseline_coverage,
        response_coverage_ratio=response_coverage,
        baseline_outlet_so2=baseline_so2,
        response_outlet_so2=metrics["response_outlet_so2"],
        delta_outlet_so2=metrics["delta_outlet_so2"],
        outlet_so2_direction=metrics["outlet_so2_direction"],
        response_outlet_so2_min=metrics["response_outlet_so2_min"],
        response_outlet_so2_max=metrics["response_outlet_so2_max"],
        response_outlet_so2_range=metrics["response_outlet_so2_range"],
        outlet_so2_safe_ratio=metrics["outlet_so2_safe_ratio"],
        outlet_so2_over_hard_max=metrics["outlet_so2_over_hard_max"],
        baseline_tower_ph=baseline_ph,
        response_tower_ph=metrics["response_tower_ph"],
        delta_tower_ph=metrics["delta_tower_ph"],
        tower_ph_direction=metrics["tower_ph_direction"],
        response_tower_ph_min=metrics["response_tower_ph_min"],
        response_tower_ph_max=metrics["response_tower_ph_max"],
        tower_ph_out_of_range=metrics["tower_ph_out_of_range"],
        oscillation_sign_changes=metrics["oscillation_sign_changes"],
        complete=not reasons,
        reason=";".join(reasons) if reasons else "OK",
    )
    timing = _timing_profile(
        event,
        indexer,
        timestamp_column,
        outlet_column,
        baseline_so2,
        metrics["response_outlet_so2"],
        response_start,
        response_end,
        metrics["outlet_so2_direction"],
        so2_deadband,
        stable_minutes,
        stable_range,
    )
    return effect, timing
