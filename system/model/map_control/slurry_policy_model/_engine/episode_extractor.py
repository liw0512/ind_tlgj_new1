from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

import numpy as np
import pandas as pd

from .action_detector import RawAction, detect_actions
from .config_loader import all_valves, enabled_towers
from .disturbance_classifier import classify_disturbance
from .state_builder import build_policy_state
from .time_index import IntervalOverlapIndex, TimeWindowIndexer
from .spatial_policy import analyze_condition_attribution, detect_supply_pump_state_change
from .schema import (
    BASE_CONDITION_ID_COLUMN,
    CLIP_AXIS_COLUMN,
    CONDITION_EXPERIENCE_SOURCE_COLUMN,
    CONDITION_LABEL_COLUMN,
    CONDITION_REASON_COLUMN,
    CONDITION_SNAPSHOT_VERSION_COLUMN,
    CONDITION_STATE_KEY_COLUMN,
    CONDITION_VALID_COLUMN,
    COVERAGE_STATUS_COLUMN,
    GRID_ID_COLUMN,
    OUTLET_SO2_COLUMN,
    OUT_OF_RANGE_CLIPPED_COLUMN,
    POLICY_REGION_ID_COLUMN,
    REGION_MEMBER_COUNT_COLUMN,
    REGION_STATUS_COLUMN,
    condition_axis_columns,
    time_column,
)
from .utils import (
    bool_value,
    hash_text,
    median_or_nan,
    normalize_condition_label,
    robust_slope_per_minute,
)


def _subset(indexer: TimeWindowIndexer, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return indexer.slice(start, end)


def _coverage(window: pd.DataFrame, ts_col: str, expected_minutes: float) -> float:
    if len(window) < 2 or expected_minutes <= 0:
        return 0.0
    actual = (window[ts_col].max() - window[ts_col].min()).total_seconds() / 60.0
    return min(1.0, max(0.0, actual / expected_minutes))


def _sign_changes(series: pd.Series, deadband: float) -> int:
    values = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype=float)
    if len(values) < 3:
        return 0
    diffs = np.diff(values)
    signs = np.where(diffs > deadband, 1, np.where(diffs < -deadband, -1, 0))
    signs = signs[signs != 0]
    if len(signs) < 2:
        return 0
    return int(np.sum(signs[1:] != signs[:-1]))


def _base_episode_record(
    plant: dict[str, Any],
    source_type: str,
    action_start: pd.Timestamp,
    action_end: pd.Timestamp,
    response_end: pd.Timestamp,
    action: RawAction | None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "episode_type": source_type,
        "action_start_time": action_start,
        "action_end_time": action_end,
        "response_end_time": response_end,
        "action_family": "HOLD" if action is None else action.action_family,
        "action_direction": "HOLD" if action is None else action.action_direction,
        "action_magnitude_value": 0.0 if action is None else action.action_magnitude_value,
        "active_valve_ids": "" if action is None else ",".join(action.active_valve_ids),
        "active_tower_ids": "" if action is None else ",".join(action.active_tower_ids),
    }
    for valve in all_valves(plant):
        valve_id = valve["valve_id"]
        record[f"before_valve__{valve_id}"] = (
            np.nan if action is None else action.before_values.get(valve_id, np.nan)
        )
        record[f"after_valve__{valve_id}"] = (
            np.nan if action is None else action.after_values.get(valve_id, np.nan)
        )
        record[f"delta_valve__{valve_id}"] = (
            0.0 if action is None else action.delta_values.get(valve_id, 0.0)
        )
        record[f"normalized_delta_valve__{valve_id}"] = (
            0.0 if action is None else action.normalized_delta_values.get(valve_id, 0.0)
        )
    return record


