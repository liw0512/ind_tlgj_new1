from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import InitialTrainingConfig


def _robust_slope_per_min(values: pd.Series, timestamps: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    times = pd.to_datetime(timestamps, errors="coerce")
    valid = numeric.notna() & times.notna()
    numeric = numeric.loc[valid].reset_index(drop=True)
    times = times.loc[valid].reset_index(drop=True)
    n = len(numeric)
    if n < 4:
        return float("nan")
    group = max(n // 3, 1)
    left_value = float(numeric.iloc[:group].median())
    right_value = float(numeric.iloc[-group:].median())
    left_time = times.iloc[:group].astype("int64").median() / 1e9
    right_time = times.iloc[-group:].astype("int64").median() / 1e9
    elapsed_minutes = (right_time - left_time) / 60.0
    if elapsed_minutes <= 0.0:
        return float("nan")
    return (right_value - left_value) / elapsed_minutes


def _median_level(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    return float(numeric.median()) if not numeric.empty else float("nan")


def _find_onset(
    work: pd.DataFrame,
    *,
    action_index: int,
    action_end_index: int,
    delta_q: float,
    value_column: str,
    pre_steps: int,
    slope_steps: int,
    search_start_steps: int,
    search_end_steps: int,
    persistence: int,
    threshold_per_min: float,
    response_kind: str,
) -> Tuple[Optional[int], float, float]:
    pre_start = action_index - pre_steps
    if pre_start < 0:
        return None, float("nan"), float("nan")
    pre = work.iloc[pre_start:action_index]
    pre_slope = _robust_slope_per_min(pre[value_column], pre["timestamp"])
    if not np.isfinite(pre_slope):
        return None, pre_slope, float("nan")

    action_sign = 1.0 if delta_q > 0 else -1.0
    start = max(action_end_index + search_start_steps, action_end_index + slope_steps)
    stop = min(action_end_index + search_end_steps, len(work) - 1)
    consecutive = 0
    first_index: Optional[int] = None
    last_improvement = float("nan")

    for endpoint in range(start, stop + 1):
        window_start = endpoint - slope_steps + 1
        if window_start <= action_end_index:
            continue
        window = work.iloc[window_start : endpoint + 1]
        post_slope = _robust_slope_per_min(window[value_column], window["timestamp"])
        if not np.isfinite(post_slope):
            consecutive = 0
            first_index = None
            continue

        if response_kind == "SO2":
            improvement = action_sign * (pre_slope - post_slope)
        elif response_kind == "PH":
            improvement = action_sign * (post_slope - pre_slope)
        else:
            raise ValueError("unsupported response_kind: %s" % response_kind)

        last_improvement = float(improvement)
        if improvement >= threshold_per_min:
            if consecutive == 0:
                first_index = endpoint
            consecutive += 1
            if consecutive >= persistence:
                assert first_index is not None
                return int(first_index), float(pre_slope), float(improvement)
        else:
            consecutive = 0
            first_index = None

    return None, float(pre_slope), float(last_improvement)


def _trend_referenced_effect(
    work: pd.DataFrame,
    *,
    action_index: int,
    onset_index: int,
    value_column: str,
    pre_steps: int,
    effect_start_steps: int,
    effect_end_steps: int,
    tail_steps: int,
) -> Tuple[float, float, float, float]:
    pre_start = action_index - pre_steps
    pre = work.iloc[pre_start:action_index]
    pre_slope = _robust_slope_per_min(pre[value_column], pre["timestamp"])
    anchor_steps = min(max(tail_steps, 1), len(pre))
    anchor = pre.iloc[-anchor_steps:]
    anchor_level = _median_level(anchor[value_column])
    anchor_time = pd.to_datetime(anchor["timestamp"]).median()

    effect_start = onset_index + effect_start_steps
    effect_end = min(onset_index + effect_end_steps, len(work) - 1)
    if effect_start > effect_end:
        return float("nan"), anchor_level, float("nan"), float("nan")
    tail_start = max(effect_end - tail_steps + 1, effect_start)
    tail = work.iloc[tail_start : effect_end + 1]
    observed = _median_level(tail[value_column])
    effect_time = pd.to_datetime(tail["timestamp"]).median()
    if (
        not np.isfinite(pre_slope)
        or not np.isfinite(anchor_level)
        or pd.isna(anchor_time)
        or pd.isna(effect_time)
    ):
        return float("nan"), anchor_level, observed, float("nan")

    elapsed_minutes = (effect_time - anchor_time).total_seconds() / 60.0
    trend_reference = anchor_level + pre_slope * elapsed_minutes
    effect = observed - trend_reference
    return float(effect), float(anchor_level), float(observed), float(trend_reference)


def estimate_event_responses(
    events: pd.DataFrame,
    work: pd.DataFrame,
    config: InitialTrainingConfig,
) -> pd.DataFrame:
    """Estimate onset and later trend-referenced effect for SO2 and pH.

    Onset may occur before the controlled variable reverses direction. For +Q,
    outlet SO2 can still be rising while a persistent reduction in its rising
    slope already counts as response onset. Phi is not computed at onset; it is
    computed later from the effect window relative to the pre-action local trend.
    """

    if events.empty:
        return pd.DataFrame()

    pre_steps = max(int(round(config.action_pre_seconds / config.sample_seconds)), 1)
    slope_steps = max(int(round(config.slope_window_seconds / config.sample_seconds)), 2)
    search_start_steps = max(int(round(config.onset_search_start_seconds / config.sample_seconds)), 1)
    search_end_steps = max(int(round(config.onset_search_end_seconds / config.sample_seconds)), search_start_steps)
    effect_start_steps = max(int(round(config.effect_start_after_onset_seconds / config.sample_seconds)), 1)
    effect_end_steps = max(int(round(config.effect_end_after_onset_seconds / config.sample_seconds)), effect_start_steps)
    tail_steps = max(int(round(config.effect_tail_seconds / config.sample_seconds)), 1)

    rows: List[dict] = []
    for _, event in events.loc[events["learnable"].astype(bool)].iterrows():
        action_index = int(event["action_index"])
        action_end_index = int(event["action_end_index"])
        delta_q = float(event["delta_q_actual_m3h"])
        segment_id = int(event["segment_id"])

        for response_kind, value_column, threshold in (
            ("SO2", config.outlet_so2_column, config.so2_min_slope_improvement_per_min),
            ("PH", config.ph_column, config.ph_min_slope_improvement_per_min),
        ):
            onset_index, pre_slope, onset_improvement = _find_onset(
                work,
                action_index=action_index,
                action_end_index=action_end_index,
                delta_q=delta_q,
                value_column=value_column,
                pre_steps=pre_steps,
                slope_steps=slope_steps,
                search_start_steps=search_start_steps,
                search_end_steps=search_end_steps,
                persistence=config.onset_persistence_windows,
                threshold_per_min=threshold,
                response_kind=response_kind,
            )

            onset_seconds = float("nan")
            effect = phi = float("nan")
            anchor_level = observed_level = trend_reference = float("nan")
            response_status = "NO_PERSISTENT_ONSET"

            if onset_index is not None:
                if int(work.loc[onset_index, "continuous_segment_id"]) != segment_id:
                    response_status = "ONSET_CROSSES_DATA_GAP"
                else:
                    onset_seconds = float(
                        (work.loc[onset_index, "timestamp"] - work.loc[action_index, "timestamp"]).total_seconds()
                    )
                    effect_end_index = min(onset_index + effect_end_steps, len(work) - 1)
                    if int(work.loc[effect_end_index, "continuous_segment_id"]) != segment_id:
                        response_status = "EFFECT_WINDOW_CROSSES_DATA_GAP"
                    else:
                        effect, anchor_level, observed_level, trend_reference = _trend_referenced_effect(
                            work,
                            action_index=action_index,
                            onset_index=onset_index,
                            value_column=value_column,
                            pre_steps=pre_steps,
                            effect_start_steps=effect_start_steps,
                            effect_end_steps=effect_end_steps,
                            tail_steps=tail_steps,
                        )
                        if np.isfinite(effect) and abs(delta_q) > 1e-12:
                            phi = effect / delta_q
                            response_status = "EFFECT_ESTIMATED"
                        else:
                            response_status = "EFFECT_NOT_ESTIMABLE"

            expected_sign = -1.0 if response_kind == "SO2" else 1.0
            physics_sign_ok = bool(np.isfinite(phi) and np.sign(phi) == np.sign(expected_sign))
            rows.append(
                {
                    "event_id": event["event_id"],
                    "action_time": event["action_time"],
                    "direction": event["direction"],
                    "condition_label": event["condition_label"],
                    "quality_grade": event["quality_grade"],
                    "event_weight": float(event["event_weight"]),
                    "delta_q_actual_m3h": delta_q,
                    "response": response_kind,
                    "response_status": response_status,
                    "onset_seconds": onset_seconds,
                    "pre_action_slope_per_min": pre_slope,
                    "onset_slope_improvement_per_min": onset_improvement,
                    "effect": effect,
                    "phi": phi,
                    "physics_sign_ok": physics_sign_ok,
                    "pre_anchor_level": anchor_level,
                    "effect_observed_level": observed_level,
                    "effect_trend_reference_level": trend_reference,
                }
            )
    return pd.DataFrame(rows)


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    if len(values) == 0:
        return float("nan")
    order = np.argsort(values)
    values = values[order]
    weights = weights[order]
    cumulative = np.cumsum(weights)
    total = cumulative[-1]
    if total <= 0:
        return float("nan")
    index = int(np.searchsorted(cumulative, q * total, side="left"))
    return float(values[min(index, len(values) - 1)])


def summarize_response_group(rows: pd.DataFrame, config: InitialTrainingConfig) -> dict:
    required = {"response_status", "phi", "event_weight", "action_time", "physics_sign_ok", "onset_seconds"}
    if rows.empty or not required.issubset(set(rows.columns)):
        return {
            "event_count": 0,
            "effective_event_weight": 0.0,
            "independent_days": 0,
            "phi_median": None,
            "phi_p25": None,
            "phi_p75": None,
            "onset_delay_p50_seconds": None,
            "onset_delay_p25_seconds": None,
            "onset_delay_p75_seconds": None,
            "physics_sign_consistency": 0.0,
            "confidence": 0.0,
        }

    usable = rows.loc[
        rows["response_status"].eq("EFFECT_ESTIMATED")
        & np.isfinite(pd.to_numeric(rows["phi"], errors="coerce"))
        & (pd.to_numeric(rows["event_weight"], errors="coerce") > 0)
    ].copy()
    if usable.empty:
        return summarize_response_group(pd.DataFrame(), config)

    phi = pd.to_numeric(usable["phi"], errors="coerce").to_numpy(dtype=float)
    weights = pd.to_numeric(usable["event_weight"], errors="coerce").to_numpy(dtype=float)
    delays = pd.to_numeric(usable["onset_seconds"], errors="coerce").to_numpy(dtype=float)
    finite_delay = np.isfinite(delays)
    effective_weight = float(weights.sum())
    independent_days = int(pd.to_datetime(usable["action_time"]).dt.normalize().nunique())
    phi_median = _weighted_quantile(phi, weights, 0.50)
    phi_p25 = _weighted_quantile(phi, weights, 0.25)
    phi_p75 = _weighted_quantile(phi, weights, 0.75)
    sign_consistency = float(np.average(usable["physics_sign_ok"].astype(float).to_numpy(), weights=weights))

    if finite_delay.any():
        delay_p25 = _weighted_quantile(delays[finite_delay], weights[finite_delay], 0.25)
        delay_p50 = _weighted_quantile(delays[finite_delay], weights[finite_delay], 0.50)
        delay_p75 = _weighted_quantile(delays[finite_delay], weights[finite_delay], 0.75)
    else:
        delay_p25 = delay_p50 = delay_p75 = float("nan")

    count_score = min(1.0, effective_weight / max(config.full_confidence_effective_events, 1e-9))
    day_score = min(1.0, independent_days / max(float(config.full_confidence_independent_days), 1.0))
    sign_score = max(0.0, min(1.0, (sign_consistency - 0.5) / 0.5))
    iqr = phi_p75 - phi_p25
    dispersion_score = max(0.0, 1.0 - iqr / max(2.0 * abs(phi_median), 1e-9))
    confidence = float(
        np.clip(0.30 * count_score + 0.20 * day_score + 0.30 * sign_score + 0.20 * dispersion_score, 0.0, 1.0)
    )

    return {
        "event_count": int(len(usable)),
        "effective_event_weight": effective_weight,
        "independent_days": independent_days,
        "phi_median": float(phi_median),
        "phi_p25": float(phi_p25),
        "phi_p75": float(phi_p75),
        "onset_delay_p50_seconds": float(delay_p50) if np.isfinite(delay_p50) else None,
        "onset_delay_p25_seconds": float(delay_p25) if np.isfinite(delay_p25) else None,
        "onset_delay_p75_seconds": float(delay_p75) if np.isfinite(delay_p75) else None,
        "physics_sign_consistency": sign_consistency,
        "confidence": confidence,
    }


def build_hierarchical_response_knowledge(response_rows: pd.DataFrame, config: InitialTrainingConfig) -> dict:
    """Build Global -> Condition shrinkage knowledge without requiring local data."""

    result: Dict[str, dict] = {"responses": {}, "conditions": {}}
    if response_rows.empty:
        for response in ("SO2", "PH"):
            result["responses"][response] = {
                direction: summarize_response_group(pd.DataFrame(), config)
                for direction in ("INCREASE", "DECREASE")
            }
        return result

    for response in ("SO2", "PH"):
        response_block: Dict[str, dict] = {}
        for direction in ("INCREASE", "DECREASE"):
            subset = response_rows.loc[
                response_rows["response"].eq(response) & response_rows["direction"].eq(direction)
            ]
            summary = summarize_response_group(subset, config)
            expected_sign_ok = (
                summary["phi_median"] is not None
                and (
                    (response == "SO2" and float(summary["phi_median"]) < 0.0)
                    or (response == "PH" and float(summary["phi_median"]) > 0.0)
                )
            )
            summary["adaptive_usable"] = bool(
                summary["effective_event_weight"] >= config.minimum_global_effective_events
                and summary["confidence"] >= config.adaptive_confidence_threshold
                and summary["physics_sign_consistency"] >= config.minimum_physics_sign_consistency
                and expected_sign_ok
            )
            response_block[direction] = summary
        result["responses"][response] = response_block

    discovered = set(response_rows["condition_label"].dropna().astype(str))
    condition_labels = list(config.known_condition_labels)
    for label in sorted(discovered):
        if label not in condition_labels and label not in {"GLOBAL_ONLY", "UNKNOWN"}:
            condition_labels.append(label)

    for label in condition_labels:
        condition_block: Dict[str, dict] = {}
        for response in ("SO2", "PH"):
            response_block = {}
            for direction in ("INCREASE", "DECREASE"):
                local_rows = response_rows.loc[
                    response_rows["condition_label"].eq(label)
                    & response_rows["response"].eq(response)
                    & response_rows["direction"].eq(direction)
                ]
                local = summarize_response_group(local_rows, config)
                global_summary = result["responses"][response][direction]
                global_phi = global_summary.get("phi_median")
                local_phi = local.get("phi_median")

                if label in {"EDGE_LOW", "EDGE_HIGH"}:
                    local_weight = 0.0
                    policy = "GLOBAL_ONLY_EDGE"
                else:
                    shrink_reference = config.shrinkage_reference_weight * (2.0 if label == "C4" else 1.0)
                    effective = float(local.get("effective_event_weight") or 0.0)
                    local_weight = effective / (effective + shrink_reference) if effective > 0.0 else 0.0
                    local_weight *= float(local.get("confidence") or 0.0)
                    policy = "SHRUNK_LOCAL" if local_weight > 0.0 else "GLOBAL_FALLBACK"

                global_adaptive_usable = bool(global_summary.get("adaptive_usable", False))
                if global_phi is None and local_phi is None:
                    effective_phi = None
                    source = "CONSERVATIVE_STEP_REQUIRED"
                elif global_phi is None:
                    effective_phi = local_phi
                    source = "LOCAL_REFERENCE_CONSERVATIVE"
                elif local_phi is None or local_weight <= 0.0:
                    effective_phi = global_phi
                    source = "GLOBAL_FALLBACK" if global_adaptive_usable else "CONSERVATIVE_STEP_WITH_GLOBAL_REFERENCE"
                else:
                    effective_phi = (1.0 - local_weight) * float(global_phi) + local_weight * float(local_phi)
                    source = policy if global_adaptive_usable else "CONSERVATIVE_STEP_WITH_SHRUNK_REFERENCE"

                response_block[direction] = {
                    "local": local,
                    "local_weight": float(local_weight),
                    "effective_phi": effective_phi,
                    "recommended_online_source": source,
                    "no_local_data_requires_hold": False,
                }
            condition_block[response] = response_block
        result["conditions"][label] = condition_block
    return result
