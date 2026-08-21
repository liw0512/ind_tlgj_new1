from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

import pandas as pd

from .schema import (
    BASE_CONDITION_ID_COLUMN,
    CONDITION_LABEL_COLUMN,
    CONDITION_SNAPSHOT_VERSION_COLUMN,
    CONDITION_STATE_KEY_COLUMN,
    CONDITION_VALID_COLUMN,
    GRID_ID_COLUMN,
    OUT_OF_RANGE_CLIPPED_COLUMN,
    POLICY_REGION_ID_COLUMN,
    REGION_MEMBER_COUNT_COLUMN,
    REGION_STATUS_COLUMN,
    time_column,
)
from .supply_flow_context_classifier import classify_supply_flow_context
from .supply_flow_effect_profiler import profile_supply_flow_effect
from .supply_flow_event_classifier import classify_supply_flow_event
from .supply_flow_event_detector import detect_supply_flow_events
from .utils import bool_value, hash_text, normalize_condition_label


def _circulation_thresholds(plant: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for tower in plant.get("towers", []) or []:
        if not tower.get("enabled", True):
            continue
        for pump in tower.get("circulation_pumps", []) or []:
            column = str(
                pump.get("value_column") or pump.get("current_column") or ""
            ).strip()
            threshold = pump.get("run_threshold", pump.get("run_current_threshold"))
            if column and threshold is not None:
                result[column] = float(threshold)
    return result


def _flow_event_anchor_fields(
    frame: pd.DataFrame,
    timestamp_column: str,
    event_start: pd.Timestamp,
) -> dict[str, Any]:
    timestamps = pd.to_datetime(frame[timestamp_column], errors="coerce")
    matches = frame.loc[timestamps == pd.Timestamp(event_start)]
    if matches.empty:
        matches = frame.loc[timestamps <= pd.Timestamp(event_start)].tail(1)
    if matches.empty:
        return {}
    anchor = matches.iloc[-1]
    label = normalize_condition_label(anchor.get(CONDITION_LABEL_COLUMN))
    grid_id = str(anchor.get(GRID_ID_COLUMN, "UNKNOWN"))
    result: dict[str, Any] = {
        "condition_label": label,
        "anchor_condition_label": label,
        "start_condition_label": label,
        "end_condition_label": label,
        "condition_label_path": label,
        "condition_label_change_count": 0,
        "anchor_grid_id": grid_id,
        "start_grid_id": grid_id,
        "end_grid_id": grid_id,
        "grid_transition_path": grid_id,
        "grid_change_count": 0,
        "condition_snapshot_version": anchor.get(
            CONDITION_SNAPSHOT_VERSION_COLUMN
        ),
        "policy_region_id": anchor.get(POLICY_REGION_ID_COLUMN),
        "region_status": anchor.get(REGION_STATUS_COLUMN),
        "region_member_count": anchor.get(REGION_MEMBER_COUNT_COLUMN),
        "base_condition_id": anchor.get(BASE_CONDITION_ID_COLUMN),
        "condition_state_key": anchor.get(CONDITION_STATE_KEY_COLUMN),
        "condition_valid": bool_value(
            anchor.get(CONDITION_VALID_COLUMN), False
        ),
        "out_of_range_clipped": bool_value(
            anchor.get(OUT_OF_RANGE_CLIPPED_COLUMN), False
        ),
        "continuous_segment_id": anchor.get("continuous_segment_id"),
        "event_date": pd.Timestamp(event_start).date().isoformat(),
    }
    if "__source_file" in frame.columns:
        result["source_files"] = str(anchor.get("__source_file", ""))
    return result


def extract_supply_flow_episode_candidates(
    df: pd.DataFrame,
    plant: dict[str, Any],
    training: dict[str, Any],
    progress: Callable[[float, str], None] | None = None,
) -> pd.DataFrame:
    """Extract learnable STEP/PULSE/BOOST_STEP episodes from actual flow."""
    events = detect_supply_flow_events(df, plant, training, progress=progress)
    if not events:
        return pd.DataFrame()

    ts_col = time_column(plant)
    circulation_thresholds = _circulation_thresholds(plant)
    transition_columns = tuple(
        column
        for column in (
            CONDITION_LABEL_COLUMN,
            CONDITION_STATE_KEY_COLUMN,
            "fast_change_mode",
            "fast_change_exact_trend_mode",
        )
        if column in df.columns
    )
    records: list[dict[str, Any]] = []
    for event in events:
        shape = classify_supply_flow_event(event)
        context = classify_supply_flow_context(
            event,
            shape,
            df,
            timestamp_column=ts_col,
            circulation_columns=tuple(circulation_thresholds),
            circulation_thresholds=circulation_thresholds,
            process_transition_columns=transition_columns,
        )
        effect, timing = profile_supply_flow_effect(event, df, plant, training)
        anchor = _flow_event_anchor_fields(df, ts_col, pd.Timestamp(event.start_time))
        condition_valid = bool(anchor.get("condition_valid", False))
        clipped = bool(anchor.get("out_of_range_clipped", False))
        allow_clipped = bool(
            training.get("validity", {}).get("allow_out_of_range_clipped", True)
        )
        learnable_shape = str(shape.shape) in {"STEP", "PULSE", "BOOST_STEP"}
        valid = bool(
            learnable_shape
            and context.learning_eligible
            and effect.complete
            and condition_valid
            and (allow_clipped or not clipped)
        )
        invalid_reasons: list[str] = []
        if not learnable_shape:
            invalid_reasons.append("FLOW_SHAPE_NOT_LEARNABLE")
        if not context.learning_eligible:
            invalid_reasons.append(f"FLOW_CONTEXT_NOT_CLEAN:{context.reason}")
        if not effect.complete:
            invalid_reasons.append(
                f"FLOW_EFFECT_PROFILE_INCOMPLETE:{effect.reason}"
            )
        if not condition_valid:
            invalid_reasons.append("CONDITION_INVALID")
        if clipped and not allow_clipped:
            invalid_reasons.append("OUT_OF_RANGE_CLIPPED")

        identity = "|".join(
            (
                str(event.tower_id),
                pd.Timestamp(event.start_time).isoformat(),
                pd.Timestamp(event.end_time).isoformat(),
                str(shape.shape),
                f"{float(event.final_delta_flow):.6f}",
            )
        )
        record: dict[str, Any] = {
            "episode_id": "FLOW_EP_" + hash_text(identity, 24),
            "episode_type": "FLOW_ACTION",
            "action_semantics": "ACTUAL_SUPPLY_FLOW_V1",
            "action_start_time": pd.Timestamp(event.start_time),
            "action_end_time": pd.Timestamp(event.end_time),
            "response_end_time": pd.Timestamp(effect.response_end_time),
            "action_family": f"TOWER:{event.tower_id}|SUPPLY_FLOW",
            "action_direction": str(shape.direction),
            "action_magnitude_value": float(event.max_abs_delta_flow),
            "active_tower_ids": str(event.tower_id),
            "valid": valid,
            "invalid_reason": "|".join(invalid_reasons),
            "training_route": "FLOW_POLICY" if valid else "FLOW_REJECTED",
            "followup_action_in_response": False,
            "short_reverse_action": False,
            "before_outlet_so2": effect.baseline_outlet_so2,
            "after_outlet_so2": effect.response_outlet_so2,
            "delta_outlet_so2": effect.delta_outlet_so2,
            "post_outlet_so2_range": effect.response_outlet_so2_range,
            "post_outlet_so2_safe_ratio": effect.outlet_so2_safe_ratio,
            "outlet_so2_sign_changes": effect.oscillation_sign_changes,
            "outlet_so2_over_hard_max": effect.outlet_so2_over_hard_max,
            "baseline_coverage_ratio": effect.baseline_coverage_ratio,
            "response_coverage_ratio": effect.response_coverage_ratio,
            "flow_context": context.context,
            "flow_learning_eligible": context.learning_eligible,
            "flow_circulation_change": context.circulation_change,
            "flow_major_process_transition": context.major_process_transition,
            "flow_context_reason": context.reason,
            "flow_effect_baseline_start_time": effect.baseline_start_time,
            "flow_effect_response_start_time": effect.response_start_time,
            "flow_effect_outlet_so2_direction": effect.outlet_so2_direction,
            "flow_effect_response_outlet_so2_min": effect.response_outlet_so2_min,
            "flow_effect_response_outlet_so2_max": effect.response_outlet_so2_max,
            "flow_effect_tower_ph_direction": effect.tower_ph_direction,
            "flow_effect_response_tower_ph_min": effect.response_tower_ph_min,
            "flow_effect_response_tower_ph_max": effect.response_tower_ph_max,
            "flow_effect_complete": effect.complete,
            "flow_effect_reason": effect.reason,
        }
        record.update(anchor)
        record.update({f"flow_event_{key}": value for key, value in asdict(event).items()})
        record.update(
            {
                f"flow_{key.removeprefix('flow_')}": value
                for key, value in asdict(shape).items()
            }
        )
        record.update({f"flow_timing_{key}": value for key, value in asdict(timing).items()})
        record[f"before_ph__{event.tower_id}"] = effect.baseline_tower_ph
        record[f"after_ph__{event.tower_id}"] = effect.response_tower_ph
        record[f"delta_ph__{event.tower_id}"] = effect.delta_tower_ph
        record[f"ph_out_of_range__{event.tower_id}"] = effect.tower_ph_out_of_range
        records.append(record)
    return pd.DataFrame(records)


def extract_decision_episodes(
    df: pd.DataFrame,
    plant: dict[str, Any],
    training: dict[str, Any],
    progress: Callable[[float, str], None] | None = None,
) -> tuple[pd.DataFrame, list[Any]]:
    episodes = extract_supply_flow_episode_candidates(df, plant, training, progress)
    if episodes.empty:
        if progress:
            progress(1.0, "未生成供浆流量动作片段")
        return episodes, []
    for column in ("action_start_time", "action_end_time", "response_end_time"):
        episodes[column] = pd.to_datetime(episodes[column])
    episodes.sort_values(["action_start_time", "episode_type"], inplace=True)
    episodes.drop_duplicates(subset=["episode_id"], keep="last", inplace=True)
    episodes.reset_index(drop=True, inplace=True)
    if progress:
        progress(1.0, f"供浆流量动作片段完成：FLOW_ACTION={len(episodes)}")
    return episodes, []