def _populate_windows(
    record: dict[str, Any],
    df: pd.DataFrame,
    baseline: pd.DataFrame,
    response: pd.DataFrame,
    full: pd.DataFrame,
    plant: dict[str, Any],
    training: dict[str, Any],
    effective_disturbance: dict[str, Any],
) -> dict[str, Any]:
    ts_col = time_column(plant)
    axes = condition_axis_columns(training)
    first_axis_col = axes[0]
    second_axis_col = axes[1] if len(axes) > 1 else None
    outlet_col = OUTLET_SO2_COLUMN

    record["before_condition_axis_1"] = median_or_nan(baseline[first_axis_col])
    record["after_condition_axis_1"] = median_or_nan(response[first_axis_col])
    if second_axis_col is None:
        record["before_condition_axis_2"] = np.nan
        record["after_condition_axis_2"] = np.nan
    else:
        record["before_condition_axis_2"] = median_or_nan(baseline[second_axis_col])
        record["after_condition_axis_2"] = median_or_nan(response[second_axis_col])

    record["before_outlet_so2"] = median_or_nan(baseline[outlet_col])
    record["after_outlet_so2"] = median_or_nan(response[outlet_col])
    record["delta_outlet_so2"] = (
        record["after_outlet_so2"] - record["before_outlet_so2"]
    )

    record["before_condition_axis_1_rate"] = robust_slope_per_minute(
        baseline[ts_col], baseline[first_axis_col]
    )
    record["episode_condition_axis_1_rate"] = robust_slope_per_minute(
        full[ts_col], full[first_axis_col]
    )
    if second_axis_col is None:
        record["before_condition_axis_2_rate"] = 0.0
        record["episode_condition_axis_2_rate"] = 0.0
    else:
        record["before_condition_axis_2_rate"] = robust_slope_per_minute(
            baseline[ts_col], baseline[second_axis_col]
        )
        record["episode_condition_axis_2_rate"] = robust_slope_per_minute(
            full[ts_col], full[second_axis_col]
        )
    record["before_outlet_so2_rate"] = robust_slope_per_minute(
        baseline[ts_col], baseline[outlet_col]
    )

    record["disturbance_mode"] = classify_disturbance(
        record["episode_condition_axis_1_rate"],
        (
            record["episode_condition_axis_2_rate"]
            if second_axis_col is not None
            else None
        ),
        effective_disturbance,
    )

    outlet_values = pd.to_numeric(response[outlet_col], errors="coerce").dropna()
    record["post_outlet_so2_median"] = (
        float(outlet_values.median()) if not outlet_values.empty else np.nan
    )
    record["post_outlet_so2_p25"] = (
        float(outlet_values.quantile(0.25)) if not outlet_values.empty else np.nan
    )
    record["post_outlet_so2_p75"] = (
        float(outlet_values.quantile(0.75)) if not outlet_values.empty else np.nan
    )
    record["post_outlet_so2_std"] = (
        float(outlet_values.std(ddof=0)) if len(outlet_values) > 1 else 0.0
    )
    record["post_outlet_so2_range"] = (
        float(outlet_values.max() - outlet_values.min())
        if not outlet_values.empty
        else np.nan
    )
    record["outlet_so2_sign_changes"] = _sign_changes(
        response[outlet_col],
        float(training["response"]["oscillation_diff_deadband"]),
    )

    safe_so2_lo, safe_so2_hi = map(float, plant["outlet_so2_safe_range"])
    record["outlet_so2_out_of_range"] = bool(
        not outlet_values.empty
        and (
            (outlet_values < safe_so2_lo).any()
            or (outlet_values > safe_so2_hi).any()
        )
    )
    record["outlet_so2_over_hard_max"] = bool(
        not outlet_values.empty and (outlet_values > safe_so2_hi).any()
    )

    for tower in enabled_towers(plant):
        tower_id = tower["tower_id"]
        ph_col = tower["ph_column"]
        before_ph = median_or_nan(baseline[ph_col])
        after_ph = median_or_nan(response[ph_col])
        values = pd.to_numeric(response[ph_col], errors="coerce").dropna()
        lo, hi = map(float, tower["ph_safe_range"])
        record[f"before_ph__{tower_id}"] = before_ph
        record[f"after_ph__{tower_id}"] = after_ph
        record[f"delta_ph__{tower_id}"] = after_ph - before_ph
        record[f"post_ph_range__{tower_id}"] = (
            float(values.max() - values.min()) if not values.empty else np.nan
        )
        record[f"post_ph_std__{tower_id}"] = (
            float(values.std(ddof=0)) if len(values) > 1 else 0.0
        )
        record[f"ph_below_limit__{tower_id}"] = bool(
            not values.empty and (values < lo).any()
        )
        record[f"ph_above_limit__{tower_id}"] = bool(
            not values.empty and (values > hi).any()
        )
        record[f"ph_out_of_range__{tower_id}"] = bool(
            record[f"ph_below_limit__{tower_id}"]
            or record[f"ph_above_limit__{tower_id}"]
        )

    if record["episode_type"] == "HOLD":
        for valve in all_valves(plant):
            valve_id = valve["valve_id"]
            col = f"__clean_valve__{valve_id}"
            before_value = median_or_nan(baseline[col])
            after_value = median_or_nan(response[col])
            record[f"before_valve__{valve_id}"] = before_value
            record[f"after_valve__{valve_id}"] = after_value
            record[f"delta_valve__{valve_id}"] = after_value - before_value
            span = float(valve["max_opening"]) - float(valve["min_opening"])
            record[f"normalized_delta_valve__{valve_id}"] = (
                (after_value - before_value) / span if span > 0 else 0.0
            )

    identity_window = full[
        full[ts_col] >= pd.Timestamp(record["action_start_time"])
    ]
    if identity_window.empty:
        identity_window = full
    attribution = analyze_condition_attribution(
        identity_window,
        str(record["episode_type"]),
        str(record["disturbance_mode"]),
        training,
    )
    anchor_row = attribution.pop("anchor_row")
    record.update(attribution)

    supply_changed, changed_columns = detect_supply_pump_state_change(
        identity_window, plant
    )
    record["supply_pump_state_changed"] = supply_changed
    record["supply_pump_changed_columns"] = ";".join(changed_columns)

    mappings = {
        CONDITION_SNAPSHOT_VERSION_COLUMN: "condition_snapshot_version",
        BASE_CONDITION_ID_COLUMN: "base_condition_id",
        POLICY_REGION_ID_COLUMN: "policy_region_id",
        REGION_STATUS_COLUMN: "region_status",
        REGION_MEMBER_COUNT_COLUMN: "region_member_count",
        COVERAGE_STATUS_COLUMN: "coverage_status",
        CONDITION_STATE_KEY_COLUMN: "condition_state_key",
        CONDITION_EXPERIENCE_SOURCE_COLUMN: "condition_experience_source",
        OUT_OF_RANGE_CLIPPED_COLUMN: "out_of_range_clipped",
        CLIP_AXIS_COLUMN: "clip_axis",
        CONDITION_REASON_COLUMN: "condition_reason",
    }
    for source_column, target_key in mappings.items():
        record[target_key] = (
            anchor_row[source_column] if source_column in full.columns else None
        )

    record["condition_valid"] = bool_value(
        anchor_row.get(CONDITION_VALID_COLUMN), False
    )
    record["continuous_segment_id"] = int(anchor_row["continuous_segment_id"])
    record["event_date"] = pd.Timestamp(
        record["action_start_time"]
    ).date().isoformat()
    record["source_files"] = ";".join(
        sorted(set(full["__source_file"].astype(str)))
    )

    state_key, state_no_grid = build_policy_state(record, plant, training)
    record["policy_state_key"] = state_key
    record["policy_state_key_no_grid"] = state_no_grid
    return record


