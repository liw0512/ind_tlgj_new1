# -*- coding: utf-8 -*-
"""Historical timing diagnostic for condition transitions around slurry actions.

This module is diagnostic-only.  It does not change HistoricalEpisodeEngine
eligibility, MFAC priors, online LEARN permission, residual control, or DCS write.

It consumes the canonical MAJORITY replay produced by
``historical_condition_stability_replay_diagnostic`` and profiles the subset of
learnable-shape events that were rejected for a process-state change and still
contain a formal MAJORITY condition transition.  The goal is to measure *when*
that transition happens relative to the actual slurry-flow action and how the
configured condition axis behaves before/during/after the action.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG
from system.model.config.standard_fields import TIME_COLUMN


DEFAULT_PRE_WINDOW_MINUTES = 10.0
DEFAULT_POST_WINDOW_MINUTES = 10.0
LOCAL_START_MEDIAN_MINUTES = 3.0


def _read_csv(path: str | Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _condition_axis_column() -> str:
    axes = PLANT_CONFIG.get("condition_axes") or []
    if len(axes) != 1:
        raise ValueError(
            "historical condition/action timing diagnostic currently requires "
            "exactly one configured condition axis"
        )
    column = str((axes[0] or {}).get("column") or "").strip()
    if not column:
        raise ValueError("configured condition axis column is empty")
    return column


def _numeric_window_metrics(
    frame: pd.DataFrame,
    timestamp_column: str,
    value_column: str,
) -> dict[str, Any]:
    if frame.empty or value_column not in frame.columns:
        return {
            "count": 0,
            "start": None,
            "end": None,
            "delta": None,
            "minimum": None,
            "maximum": None,
            "range": None,
            "mean": None,
            "median": None,
        }
    work = pd.DataFrame(
        {
            "time": pd.to_datetime(frame[timestamp_column], errors="coerce"),
            "value": pd.to_numeric(frame[value_column], errors="coerce"),
        }
    ).dropna()
    if work.empty:
        return {
            "count": 0,
            "start": None,
            "end": None,
            "delta": None,
            "minimum": None,
            "maximum": None,
            "range": None,
            "mean": None,
            "median": None,
        }
    work.sort_values("time", inplace=True, kind="stable")
    values = work["value"].to_numpy(dtype=float)
    start = float(values[0])
    end = float(values[-1])
    minimum = float(np.min(values))
    maximum = float(np.max(values))
    return {
        "count": int(len(values)),
        "start": start,
        "end": end,
        "delta": end - start,
        "minimum": minimum,
        "maximum": maximum,
        "range": maximum - minimum,
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
    }


def _last_value_at_or_before(
    frame: pd.DataFrame,
    timestamp_column: str,
    value_column: str,
    timestamp: pd.Timestamp,
) -> str:
    if frame.empty or value_column not in frame.columns:
        return ""
    values = frame.loc[frame[timestamp_column] <= timestamp, [timestamp_column, value_column]].dropna()
    if values.empty:
        return ""
    return _text(values.sort_values(timestamp_column, kind="stable").iloc[-1][value_column])


def _switch_times(
    replay: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    pre_minutes: float,
    post_minutes: float,
) -> tuple[list[pd.Timestamp], list[pd.Timestamp], list[pd.Timestamp]]:
    pre_start = start - pd.Timedelta(minutes=float(pre_minutes))
    post_end = end + pd.Timedelta(minutes=float(post_minutes))
    mask = replay["replay_condition_switch_state"].astype(str).eq("SWITCHED")
    switched = replay.loc[mask, TIME_COLUMN]
    pre = list(switched.loc[(switched >= pre_start) & (switched < start)])
    action = list(switched.loc[(switched >= start) & (switched <= end)])
    post = list(switched.loc[(switched > end) & (switched <= post_end)])
    return pre, action, post


def _switch_pattern(pre_count: int, action_count: int, post_count: int) -> str:
    parts: list[str] = []
    if pre_count:
        parts.append("PRE")
    if action_count:
        parts.append("ACTION")
    if post_count:
        parts.append("POST")
    return "+".join(parts) if parts else "NONE"


def _first_action_switch_bucket(offset_minutes: float | None) -> str:
    if offset_minutes is None:
        return "NO_ACTION_SWITCH"
    value = float(offset_minutes)
    if value <= 1.0:
        return "0_1_MIN"
    if value <= 3.0:
        return "1_3_MIN"
    if value <= 5.0:
        return "3_5_MIN"
    return "GT_5_MIN"


def select_target_events(event_detail: pd.DataFrame) -> pd.DataFrame:
    """Return the canonical 546-style target cohort without hard-coding a count."""
    if event_detail.empty:
        return event_detail.copy()
    required = {
        "learnable_flow_shape",
        "original_process_state_changed",
        "majority_condition_changed",
    }
    missing = sorted(required - set(event_detail.columns))
    if missing:
        raise KeyError("event detail is missing required columns: " + ", ".join(missing))
    mask = (
        event_detail["learnable_flow_shape"].fillna(False).astype(bool)
        & event_detail["original_process_state_changed"].fillna(False).astype(bool)
        & event_detail["majority_condition_changed"].fillna(False).astype(bool)
    )
    return event_detail.loc[mask].copy()


def diagnose_condition_action_timing(
    history: pd.DataFrame,
    replay: pd.DataFrame,
    event_detail: pd.DataFrame,
    *,
    pre_minutes: float = DEFAULT_PRE_WINDOW_MINUTES,
    post_minutes: float = DEFAULT_POST_WINDOW_MINUTES,
) -> pd.DataFrame:
    """Profile formal condition-switch timing and condition-axis motion per event."""
    if pre_minutes <= 0 or post_minutes <= 0:
        raise ValueError("pre_minutes and post_minutes must be > 0")
    axis_column = _condition_axis_column()
    if TIME_COLUMN not in history.columns:
        raise KeyError(f"history is missing timestamp column {TIME_COLUMN!r}")
    if axis_column not in history.columns:
        raise KeyError(f"history is missing condition axis column {axis_column!r}")
    required_replay = {
        TIME_COLUMN,
        "replay_stable_condition_label",
        "replay_condition_switch_state",
    }
    missing_replay = sorted(required_replay - set(replay.columns))
    if missing_replay:
        raise KeyError("replay is missing required columns: " + ", ".join(missing_replay))

    hist = history[[TIME_COLUMN, axis_column]].copy()
    hist[TIME_COLUMN] = pd.to_datetime(hist[TIME_COLUMN], errors="coerce")
    hist = hist.dropna(subset=[TIME_COLUMN]).sort_values(TIME_COLUMN, kind="stable").reset_index(drop=True)

    rep = replay[list(required_replay)].copy()
    rep[TIME_COLUMN] = pd.to_datetime(rep[TIME_COLUMN], errors="coerce")
    rep = rep.dropna(subset=[TIME_COLUMN]).sort_values(TIME_COLUMN, kind="stable").reset_index(drop=True)

    target = select_target_events(event_detail)
    records: list[dict[str, Any]] = []
    for _, event in target.iterrows():
        start = pd.to_datetime(event.get("action_start_time"), errors="coerce")
        end = pd.to_datetime(event.get("action_end_time"), errors="coerce")
        if pd.isna(start) or pd.isna(end) or end < start:
            continue

        pre_start = start - pd.Timedelta(minutes=float(pre_minutes))
        post_end = end + pd.Timedelta(minutes=float(post_minutes))
        pre_hist = hist.loc[(hist[TIME_COLUMN] >= pre_start) & (hist[TIME_COLUMN] < start)]
        action_hist = hist.loc[(hist[TIME_COLUMN] >= start) & (hist[TIME_COLUMN] <= end)]
        post_hist = hist.loc[(hist[TIME_COLUMN] > end) & (hist[TIME_COLUMN] <= post_end)]

        pre_metric = _numeric_window_metrics(pre_hist, TIME_COLUMN, axis_column)
        action_metric = _numeric_window_metrics(action_hist, TIME_COLUMN, axis_column)
        post_metric = _numeric_window_metrics(post_hist, TIME_COLUMN, axis_column)

        local_pre = hist.loc[
            (hist[TIME_COLUMN] >= start - pd.Timedelta(minutes=LOCAL_START_MEDIAN_MINUTES))
            & (hist[TIME_COLUMN] < start)
        ]
        local_post = hist.loc[
            (hist[TIME_COLUMN] >= start)
            & (hist[TIME_COLUMN] <= start + pd.Timedelta(minutes=LOCAL_START_MEDIAN_MINUTES))
        ]
        local_pre_metric = _numeric_window_metrics(local_pre, TIME_COLUMN, axis_column)
        local_post_metric = _numeric_window_metrics(local_post, TIME_COLUMN, axis_column)
        pre_median = _finite(local_pre_metric.get("median"))
        post_median = _finite(local_post_metric.get("median"))
        start_median_shift = (
            post_median - pre_median
            if pre_median is not None and post_median is not None
            else None
        )

        pre_switch, action_switch, post_switch = _switch_times(
            rep, start, end, pre_minutes, post_minutes
        )
        first_action_offset = (
            (action_switch[0] - start).total_seconds() / 60.0
            if action_switch else None
        )
        last_action_offset = (
            (action_switch[-1] - start).total_seconds() / 60.0
            if action_switch else None
        )
        last_pre_offset = (
            (pre_switch[-1] - start).total_seconds() / 60.0
            if pre_switch else None
        )
        first_post_offset = (
            (post_switch[0] - end).total_seconds() / 60.0
            if post_switch else None
        )

        record: dict[str, Any] = {
            "episode_id": event.get("episode_id"),
            "flow_shape": _text(event.get("flow_shape")),
            "action_start_time": start,
            "action_end_time": end,
            "action_duration_min": (end - start).total_seconds() / 60.0,
            "condition_axis_column": axis_column,
            "stable_condition_at_action_start": _last_value_at_or_before(
                rep, TIME_COLUMN, "replay_stable_condition_label", start
            ),
            "stable_condition_at_action_end": _last_value_at_or_before(
                rep, TIME_COLUMN, "replay_stable_condition_label", end
            ),
            "pre_formal_switch_count": len(pre_switch),
            "action_formal_switch_count": len(action_switch),
            "post_formal_switch_count": len(post_switch),
            "switch_pattern": _switch_pattern(
                len(pre_switch), len(action_switch), len(post_switch)
            ),
            "last_pre_switch_offset_min_from_action_start": last_pre_offset,
            "first_action_switch_offset_min": first_action_offset,
            "last_action_switch_offset_min": last_action_offset,
            "first_post_switch_offset_min_from_action_end": first_post_offset,
            "first_action_switch_bucket": _first_action_switch_bucket(first_action_offset),
            "start_local_pre_median": pre_median,
            "start_local_post_median": post_median,
            "start_local_median_shift": start_median_shift,
        }
        for prefix, metric in (
            ("pre", pre_metric),
            ("action", action_metric),
            ("post", post_metric),
        ):
            for key, value in metric.items():
                record[f"{prefix}_axis_{key}"] = value
        records.append(record)
    return pd.DataFrame(records)


def _quantiles(frame: pd.DataFrame, column: str) -> dict[str, float | None]:
    if column not in frame.columns or frame.empty:
        return {"p50": None, "p75": None, "p90": None, "p95": None, "max": None}
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return {"p50": None, "p75": None, "p90": None, "p95": None, "max": None}
    return {
        "p50": float(values.quantile(0.50)),
        "p75": float(values.quantile(0.75)),
        "p90": float(values.quantile(0.90)),
        "p95": float(values.quantile(0.95)),
        "max": float(values.max()),
    }


def build_timing_summary(detail: pd.DataFrame) -> dict[str, Any]:
    def counts(column: str) -> dict[str, int]:
        if column not in detail.columns or detail.empty:
            return {}
        values = detail[column].fillna("<NA>").astype(str).value_counts()
        return {str(key): int(value) for key, value in values.items()}

    return {
        "target_event_count": int(len(detail)),
        "switch_pattern_counts": counts("switch_pattern"),
        "first_action_switch_bucket_counts": counts("first_action_switch_bucket"),
        "action_duration_min_quantiles": _quantiles(detail, "action_duration_min"),
        "first_action_switch_offset_min_quantiles": _quantiles(
            detail, "first_action_switch_offset_min"
        ),
        "action_axis_range_quantiles": _quantiles(detail, "action_axis_range"),
        "action_axis_abs_delta_quantiles": _quantiles(
            detail.assign(
                __abs_delta=pd.to_numeric(
                    detail.get("action_axis_delta"), errors="coerce"
                ).abs()
            ),
            "__abs_delta",
        ) if not detail.empty else _quantiles(detail, "action_axis_delta"),
        "start_local_abs_median_shift_quantiles": _quantiles(
            detail.assign(
                __abs_shift=pd.to_numeric(
                    detail.get("start_local_median_shift"), errors="coerce"
                ).abs()
            ),
            "__abs_shift",
        ) if not detail.empty else _quantiles(detail, "start_local_median_shift"),
        "pre_axis_range_quantiles": _quantiles(detail, "pre_axis_range"),
        "post_axis_range_quantiles": _quantiles(detail, "post_axis_range"),
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(dict(value), stream, ensure_ascii=False, indent=2, allow_nan=False)


def run_diagnostic(
    *,
    history_csv: str,
    replay_csv: str,
    event_detail_csv: str,
    output_dir: str,
    pre_minutes: float = DEFAULT_PRE_WINDOW_MINUTES,
    post_minutes: float = DEFAULT_POST_WINDOW_MINUTES,
) -> dict[str, Any]:
    history = _read_csv(history_csv)
    replay = _read_csv(replay_csv)
    event_detail = _read_csv(event_detail_csv)
    detail = diagnose_condition_action_timing(
        history,
        replay,
        event_detail,
        pre_minutes=pre_minutes,
        post_minutes=post_minutes,
    )
    summary = build_timing_summary(detail)
    summary.update(
        {
            "condition_axis_column": _condition_axis_column(),
            "pre_window_minutes": float(pre_minutes),
            "post_window_minutes": float(post_minutes),
            "local_start_median_minutes": float(LOCAL_START_MEDIAN_MINUTES),
        }
    )

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    detail_path = root / "historical_condition_action_timing_detail.csv"
    summary_path = root / "historical_condition_action_timing_summary.json"
    detail.to_csv(detail_path, index=False, encoding="utf-8-sig")
    _write_json(summary_path, summary)

    result = dict(summary)
    result["detail_csv"] = str(detail_path)
    result["summary_json"] = str(summary_path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Profile canonical stable-condition switch timing and condition-axis "
            "motion around historical slurry-flow actions."
        )
    )
    parser.add_argument("--history", required=True)
    parser.add_argument("--replay", required=True)
    parser.add_argument("--event-detail", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--pre-minutes", type=float, default=DEFAULT_PRE_WINDOW_MINUTES)
    parser.add_argument("--post-minutes", type=float, default=DEFAULT_POST_WINDOW_MINUTES)
    args = parser.parse_args(argv)

    result = run_diagnostic(
        history_csv=args.history,
        replay_csv=args.replay,
        event_detail_csv=args.event_detail,
        output_dir=args.output_dir,
        pre_minutes=args.pre_minutes,
        post_minutes=args.post_minutes,
    )
    print("========== CONDITION / SLURRY ACTION TIMING ==========")
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
