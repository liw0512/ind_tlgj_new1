# -*- coding: utf-8 -*-
"""Diagnose inlet-SO2 disturbance / actual-slurry-flow direction coupling.

This module is diagnostic-only.  It must not change HistoricalEpisodeEngine
validity, historical evidence routing, MFAC priors, online LEARN permission,
residual control, or DCS write permission.

The diagnostic joins the canonical 546-style condition/action timing cohort with
the original historical flow episodes.  The primary disturbance quantity is the
3-minute median shift around action start that is already produced by
``historical_condition_action_timing_diagnostic``.  The slurry-action direction
and amplitude reuse the existing ACTUAL_SUPPLY_FLOW_V1 event semantics instead
of reconstructing a command-flow or endpoint-only delta.  This is important for
PULSE events whose final flow may return close to the old baseline.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


DEFAULT_SO2_DEADBANDS = (0.0, 50.0, 100.0)
PROCESS_STATE_CONTEXT_REASON = "PROCESS_STATE_CHANGED_DURING_EVENT"
PROCESS_STATE_ONLY_INVALID_REASON = (
    "FLOW_CONTEXT_NOT_CLEAN:PROCESS_STATE_CHANGED_DURING_EVENT"
)


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


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    return default


def _flow_direction_sign(value: Any) -> int | None:
    text = _text(value).upper()
    if text == "INCREASE":
        return 1
    if text == "DECREASE":
        return -1
    return None


def _so2_direction(value: float | None, deadband: float) -> str:
    if value is None:
        return "MISSING"
    if value > float(deadband):
        return "INCREASE"
    if value < -float(deadband):
        return "DECREASE"
    return "NEUTRAL"


def _coupling_role(
    flow_direction: Any,
    so2_shift: float | None,
    deadband: float,
) -> str:
    flow_sign = _flow_direction_sign(flow_direction)
    if flow_sign is None:
        return "FLOW_UNCLASSIFIED"
    so2_direction = _so2_direction(so2_shift, deadband)
    if so2_direction == "MISSING":
        return "SO2_MISSING"
    if so2_direction == "NEUTRAL":
        return "SO2_NEUTRAL"
    so2_sign = 1 if so2_direction == "INCREASE" else -1
    return "SAME_DIRECTION" if flow_sign == so2_sign else "OPPOSITE_DIRECTION"


def _deadband_key(value: float) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number).replace(".", "p")


def _quantiles(values: pd.Series) -> dict[str, float | None]:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return {"p50": None, "p75": None, "p90": None, "p95": None, "max": None}
    return {
        "p50": float(numeric.quantile(0.50)),
        "p75": float(numeric.quantile(0.75)),
        "p90": float(numeric.quantile(0.90)),
        "p95": float(numeric.quantile(0.95)),
        "max": float(numeric.max()),
    }


def _correlation(x: pd.Series, y: pd.Series) -> dict[str, float | int | None]:
    pair = pd.DataFrame(
        {
            "x": pd.to_numeric(x, errors="coerce"),
            "y": pd.to_numeric(y, errors="coerce"),
        }
    ).dropna()
    count = int(len(pair))
    if count < 2 or pair["x"].nunique() < 2 or pair["y"].nunique() < 2:
        return {"pair_count": count, "pearson": None, "spearman": None}
    pearson = _finite(pair["x"].corr(pair["y"], method="pearson"))
    # Rank first so Spearman does not require scipy at runtime.
    spearman = _finite(
        pair["x"].rank(method="average").corr(
            pair["y"].rank(method="average"), method="pearson"
        )
    )
    return {"pair_count": count, "pearson": pearson, "spearman": spearman}


def _require_unique_episode_ids(frame: pd.DataFrame, name: str) -> None:
    if "episode_id" not in frame.columns:
        raise KeyError(f"{name} is missing required column 'episode_id'")
    ids = frame["episode_id"].dropna().astype(str)
    if ids.duplicated().any():
        duplicate = ids.loc[ids.duplicated()].iloc[0]
        raise ValueError(f"{name} contains duplicate episode_id: {duplicate}")


def diagnose_disturbance_slurry_coupling(
    episodes: pd.DataFrame,
    timing_detail: pd.DataFrame,
    *,
    so2_deadbands: Sequence[float] = DEFAULT_SO2_DEADBANDS,
) -> pd.DataFrame:
    """Join the formal-switch cohort with actual-flow event semantics.

    ``timing_detail`` is expected to be the output of
    ``historical_condition_action_timing_diagnostic``; therefore every row is
    already in the learnable-shape + process-state-change + formal-MAJORITY-
    switch cohort.  ``episodes`` supplies the original actual-flow trajectory
    fields and original rejection/effect context.
    """
    if timing_detail.empty:
        return pd.DataFrame()
    if episodes.empty:
        raise ValueError("episodes is empty while timing_detail contains target events")

    _require_unique_episode_ids(episodes, "episodes")
    _require_unique_episode_ids(timing_detail, "timing_detail")

    required_timing = {
        "episode_id",
        "flow_shape",
        "start_local_median_shift",
        "last_pre_switch_offset_min_from_action_start",
    }
    required_episode = {
        "episode_id",
        "action_semantics",
        "flow_direction",
        "flow_event_peak_delta_flow",
        "flow_event_max_abs_delta_flow",
        "flow_event_final_delta_flow",
        "flow_event_baseline_flow",
        "flow_effect_complete",
        "flow_context_reason",
        "invalid_reason",
    }
    missing_timing = sorted(required_timing - set(timing_detail.columns))
    missing_episode = sorted(required_episode - set(episodes.columns))
    if missing_timing:
        raise KeyError("timing_detail is missing required columns: " + ", ".join(missing_timing))
    if missing_episode:
        raise KeyError("episodes is missing required columns: " + ", ".join(missing_episode))

    event_columns = list(required_episode)
    optional_event_columns = [
        "valid",
        "flow_event_tower_id",
        "action_direction",
        "action_magnitude_value",
        "flow_event_trigger_deadband",
        "flow_event_active_duration_minutes",
    ]
    event_columns.extend(column for column in optional_event_columns if column in episodes.columns)

    event_view = episodes[event_columns].copy()
    event_view["episode_id"] = event_view["episode_id"].astype(str)
    timing = timing_detail.copy()
    timing["episode_id"] = timing["episode_id"].astype(str)
    joined = timing.merge(
        event_view,
        on="episode_id",
        how="left",
        validate="one_to_one",
        suffixes=("", "__episode"),
        indicator=True,
    )
    missing_ids = joined.loc[joined["_merge"] != "both", "episode_id"].tolist()
    if missing_ids:
        preview = ", ".join(map(str, missing_ids[:5]))
        raise KeyError(f"{len(missing_ids)} timing episodes are missing from episodes: {preview}")
    joined.drop(columns=["_merge"], inplace=True)

    records: list[dict[str, Any]] = []
    deadbands = tuple(sorted({float(value) for value in so2_deadbands}))
    if not deadbands or any(value < 0.0 or not math.isfinite(value) for value in deadbands):
        raise ValueError("so2_deadbands must contain finite values >= 0")

    for _, row in joined.iterrows():
        flow_direction = _text(row.get("flow_direction")).upper()
        flow_sign = _flow_direction_sign(flow_direction)
        peak_delta = _finite(row.get("flow_event_peak_delta_flow"))
        max_abs_delta = _finite(row.get("flow_event_max_abs_delta_flow"))
        signed_flow_amplitude = None
        if flow_sign is not None and max_abs_delta is not None:
            signed_flow_amplitude = float(flow_sign) * abs(max_abs_delta)
        elif peak_delta is not None:
            signed_flow_amplitude = peak_delta

        so2_start_shift = _finite(row.get("start_local_median_shift"))
        invalid_reason = _text(row.get("invalid_reason"))
        context_reason = _text(row.get("flow_context_reason"))
        effect_complete = _bool(row.get("flow_effect_complete"), False)
        action_semantics = _text(row.get("action_semantics"))

        record: dict[str, Any] = {
            "episode_id": row.get("episode_id"),
            "flow_shape": _text(row.get("flow_shape")).upper(),
            "flow_direction": flow_direction,
            "action_semantics": action_semantics,
            "actual_flow_semantics_ok": action_semantics == "ACTUAL_SUPPLY_FLOW_V1",
            "flow_event_baseline_flow": _finite(row.get("flow_event_baseline_flow")),
            "flow_event_peak_delta_flow": peak_delta,
            "flow_event_max_abs_delta_flow": max_abs_delta,
            "flow_event_final_delta_flow": _finite(row.get("flow_event_final_delta_flow")),
            "flow_signed_amplitude_actual": signed_flow_amplitude,
            "flow_peak_direction_consistent": (
                None
                if flow_sign is None or peak_delta is None
                else bool(float(flow_sign) * peak_delta > 0.0)
            ),
            "so2_start_local_median_shift": so2_start_shift,
            "so2_action_delta": _finite(row.get("action_axis_delta")),
            "so2_action_range": _finite(row.get("action_axis_range")),
            "last_pre_switch_offset_min_from_action_start": _finite(
                row.get("last_pre_switch_offset_min_from_action_start")
            ),
            "first_action_switch_offset_min": _finite(row.get("first_action_switch_offset_min")),
            "switch_pattern": _text(row.get("switch_pattern")),
            "flow_effect_complete": effect_complete,
            "flow_context_reason": context_reason,
            "invalid_reason": invalid_reason,
            "only_condition_context_invalid": (
                invalid_reason == PROCESS_STATE_ONLY_INVALID_REASON
                and context_reason == PROCESS_STATE_CONTEXT_REASON
            ),
            "only_condition_context_invalid_and_effect_complete": (
                invalid_reason == PROCESS_STATE_ONLY_INVALID_REASON
                and context_reason == PROCESS_STATE_CONTEXT_REASON
                and effect_complete
            ),
        }
        for deadband in deadbands:
            key = _deadband_key(deadband)
            record[f"so2_direction_db_{key}"] = _so2_direction(so2_start_shift, deadband)
            record[f"coupling_db_{key}"] = _coupling_role(
                flow_direction, so2_start_shift, deadband
            )
        records.append(record)
    return pd.DataFrame(records)


def _counts(frame: pd.DataFrame, column: str) -> dict[str, int]:
    if frame.empty or column not in frame.columns:
        return {}
    values = frame[column].fillna("<NA>").astype(str).value_counts()
    return {str(key): int(value) for key, value in values.items()}


def _direction_summary(detail: pd.DataFrame, deadband: float) -> dict[str, Any]:
    key = _deadband_key(deadband)
    column = f"coupling_db_{key}"
    counts = _counts(detail, column)
    same = int(counts.get("SAME_DIRECTION", 0))
    opposite = int(counts.get("OPPOSITE_DIRECTION", 0))
    neutral = int(counts.get("SO2_NEUTRAL", 0))
    flow_unclassified = int(counts.get("FLOW_UNCLASSIFIED", 0))
    so2_missing = int(counts.get("SO2_MISSING", 0))
    directional = same + opposite
    classifiable = directional + neutral
    return {
        "so2_deadband": float(deadband),
        "same_direction_count": same,
        "opposite_direction_count": opposite,
        "so2_neutral_count": neutral,
        "flow_unclassified_count": flow_unclassified,
        "so2_missing_count": so2_missing,
        "directional_pair_count": directional,
        "classifiable_pair_count": classifiable,
        "same_direction_ratio_directional": (
            float(same / directional) if directional else None
        ),
        "opposite_direction_ratio_directional": (
            float(opposite / directional) if directional else None
        ),
        "same_direction_ratio_all_classifiable": (
            float(same / classifiable) if classifiable else None
        ),
    }


def _pre_switch_summary(detail: pd.DataFrame) -> dict[str, Any]:
    if detail.empty or "last_pre_switch_offset_min_from_action_start" not in detail.columns:
        return {
            "available_count": 0,
            "within_1_min_count": 0,
            "within_3_min_count": 0,
            "within_5_min_count": 0,
            "within_10_min_count": 0,
            "absolute_offset_quantiles": _quantiles(pd.Series(dtype=float)),
        }
    values = pd.to_numeric(
        detail["last_pre_switch_offset_min_from_action_start"], errors="coerce"
    ).dropna()
    # Pre-switch offsets are negative by construction; distance to action start
    # is therefore the absolute value.
    distance = values.abs()
    return {
        "available_count": int(len(values)),
        "within_1_min_count": int((distance <= 1.0).sum()),
        "within_3_min_count": int((distance <= 3.0).sum()),
        "within_5_min_count": int((distance <= 5.0).sum()),
        "within_10_min_count": int((distance <= 10.0).sum()),
        "absolute_offset_quantiles": _quantiles(distance),
    }


def build_coupling_summary(
    detail: pd.DataFrame,
    *,
    so2_deadbands: Sequence[float] = DEFAULT_SO2_DEADBANDS,
) -> dict[str, Any]:
    deadbands = tuple(sorted({float(value) for value in so2_deadbands}))
    if detail.empty:
        empty = pd.Series(dtype=float)
        return {
            "target_event_count": 0,
            "flow_shape_counts": {},
            "flow_direction_counts": {},
            "actual_flow_semantics_ok_count": 0,
            "only_condition_context_invalid_count": 0,
            "only_condition_context_invalid_and_effect_complete_count": 0,
            "direction_coupling": {
                _deadband_key(value): _direction_summary(detail, value)
                for value in deadbands
            },
            "signed_correlation": _correlation(empty, empty),
            "absolute_amplitude_correlation": _correlation(empty, empty),
            "pre_switch_timing": _pre_switch_summary(detail),
            "flow_abs_peak_delta_quantiles": _quantiles(empty),
            "so2_start_abs_shift_quantiles": _quantiles(empty),
        }

    so2_shift = pd.to_numeric(detail["so2_start_local_median_shift"], errors="coerce")
    signed_flow = pd.to_numeric(detail["flow_signed_amplitude_actual"], errors="coerce")
    flow_abs = pd.to_numeric(detail["flow_event_max_abs_delta_flow"], errors="coerce")
    return {
        "target_event_count": int(len(detail)),
        "flow_shape_counts": _counts(detail, "flow_shape"),
        "flow_direction_counts": _counts(detail, "flow_direction"),
        "actual_flow_semantics_ok_count": int(
            detail["actual_flow_semantics_ok"].fillna(False).astype(bool).sum()
        ),
        "only_condition_context_invalid_count": int(
            detail["only_condition_context_invalid"].fillna(False).astype(bool).sum()
        ),
        "only_condition_context_invalid_and_effect_complete_count": int(
            detail["only_condition_context_invalid_and_effect_complete"]
            .fillna(False)
            .astype(bool)
            .sum()
        ),
        "direction_coupling": {
            _deadband_key(value): _direction_summary(detail, value)
            for value in deadbands
        },
        "signed_correlation": _correlation(so2_shift, signed_flow),
        "absolute_amplitude_correlation": _correlation(so2_shift.abs(), flow_abs.abs()),
        "pre_switch_timing": _pre_switch_summary(detail),
        "flow_abs_peak_delta_quantiles": _quantiles(flow_abs.abs()),
        "flow_abs_final_delta_quantiles": _quantiles(
            pd.to_numeric(detail["flow_event_final_delta_flow"], errors="coerce").abs()
        ),
        "so2_start_abs_shift_quantiles": _quantiles(so2_shift.abs()),
        "so2_action_range_quantiles": _quantiles(
            pd.to_numeric(detail["so2_action_range"], errors="coerce")
        ),
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(dict(value), stream, ensure_ascii=False, indent=2, allow_nan=False)


def run_diagnostic(
    *,
    episodes_csv: str,
    timing_detail_csv: str,
    output_dir: str,
    so2_deadbands: Sequence[float] = DEFAULT_SO2_DEADBANDS,
) -> dict[str, Any]:
    episodes = _read_csv(episodes_csv)
    timing_detail = _read_csv(timing_detail_csv)
    detail = diagnose_disturbance_slurry_coupling(
        episodes,
        timing_detail,
        so2_deadbands=so2_deadbands,
    )
    summary = build_coupling_summary(detail, so2_deadbands=so2_deadbands)
    summary.update(
        {
            "semantics": "DISTURBANCE_SLURRY_COUPLING_DIAGNOSTIC_V1",
            "primary_so2_metric": "ACTION_START_PRE3_POST3_MEDIAN_SHIFT",
            "primary_flow_metric": "ACTUAL_SUPPLY_FLOW_SIGNED_PEAK_EXCURSION",
            "so2_deadbands": [float(value) for value in so2_deadbands],
            "diagnostic_only": True,
            "changes_mfac_eligibility": False,
            "changes_runtime_permissions": False,
        }
    )

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    detail_path = root / "historical_disturbance_slurry_coupling_detail.csv"
    summary_path = root / "historical_disturbance_slurry_coupling_summary.json"
    detail.to_csv(detail_path, index=False, encoding="utf-8-sig")
    _write_json(summary_path, summary)

    result = dict(summary)
    result["detail_csv"] = str(detail_path)
    result["summary_json"] = str(summary_path)
    return result


def _parse_deadbands(value: str) -> tuple[float, ...]:
    result = tuple(float(item.strip()) for item in str(value).split(",") if item.strip())
    if not result:
        raise argparse.ArgumentTypeError("at least one SO2 deadband is required")
    if any(not math.isfinite(item) or item < 0.0 for item in result):
        raise argparse.ArgumentTypeError("SO2 deadbands must be finite values >= 0")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose inlet-SO2 direction coupling with actual historical "
            "slurry-flow actions in the formal condition-switch cohort."
        )
    )
    parser.add_argument("--episodes", required=True)
    parser.add_argument("--timing-detail", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--so2-deadbands",
        type=_parse_deadbands,
        default=DEFAULT_SO2_DEADBANDS,
        help="comma-separated mg/Nm3 deadbands; default: 0,50,100",
    )
    args = parser.parse_args(argv)

    result = run_diagnostic(
        episodes_csv=args.episodes,
        timing_detail_csv=args.timing_detail,
        output_dir=args.output_dir,
        so2_deadbands=args.so2_deadbands,
    )
    print("========== DISTURBANCE / SLURRY DIRECTION COUPLING ==========")
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