def _validate_episode(
    record: dict[str, Any],
    baseline_coverage: float,
    response_coverage: float,
    plant: dict[str, Any],
    training: dict[str, Any],
    followup_action: bool,
) -> tuple[bool, str]:
    reasons: list[str] = []
    minimum = float(training["episode"]["minimum_window_coverage_ratio"])
    if baseline_coverage < minimum:
        reasons.append("BASELINE_WINDOW_INCOMPLETE")
    if response_coverage < minimum:
        reasons.append("RESPONSE_WINDOW_INCOMPLETE")
    if (
        training["validity"].get("require_condition_valid", True)
        and not record["condition_valid"]
    ):
        reasons.append("CONDITION_INVALID")
    if (
        not training["validity"].get("allow_out_of_range_clipped", True)
        and bool_value(record.get("out_of_range_clipped"), False)
    ):
        reasons.append("CONDITION_CLIPPED")
    if (
        followup_action
        and training["episode"].get(
            "invalidate_followup_action_in_response", True
        )
    ):
        reasons.append("FOLLOWUP_ACTION_IN_RESPONSE_WINDOW")
    if (
        bool(record.get("supply_pump_state_changed", False))
        and training["validity"].get(
            "invalidate_supply_pump_state_change", True
        )
    ):
        reasons.append("SUPPLY_PUMP_STATE_CHANGED")

    required_values = [
        record.get("before_condition_axis_1"),
        record.get("before_outlet_so2"),
        record.get("after_outlet_so2"),
    ]
    if len(condition_axis_columns(training)) > 1:
        required_values.append(record.get("before_condition_axis_2"))
    for tower in enabled_towers(plant):
        required_values.extend(
            [
                record.get(f"before_ph__{tower['tower_id']}"),
                record.get(f"after_ph__{tower['tower_id']}"),
            ]
        )
    if any(pd.isna(value) for value in required_values):
        reasons.append("CRITICAL_VALUE_MISSING")
    return not reasons, ";".join(reasons) if reasons else "OK"


