# -*- coding: utf-8 -*-
"""Causal historical replay of canonical first-module MAJORITY semantics.

This diagnostic does not change MFAC eligibility.  It replays the exact
``OnlineConditionClassifier`` row by row over historical data and compares the
instantaneous region label with the formal MAJORITY-stabilized condition label
inside each historical supply-flow event.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from system.model.config.standard_fields import TIME_COLUMN
from system.model.map_control.condition_model.condition_config import from_dict
from system.model.map_control.condition_model.online_condition_classifier import (
    OnlineConditionClassifier,
)
from system.model.map_control.condition_model.snapshot_io import read_snapshot


LEARNABLE_FLOW_SHAPES = frozenset({"STEP", "PULSE", "BOOST_STEP"})
PROCESS_STATE_CHANGED_REASON = "PROCESS_STATE_CHANGED_DURING_EVENT"

_REPLAY_INPUT_STATE_FIELDS = (
    "xst_circulation_pump_count",
    "apt_circulation_pump_count",
    "xst_circulation_pump_status",
    "apt_circulation_pump_status",
    "xst_pump_status",
    "apt_pump_status",
    "reaction_unit_mode",
    "slurry_supply_capacity_state",
)


def _read_csv(path: str | Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value)


def _label_stats(values) -> tuple[int, int, bool]:
    labels = [_text(value) for value in values]
    labels = [value for value in labels if value]
    if not labels:
        return 0, 0, False
    unique_count = len(set(labels))
    switch_count = sum(
        1
        for previous, current in zip(labels, labels[1:])
        if previous != current
    )
    return unique_count, switch_count, unique_count > 1


def replay_canonical_condition_history(
    history: pd.DataFrame,
    snapshot,
) -> pd.DataFrame:
    """Replay the exact canonical online classifier chronologically."""
    if TIME_COLUMN not in history.columns:
        raise KeyError(f"history is missing timestamp column {TIME_COLUMN!r}")

    work = history.copy()
    work["__replay_input_order"] = np.arange(len(work), dtype=np.int64)
    work[TIME_COLUMN] = pd.to_datetime(work[TIME_COLUMN], errors="coerce")
    if work[TIME_COLUMN].isna().any():
        bad = int(work[TIME_COLUMN].isna().sum())
        raise ValueError(f"history contains {bad} invalid timestamps")
    work = work.sort_values(
        [TIME_COLUMN, "__replay_input_order"],
        kind="stable",
    ).reset_index(drop=True)

    config = from_dict(snapshot.grid_config)
    classifier = OnlineConditionClassifier(config, snapshot)

    missing_axes = [
        column
        for column in config.condition_axis_columns
        if column not in work.columns
    ]
    if missing_axes:
        raise KeyError(
            "history is missing condition-axis columns: "
            + ", ".join(missing_axes)
        )

    classifier_columns = list(config.condition_axis_columns)
    classifier_columns.extend(
        column
        for column in _REPLAY_INPUT_STATE_FIELDS
        if column in work.columns and column not in classifier_columns
    )
    source_columns = [
        column
        for column in ("grid_id", "condition_label")
        if column in work.columns
    ]
    selected_columns = list(
        dict.fromkeys([TIME_COLUMN, *classifier_columns, *source_columns])
    )

    records: list[dict[str, Any]] = []
    for values in work[selected_columns].itertuples(index=False, name=None):
        row = dict(zip(selected_columns, values))
        realtime = {
            column: row.get(column)
            for column in classifier_columns
        }
        result = classifier.classify(realtime)
        source_grid = _text(row.get("grid_id"))
        source_label = _text(row.get("condition_label"))
        replay_raw_grid = _text(result.raw_grid_id)
        replay_raw_label = _text(result.raw_condition_label)

        records.append(
            {
                TIME_COLUMN: row[TIME_COLUMN],
                "source_grid_id": source_grid,
                "source_condition_label": source_label,
                "replay_raw_grid_id": replay_raw_grid,
                "replay_raw_condition_label": replay_raw_label,
                "replay_stable_grid_id": _text(result.stable_grid_id),
                "replay_stable_condition_label": _text(
                    result.stable_condition_label
                ),
                "replay_condition_label": _text(result.condition_label),
                "replay_condition_valid": bool(result.condition_valid),
                "replay_condition_stable": bool(result.condition_stable),
                "replay_condition_switch_state": str(
                    result.condition_switch_state
                ),
                "replay_stability_sample_count": int(
                    result.stability_sample_count
                ),
                "replay_majority_count": int(result.majority_count),
                "replay_majority_tied": bool(result.majority_tied),
                "source_grid_matches_replay_raw": (
                    None if not source_grid else source_grid == replay_raw_grid
                ),
                "source_condition_matches_replay_raw": (
                    None
                    if not source_label
                    else source_label == replay_raw_label
                ),
            }
        )
    return pd.DataFrame(records)


def diagnose_episode_condition_transitions(
    replay: pd.DataFrame,
    episodes: pd.DataFrame,
) -> pd.DataFrame:
    """Compare raw-region and MAJORITY condition transitions per event."""
    required_replay = {
        TIME_COLUMN,
        "replay_raw_condition_label",
        "replay_stable_condition_label",
        "replay_condition_stable",
        "replay_condition_switch_state",
    }
    missing_replay = sorted(required_replay - set(replay.columns))
    if missing_replay:
        raise KeyError(
            "replay is missing required columns: "
            + ", ".join(missing_replay)
        )
    if episodes.empty:
        return pd.DataFrame()
    for column in ("action_start_time", "action_end_time"):
        if column not in episodes.columns:
            raise KeyError(f"episodes is missing {column!r}")

    ordered = replay.copy()
    ordered[TIME_COLUMN] = pd.to_datetime(
        ordered[TIME_COLUMN], errors="coerce"
    )
    ordered = ordered.dropna(subset=[TIME_COLUMN]).sort_values(
        TIME_COLUMN, kind="stable"
    ).reset_index(drop=True)

    times = ordered[TIME_COLUMN].to_numpy(dtype="datetime64[ns]")
    raw = ordered["replay_raw_condition_label"].to_numpy(dtype=object)
    stable = ordered["replay_stable_condition_label"].to_numpy(dtype=object)
    ready = ordered["replay_condition_stable"].fillna(False).to_numpy(
        dtype=bool
    )
    switch_state = ordered[
        "replay_condition_switch_state"
    ].to_numpy(dtype=object)

    records: list[dict[str, Any]] = []
    for _, episode in episodes.iterrows():
        start = pd.to_datetime(
            episode.get("action_start_time"), errors="coerce"
        )
        end = pd.to_datetime(
            episode.get("action_end_time"), errors="coerce"
        )
        if pd.isna(start) or pd.isna(end):
            left = right = 0
        else:
            left = int(
                np.searchsorted(times, np.datetime64(start), side="left")
            )
            right = int(
                np.searchsorted(times, np.datetime64(end), side="right")
            )

        raw_unique, raw_switches, raw_changed = _label_stats(raw[left:right])
        stable_unique, stable_switches, stable_changed = _label_stats(
            stable[left:right]
        )
        ready_unique, ready_switches, ready_changed = _label_stats(
            stable[left:right][ready[left:right]]
        )
        formal_switch_count = sum(
            1
            for value in switch_state[left:right]
            if str(value) == "SWITCHED"
        )

        flow_shape = _text(episode.get("flow_shape"))
        context_reason = _text(episode.get("flow_context_reason"))
        learnable_shape = flow_shape in LEARNABLE_FLOW_SHAPES
        original_process_state_changed = (
            context_reason == PROCESS_STATE_CHANGED_REASON
        )

        records.append(
            {
                "episode_id": episode.get("episode_id"),
                "action_start_time": start,
                "action_end_time": end,
                "flow_shape": flow_shape,
                "original_valid": bool(episode.get("valid", False)),
                "original_flow_context_reason": context_reason,
                "learnable_flow_shape": learnable_shape,
                "original_process_state_changed": (
                    original_process_state_changed
                ),
                "replay_row_count": max(0, right - left),
                "raw_condition_unique_count": raw_unique,
                "raw_condition_switch_count": raw_switches,
                "raw_condition_changed": raw_changed,
                "majority_condition_unique_count": stable_unique,
                "majority_condition_switch_count": stable_switches,
                "majority_condition_changed": stable_changed,
                "majority_ready_condition_unique_count": ready_unique,
                "majority_ready_condition_switch_count": ready_switches,
                "majority_ready_condition_changed": ready_changed,
                "formal_online_switched_count": int(formal_switch_count),
                "majority_filters_raw_transition": (
                    raw_changed and not stable_changed
                ),
                "original_process_state_rejection_but_majority_stable": (
                    original_process_state_changed
                    and raw_changed
                    and not stable_changed
                ),
                "learnable_shape_process_state_rejection_but_majority_stable": (
                    learnable_shape
                    and original_process_state_changed
                    and raw_changed
                    and not stable_changed
                ),
            }
        )
    return pd.DataFrame(records)


def build_replay_summary(
    replay: pd.DataFrame,
    detail: pd.DataFrame,
) -> dict[str, Any]:
    """Build compact audit counts for the raw-vs-majority comparison."""

    def _sum_bool(frame: pd.DataFrame, column: str) -> int:
        if column not in frame.columns or frame.empty:
            return 0
        return int(frame[column].fillna(False).astype(bool).sum())

    def _mismatch_count(column: str) -> int:
        if column not in replay.columns or replay.empty:
            return 0
        comparable = replay[column].dropna()
        if comparable.empty:
            return 0
        return int((~comparable.astype(bool)).sum())

    target = detail.iloc[0:0]
    if not detail.empty:
        mask = (
            detail["learnable_flow_shape"].fillna(False).astype(bool)
            & detail["original_process_state_changed"]
            .fillna(False)
            .astype(bool)
        )
        target = detail.loc[mask]

    formal_switch_series = (
        pd.to_numeric(target["formal_online_switched_count"], errors="coerce")
        if "formal_online_switched_count" in target.columns
        else pd.Series(dtype=float)
    )

    return {
        "history_row_count": int(len(replay)),
        "source_grid_vs_replay_raw_mismatch_count": _mismatch_count(
            "source_grid_matches_replay_raw"
        ),
        "source_condition_vs_replay_raw_mismatch_count": _mismatch_count(
            "source_condition_matches_replay_raw"
        ),
        "episode_count": int(len(detail)),
        "raw_condition_changed_episode_count": _sum_bool(
            detail, "raw_condition_changed"
        ),
        "majority_condition_changed_episode_count": _sum_bool(
            detail, "majority_condition_changed"
        ),
        "original_process_state_changed_episode_count": _sum_bool(
            detail, "original_process_state_changed"
        ),
        "learnable_shape_process_state_changed_episode_count": int(
            len(target)
        ),
        "learnable_shape_process_state_still_changes_after_majority_count": (
            _sum_bool(target, "majority_condition_changed")
        ),
        "learnable_shape_process_state_filtered_by_majority_count": _sum_bool(
            target,
            "learnable_shape_process_state_rejection_but_majority_stable",
        ),
        "learnable_shape_process_state_formal_switch_event_count": int(
            (formal_switch_series.fillna(0) > 0).sum()
        ),
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(
            dict(value),
            stream,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )


def run_diagnostic(
    *,
    input_csv: str,
    condition_snapshot: str,
    invalid_episodes: str,
    output_dir: str,
    valid_episodes: str | None = None,
) -> dict[str, Any]:
    history = _read_csv(input_csv)
    snapshot = read_snapshot(condition_snapshot)
    replay = replay_canonical_condition_history(history, snapshot)

    episode_frames: list[pd.DataFrame] = []
    if valid_episodes:
        valid = _read_csv(valid_episodes)
        if not valid.empty:
            valid = valid.copy()
            valid["__episode_partition"] = "VALID"
            episode_frames.append(valid)
    invalid = _read_csv(invalid_episodes)
    if not invalid.empty:
        invalid = invalid.copy()
        invalid["__episode_partition"] = "INVALID"
        episode_frames.append(invalid)
    episodes = (
        pd.concat(episode_frames, ignore_index=True, sort=False)
        if episode_frames
        else pd.DataFrame()
    )

    detail = diagnose_episode_condition_transitions(replay, episodes)
    summary = build_replay_summary(replay, detail)

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    replay_path = root / "historical_condition_majority_replay.csv"
    detail_path = root / "historical_condition_event_transition_diagnostic.csv"
    summary_path = root / "historical_condition_majority_replay_summary.json"
    replay.to_csv(replay_path, index=False, encoding="utf-8-sig")
    detail.to_csv(detail_path, index=False, encoding="utf-8-sig")
    _write_json(summary_path, summary)

    result = dict(summary)
    result.update(
        {
            "replay_csv": str(replay_path),
            "event_detail_csv": str(detail_path),
            "summary_json": str(summary_path),
        }
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay canonical MAJORITY condition semantics over historical "
            "Scheme2 data and compare raw/stable transitions per supply event."
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--invalid-episodes", required=True)
    parser.add_argument("--valid-episodes")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)

    result = run_diagnostic(
        input_csv=args.input,
        condition_snapshot=args.snapshot,
        invalid_episodes=args.invalid_episodes,
        valid_episodes=args.valid_episodes,
        output_dir=args.output_dir,
    )

    print("========== CANONICAL CONDITION MAJORITY REPLAY ==========")
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