def _episode_id(record: dict[str, Any]) -> str:
    key = "|".join(
        [
            str(record["episode_type"]),
            pd.Timestamp(record["action_start_time"]).isoformat(),
            pd.Timestamp(record["action_end_time"]).isoformat(),
            str(record.get("start_grid_id", "UNKNOWN")),
            str(record["action_family"]),
            *(
                f"{k}={record[k]:.6f}"
                for k in sorted(record)
                if k.startswith("delta_valve__")
            ),
        ]
    )
    return "EP_" + hash_text(key, 24)


def _build_action_records(
    df: pd.DataFrame,
    actions: list[RawAction],
    plant: dict[str, Any],
    training: dict[str, Any],
    effective_disturbance: dict[str, Any],
    progress: Callable[[float, str], None] | None = None,
) -> list[dict[str, Any]]:
    ts_col = time_column(plant)
    indexer = TimeWindowIndexer(df, ts_col)
    baseline_minutes = float(training["episode"]["baseline_minutes"])
    delay_minutes = float(training["episode"]["response_delay_minutes"])
    response_minutes = float(training["episode"]["response_window_minutes"])
    ordered_actions = sorted(actions, key=lambda item: item.start_time)
    action_start_ns = np.array(
        [pd.Timestamp(item.start_time).value for item in ordered_actions],
        dtype=np.int64,
    )
    records: list[dict[str, Any]] = []
    if progress and not ordered_actions:
        progress(1.0, "没有 ACTION 决策片段需要构建")
    for index, action in enumerate(ordered_actions):
        if progress:
            progress(
                index / max(len(ordered_actions), 1),
                f"构建 ACTION 片段 {index + 1}/{len(ordered_actions)}",
            )
        baseline_start = action.start_time - pd.Timedelta(
            minutes=baseline_minutes
        )
        response_start = action.end_time + pd.Timedelta(minutes=delay_minutes)
        response_end = response_start + pd.Timedelta(minutes=response_minutes)
        baseline = _subset(indexer, baseline_start, action.start_time)
        response = _subset(indexer, response_start, response_end)
        full = _subset(indexer, baseline_start, response_end)
        record = _base_episode_record(
            plant,
            "ACTION",
            action.start_time,
            action.end_time,
            response_end,
            action,
        )
        next_index = int(
            np.searchsorted(
                action_start_ns,
                pd.Timestamp(action.start_time).value,
                side="right",
            )
        )
        followup = bool(
            next_index < len(ordered_actions)
            and ordered_actions[next_index].start_time <= response_end
        )
        record = _populate_windows(
            record,
            df,
            baseline,
            response,
            full,
            plant,
            training,
            effective_disturbance,
        )
        baseline_cov = _coverage(baseline, ts_col, baseline_minutes)
        response_cov = _coverage(response, ts_col, response_minutes)
        record["baseline_coverage_ratio"] = baseline_cov
        record["response_coverage_ratio"] = response_cov
        record["followup_action_in_response"] = followup
        record["valid"], record["invalid_reason"] = _validate_episode(
            record,
            baseline_cov,
            response_cov,
            plant,
            training,
            followup,
        )
        if not record["valid"]:
            record["training_route"] = "INVALID"
        record["episode_id"] = _episode_id(record)
        records.append(record)
    if progress and ordered_actions:
        progress(1.0, f"ACTION 片段构建完成，共 {len(records)} 个")
    return records


def _build_hold_records(
    df: pd.DataFrame,
    actions: list[RawAction],
    plant: dict[str, Any],
    training: dict[str, Any],
    effective_disturbance: dict[str, Any],
    progress: Callable[[float, str], None] | None = None,
) -> list[dict[str, Any]]:
    ts_col = time_column(plant)
    total_minutes = float(training["episode"]["hold_episode_minutes"])
    stride_minutes = float(training["episode"]["hold_stride_minutes"])
    baseline_minutes = float(training["episode"]["baseline_minutes"])
    response_minutes = float(training["episode"]["response_window_minutes"])
    guard = float(training["episode"]["hold_action_guard_minutes"])
    max_per_segment = int(training["episode"]["max_hold_episodes_per_segment"])

    exclusion: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    for action in actions:
        left = action.start_time - pd.Timedelta(minutes=guard)
        right = (
            action.end_time
            + pd.Timedelta(
                minutes=float(training["episode"]["response_delay_minutes"])
            )
            + pd.Timedelta(minutes=response_minutes + guard)
        )
        exclusion.append((left, right))
    exclusion_index = IntervalOverlapIndex(exclusion)

    records: list[dict[str, Any]] = []
    segments = list(df.groupby("continuous_segment_id", sort=True))
    if progress and not segments:
        progress(1.0, "没有连续运行段可提取 HOLD")
    for segment_index, (segment_id, segment) in enumerate(
        segments, start=1
    ):
        if progress:
            progress(
                (segment_index - 1) / max(len(segments), 1),
                f"提取 HOLD：连续段 {segment_index}/{len(segments)}，已生成 {len(records)} 个",
            )
        if segment.empty:
            continue
        segment = (
            segment.sort_values(ts_col, kind="stable")
            if not segment[ts_col].is_monotonic_increasing
            else segment
        )
        segment_indexer = TimeWindowIndexer(segment, ts_col)
        cursor = segment[ts_col].min()
        end_limit = segment[ts_col].max()
        count = 0
        while (
            cursor + pd.Timedelta(minutes=total_minutes) <= end_limit
            and count < max_per_segment
        ):
            end = cursor + pd.Timedelta(minutes=total_minutes)
            if exclusion_index.overlaps(cursor, end):
                cursor += pd.Timedelta(minutes=stride_minutes)
                continue
            full = _subset(segment_indexer, cursor, end)
            if len(full) < 2:
                cursor += pd.Timedelta(minutes=stride_minutes)
                continue
            hold_ok = True
            for valve in all_valves(plant):
                values = pd.to_numeric(
                    full[f"__clean_valve__{valve['valve_id']}"],
                    errors="coerce",
                ).dropna()
                if (
                    values.empty
                    or float(values.max() - values.min())
                    >= float(valve["action_threshold"])
                ):
                    hold_ok = False
                    break
            if not hold_ok:
                cursor += pd.Timedelta(minutes=stride_minutes)
                continue

            baseline = _subset(
                segment_indexer,
                cursor,
                cursor + pd.Timedelta(minutes=baseline_minutes),
            )
            response = _subset(
                segment_indexer,
                end - pd.Timedelta(minutes=response_minutes),
                end,
            )
            action_start = cursor + pd.Timedelta(minutes=baseline_minutes)
            record = _base_episode_record(
                plant, "HOLD", action_start, action_start, end, None
            )
            record = _populate_windows(
                record,
                df,
                baseline,
                response,
                full,
                plant,
                training,
                effective_disturbance,
            )
            baseline_cov = _coverage(baseline, ts_col, baseline_minutes)
            response_cov = _coverage(response, ts_col, response_minutes)
            record["baseline_coverage_ratio"] = baseline_cov
            record["response_coverage_ratio"] = response_cov
            record["followup_action_in_response"] = False
            record["valid"], record["invalid_reason"] = _validate_episode(
                record,
                baseline_cov,
                response_cov,
                plant,
                training,
                False,
            )
            if not record["valid"]:
                record["training_route"] = "INVALID"
            record["episode_id"] = _episode_id(record)
            records.append(record)
            count += 1
            cursor += pd.Timedelta(minutes=stride_minutes)
    if progress and segments:
        progress(1.0, f"HOLD 片段提取完成，共 {len(records)} 个")
    return records


def _mark_short_reverse_actions(
    episodes: pd.DataFrame, training: dict[str, Any]
) -> pd.DataFrame:
    result = episodes.copy()
    result["short_reverse_action"] = False
    action = result[result["episode_type"] == "ACTION"].sort_values(
        "action_start_time"
    )
    limit = pd.Timedelta(
        minutes=float(training["episode"]["short_reverse_action_minutes"])
    )
    indices = list(action.index)
    for left_idx, right_idx in zip(indices[:-1], indices[1:]):
        left = result.loc[left_idx]
        right = result.loc[right_idx]
        gap = pd.Timestamp(right["action_start_time"]) - pd.Timestamp(
            left["action_end_time"]
        )
        opposite = {
            ("INCREASE", "DECREASE"),
            ("DECREASE", "INCREASE"),
        }
        if (
            gap <= limit
            and (left["action_direction"], right["action_direction"])
            in opposite
        ):
            result.loc[left_idx, "short_reverse_action"] = True
    return result


def extract_decision_episodes(
    df: pd.DataFrame,
    plant: dict[str, Any],
    training: dict[str, Any],
    effective_disturbance: dict[str, Any],
    progress: Callable[[float, str], None] | None = None,
) -> tuple[pd.DataFrame, list[RawAction]]:
    def emit(start: float, end: float):
        if not progress:
            return None
        return lambda value, message: progress(
            start + (end - start) * value, message
        )

    actions = detect_actions(df, plant, training, emit(0.00, 0.55))
    records = _build_action_records(
        df,
        actions,
        plant,
        training,
        effective_disturbance,
        emit(0.55, 0.75),
    )
    records.extend(
        _build_hold_records(
            df,
            actions,
            plant,
            training,
            effective_disturbance,
            emit(0.75, 0.96),
        )
    )
    if not records:
        if progress:
            progress(1.0, "未生成 ACTION 或 HOLD 决策片段")
        return pd.DataFrame(), actions
    episodes = pd.DataFrame(records)
    episodes["action_start_time"] = pd.to_datetime(
        episodes["action_start_time"]
    )
    episodes["action_end_time"] = pd.to_datetime(
        episodes["action_end_time"]
    )
    episodes["response_end_time"] = pd.to_datetime(
        episodes["response_end_time"]
    )
    episodes.sort_values(
        ["action_start_time", "episode_type"], inplace=True
    )
    episodes.drop_duplicates(
        subset=["episode_id"], keep="last", inplace=True
    )
    episodes.reset_index(drop=True, inplace=True)
    episodes = _mark_short_reverse_actions(episodes, training)
    if progress:
        action_count = int((episodes["episode_type"] == "ACTION").sum())
        hold_count = int((episodes["episode_type"] == "HOLD").sum())
        progress(
            1.0,
            f"决策片段完成：ACTION={action_count}，HOLD={hold_count}",
        )
    return episodes, actions
