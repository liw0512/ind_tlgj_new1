# -*- coding: utf-8 -*-
"""Historical pH/SO2 response-window diagnostic for Scheme 2 MFAC.

This module is REVIEW/DIAGNOSTIC ONLY.

Key evidence separation
-----------------------
Historical slurry-flow data contains two very different evidence types:

1. TIMING_ONLY evidence
   Clear, persistent large changes including STARTUP (near-zero -> operating)
   and SHUTDOWN (operating -> near-zero).  These events are useful for learning
   approximate pH/SO2 response timing, but their apparent ``delta_y/delta_q``
   must NOT be published as a local MFAC gain.

2. LOCAL_GAIN evidence
   Both before and after flow remain inside the operating region.  Only this
   class is a candidate for later local marginal-gain training, subject to the
   existing causal/context/blocked-validation gates.

The previous diagnostic incorrectly required both baseline and final flow to be
above the operating threshold even for timing analysis.  On the current plant
history that made the timing cohort empty because most strong historical
changes are startup/shutdown actions.

Safety contract: this module never mutates runtime configuration, never
publishes a historical prior, never enables LEARN/Residual, and never writes DCS.

Current review candidates (NOT CALIBRATED):
- pH:  flow-reached proxy + 5~8 min
- SO2: flow-reached proxy + 10~12 min
- legacy 3~13 min is retained only for A/B comparison.

``yyq_LL`` remains diagnostic-only and is not a formal MFAC feature/gate here.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG
from system.model.map_control.mfac_model.offline_training_config import (
    historical_episode_training_config,
)
from system.model.map_control.mfac_model.historical_episode_engine.config_loader import (
    enabled_towers,
)
from system.model.map_control.mfac_model.historical_episode_engine.schema import time_column
from system.model.map_control.mfac_model.historical_episode_engine.signal_processing import (
    add_clean_supply_flow_columns,
)
from system.model.map_control.mfac_model.historical_episode_engine.supply_flow_event_classifier import (
    classify_supply_flow_event,
)
from system.model.map_control.mfac_model.historical_episode_engine.supply_flow_event_detector import (
    detect_supply_flow_events,
)


OUTLET_SO2_COLUMN = "jyq_SO2"
INLET_SO2_COLUMN = "yyq_SO2"

PH_WINDOWS = (
    (2.0, 4.0),
    (3.0, 5.0),
    (4.0, 6.0),
    (4.0, 8.0),
    (5.0, 8.0),
    (6.0, 9.0),
    (8.0, 10.0),
    (10.0, 12.0),
    (3.0, 13.0),  # legacy shared window, comparison only
)
SO2_WINDOWS = (
    (3.0, 5.0),
    (4.0, 6.0),
    (5.0, 7.0),
    (6.0, 8.0),
    (7.0, 9.0),
    (7.0, 12.0),
    (8.0, 10.0),
    (9.0, 11.0),
    (10.0, 12.0),
    (3.0, 13.0),  # legacy shared window, comparison only
)


@dataclass(frozen=True)
class WindowSpec:
    channel: str
    start_min: float
    end_min: float

    @property
    def label(self) -> str:
        return "%s_%g_%gmin" % (self.channel, self.start_min, self.end_min)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _median(values: Iterable[Any]) -> Optional[float]:
    series = pd.to_numeric(pd.Series(list(values)), errors="coerce").dropna()
    return float(series.median()) if not series.empty else None


def _mad(values: Iterable[Any]) -> Optional[float]:
    array = pd.to_numeric(pd.Series(list(values)), errors="coerce").dropna().to_numpy(float)
    if array.size == 0:
        return None
    center = float(np.median(array))
    return float(np.median(np.abs(array - center)))


def fit_pretrend(
    timestamps: pd.Series,
    values: pd.Series,
    action_start: pd.Timestamp,
) -> tuple[Optional[float], Optional[float]]:
    """Fit baseline-only y = intercept + slope * minutes_from_action_start."""
    work = pd.DataFrame(
        {
            "time": pd.to_datetime(timestamps, errors="coerce"),
            "value": pd.to_numeric(values, errors="coerce"),
        }
    ).dropna()
    if len(work) < 3:
        return None, None

    x = (work["time"] - pd.Timestamp(action_start)).dt.total_seconds().to_numpy(float) / 60.0
    y = work["value"].to_numpy(float)
    if np.ptp(x) <= 1e-12:
        return None, None

    slope, intercept = np.polyfit(x, y, 1)
    residual = y - (intercept + slope * x)
    center = float(np.median(residual))
    mad = float(np.median(np.abs(residual - center)))
    if mad > 0.0 and np.isfinite(mad):
        keep = np.abs(residual - center) <= 4.0 * 1.4826 * mad
        if 3 <= int(np.sum(keep)) < len(y):
            slope, intercept = np.polyfit(x[keep], y[keep], 1)
    return float(intercept), float(slope)


def response_metrics(
    *,
    baseline: pd.DataFrame,
    response: pd.DataFrame,
    timestamp_column: str,
    signal_column: str,
    action_start: pd.Timestamp,
    delta_q: float,
) -> dict[str, Any]:
    """Calculate raw and pretrend-corrected response for one measurement window.

    ``raw_phi``/``corrected_phi`` in this diagnostic are descriptive apparent
    ratios.  For STARTUP/SHUTDOWN timing events they are explicitly NOT local
    MFAC gains and must not be published to the historical prior.
    """
    baseline_values = pd.to_numeric(baseline[signal_column], errors="coerce").dropna()
    response_values = pd.to_numeric(response[signal_column], errors="coerce").dropna()
    if baseline_values.empty or response_values.empty or abs(float(delta_q)) <= 1e-12:
        return {
            "baseline_median": None,
            "response_median": None,
            "pretrend_per_min": None,
            "counterfactual_response_median": None,
            "raw_delta": None,
            "corrected_delta": None,
            "raw_phi": None,
            "corrected_phi": None,
        }

    baseline_median = float(baseline_values.median())
    response_median = float(response_values.median())
    intercept, slope = fit_pretrend(
        baseline[timestamp_column], baseline[signal_column], pd.Timestamp(action_start)
    )

    counterfactual = None
    if intercept is not None and slope is not None:
        response_times = pd.to_datetime(response[timestamp_column], errors="coerce").dropna()
        if not response_times.empty:
            x = (
                response_times - pd.Timestamp(action_start)
            ).dt.total_seconds().to_numpy(float) / 60.0
            counterfactual = float(np.median(intercept + slope * x))

    raw_delta = response_median - baseline_median
    corrected_delta = (
        response_median - counterfactual if counterfactual is not None else None
    )
    return {
        "baseline_median": baseline_median,
        "response_median": response_median,
        "pretrend_per_min": slope,
        "counterfactual_response_median": counterfactual,
        "raw_delta": raw_delta,
        "corrected_delta": corrected_delta,
        "raw_phi": raw_delta / float(delta_q),
        "corrected_phi": (
            corrected_delta / float(delta_q) if corrected_delta is not None else None
        ),
    }


def physical_direction_ok(channel: str, apparent_phi: Any) -> Optional[bool]:
    value = _finite(apparent_phi)
    if value is None:
        return None
    if str(channel).upper() == "PH":
        return value > 0.0
    if str(channel).upper() == "SO2":
        return value < 0.0
    raise ValueError("unknown channel: %s" % channel)


def _slice(
    frame: pd.DataFrame,
    timestamp_column: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    timestamps = pd.to_datetime(frame[timestamp_column], errors="coerce")
    mask = (timestamps >= pd.Timestamp(start)) & (timestamps <= pd.Timestamp(end))
    return frame.loc[mask]


def _add_continuous_segment_id(
    frame: pd.DataFrame,
    *,
    timestamp_column: str,
    max_gap_seconds: float,
) -> pd.DataFrame:
    result = frame.copy()
    timestamps = pd.to_datetime(result[timestamp_column], errors="coerce")
    gaps = timestamps.diff().dt.total_seconds()
    result["continuous_segment_id"] = (
        gaps.isna() | (gaps > float(max_gap_seconds)) | (gaps < 0.0)
    ).cumsum()
    return result


def _tower_config(tower_id: str) -> dict[str, Any]:
    for tower in enabled_towers(PLANT_CONFIG):
        if str(tower.get("tower_id")) == str(tower_id):
            return tower
    raise ValueError("enabled tower not found: %s" % tower_id)


def prepare_history(
    csv_path: Path,
    *,
    tower_id: str,
) -> tuple[pd.DataFrame, list[Any], str, str]:
    """Load history and run the repository's current actual-flow detector."""
    training = historical_episode_training_config()
    timestamp_column = time_column(PLANT_CONFIG)
    tower = _tower_config(tower_id)
    ph_column = str(tower.get("ph_column") or "").strip()
    if not ph_column:
        raise ValueError("tower has no ph_column: %s" % tower_id)

    encoding = str(training.get("io", {}).get("csv_encoding") or "utf-8-sig")
    frame = pd.read_csv(csv_path, encoding=encoding)
    required = [timestamp_column, ph_column, OUTLET_SO2_COLUMN]
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise ValueError("missing required columns: %s" % missing)

    frame[timestamp_column] = pd.to_datetime(frame[timestamp_column], errors="coerce")
    frame = (
        frame.dropna(subset=[timestamp_column])
        .sort_values(timestamp_column, kind="stable")
        .drop_duplicates(subset=[timestamp_column], keep="last")
        .reset_index(drop=True)
    )
    max_gap = float(training["preprocessing"]["max_continuous_gap_seconds"])
    frame = _add_continuous_segment_id(
        frame,
        timestamp_column=timestamp_column,
        max_gap_seconds=max_gap,
    )
    frame = add_clean_supply_flow_columns(frame, PLANT_CONFIG, training)
    events = detect_supply_flow_events(frame, PLANT_CONFIG, training)
    return frame, events, timestamp_column, ph_column


def _flow_evidence_class(
    baseline_flow: Optional[float],
    final_flow: Optional[float],
    min_operating_flow: float,
) -> str:
    before_operating = baseline_flow is not None and baseline_flow >= min_operating_flow
    after_operating = final_flow is not None and final_flow >= min_operating_flow
    if before_operating and after_operating:
        return "LOCAL_STEP"
    if not before_operating and after_operating:
        return "STARTUP_STEP"
    if before_operating and not after_operating:
        return "SHUTDOWN_STEP"
    return "LOW_FLOW_TRANSITION"


def build_event_audit(
    frame: pd.DataFrame,
    events: list[Any],
    *,
    timestamp_column: str,
    baseline_minutes: float,
    max_response_end_minutes: float,
    min_abs_delta_q: float,
    min_operating_flow: float,
    max_timing_action_duration_minutes: float = 20.0,
    max_timing_transition_count: int = 3,
) -> pd.DataFrame:
    """Build separate TIMING_ONLY and LOCAL_GAIN eligibility flags.

    Timing analysis permits clear startup/shutdown steps.  Local-gain eligibility
    remains strict and requires both before/after flow in the operating region.
    """
    rows: list[dict[str, Any]] = []
    ordered = sorted(events, key=lambda item: pd.Timestamp(item.start_time))

    for index, event in enumerate(ordered):
        start = pd.Timestamp(event.start_time)
        reached_proxy = pd.Timestamp(event.end_time)
        baseline = _slice(
            frame,
            timestamp_column,
            start - pd.Timedelta(minutes=baseline_minutes),
            start,
        )
        horizon_end = reached_proxy + pd.Timedelta(minutes=max_response_end_minutes)
        horizon = _slice(frame, timestamp_column, start, horizon_end)
        next_start = (
            pd.Timestamp(ordered[index + 1].start_time)
            if index + 1 < len(ordered)
            else None
        )
        followup = bool(next_start is not None and next_start <= horizon_end)

        delta_q = _finite(getattr(event, "final_delta_flow", None))
        baseline_flow = _finite(getattr(event, "baseline_flow", None))
        final_flow = _finite(getattr(event, "final_flow", None))
        duration = _finite(getattr(event, "active_duration_minutes", None))
        transitions = int(getattr(event, "transition_count", 0) or 0)
        complete = bool(getattr(event, "complete", False))
        evidence_class = _flow_evidence_class(
            baseline_flow, final_flow, float(min_operating_flow)
        )

        try:
            shape = classify_supply_flow_event(event)
            shape_name = str(shape.shape)
            shape_direction = str(shape.direction)
        except Exception:
            shape_name = "UNAVAILABLE"
            shape_direction = "UNAVAILABLE"

        timing_reasons: list[str] = []
        if not complete:
            timing_reasons.append("EVENT_INCOMPLETE")
        if delta_q is None or abs(delta_q) < float(min_abs_delta_q):
            timing_reasons.append("DELTA_Q_BELOW_TIMING_MIN")
        if evidence_class == "LOW_FLOW_TRANSITION":
            timing_reasons.append("LOW_FLOW_TRANSITION")
        if followup:
            timing_reasons.append("FOLLOWUP_ACTION_WITHIN_RESPONSE_HORIZON")
        if baseline.empty:
            timing_reasons.append("BASELINE_EMPTY")
        if duration is None or duration > float(max_timing_action_duration_minutes):
            timing_reasons.append("ACTION_DURATION_TOO_LONG_FOR_TIMING")
        if transitions > int(max_timing_transition_count):
            timing_reasons.append("TOO_MANY_TRANSITIONS_FOR_TIMING")

        # Local gain is intentionally stricter than timing evidence.  This flag
        # is informational only; the official historical trainer still applies
        # its existing causal/context/model-validation gates downstream.
        local_gain_reasons = list(timing_reasons)
        if evidence_class != "LOCAL_STEP":
            local_gain_reasons.append("NOT_LOCAL_OPERATING_STEP")

        inlet_before = (
            _median(baseline[INLET_SO2_COLUMN])
            if INLET_SO2_COLUMN in baseline.columns
            else None
        )
        inlet_after = (
            _median(horizon[INLET_SO2_COLUMN])
            if INLET_SO2_COLUMN in horizon.columns
            else None
        )

        timing_eligible = not timing_reasons
        local_gain_eligible = not local_gain_reasons
        rows.append(
            {
                "event_index": index,
                "event_start_time": start,
                "flow_reached_proxy_time": reached_proxy,
                "event_date": start.date().isoformat(),
                "baseline_flow": baseline_flow,
                "final_flow": final_flow,
                "delta_q_actual": delta_q,
                "active_duration_minutes": duration,
                "transition_count": transitions,
                "detector_shape": shape_name,
                "detector_direction": shape_direction,
                "flow_evidence_class": evidence_class,
                "followup_action": followup,
                "inlet_so2_baseline_median": inlet_before,
                "inlet_so2_horizon_median": inlet_after,
                "inlet_so2_horizon_change": (
                    inlet_after - inlet_before
                    if inlet_before is not None and inlet_after is not None
                    else None
                ),
                "timing_eligible": timing_eligible,
                "timing_reasons": ";".join(timing_reasons) if timing_reasons else "OK",
                "local_gain_eligible": local_gain_eligible,
                "local_gain_reasons": (
                    ";".join(local_gain_reasons) if local_gain_reasons else "OK"
                ),
                # Backward-compatible alias for old result readers.  From V2 it
                # means TIMING eligibility, not local-gain eligibility.
                "diagnostic_eligible": timing_eligible,
                "diagnostic_reasons": (
                    ";".join(timing_reasons) if timing_reasons else "OK"
                ),
                "yyq_ll_used_as_formal_feature": False,
            }
        )
    return pd.DataFrame(rows)


def evaluate_windows(
    frame: pd.DataFrame,
    event_audit: pd.DataFrame,
    *,
    timestamp_column: str,
    ph_column: str,
    baseline_minutes: float,
) -> pd.DataFrame:
    """Evaluate independent pH/SO2 windows using TIMING_ONLY evidence."""
    rows: list[dict[str, Any]] = []
    windows = [
        *(WindowSpec("PH", start, end) for start, end in PH_WINDOWS),
        *(WindowSpec("SO2", start, end) for start, end in SO2_WINDOWS),
    ]

    for event in event_audit.itertuples(index=False):
        if not bool(event.timing_eligible):
            continue
        action_start = pd.Timestamp(event.event_start_time)
        reached_proxy = pd.Timestamp(event.flow_reached_proxy_time)
        baseline = _slice(
            frame,
            timestamp_column,
            action_start - pd.Timedelta(minutes=baseline_minutes),
            action_start,
        )
        delta_q = float(event.delta_q_actual)

        for spec in windows:
            signal_column = ph_column if spec.channel == "PH" else OUTLET_SO2_COLUMN
            response = _slice(
                frame,
                timestamp_column,
                reached_proxy + pd.Timedelta(minutes=spec.start_min),
                reached_proxy + pd.Timedelta(minutes=spec.end_min),
            )
            if response.empty:
                continue
            metrics = response_metrics(
                baseline=baseline,
                response=response,
                timestamp_column=timestamp_column,
                signal_column=signal_column,
                action_start=action_start,
                delta_q=delta_q,
            )
            rows.append(
                {
                    "event_index": event.event_index,
                    "event_date": event.event_date,
                    "event_start_time": action_start,
                    "flow_reached_proxy_time": reached_proxy,
                    "flow_evidence_class": event.flow_evidence_class,
                    "detector_shape": event.detector_shape,
                    "detector_direction": event.detector_direction,
                    "local_gain_eligible": bool(event.local_gain_eligible),
                    "delta_q_actual": delta_q,
                    "channel": spec.channel,
                    "window_start_min": spec.start_min,
                    "window_end_min": spec.end_min,
                    "window_label": spec.label,
                    **metrics,
                    "raw_direction_ok": physical_direction_ok(
                        spec.channel, metrics["raw_phi"]
                    ),
                    "corrected_direction_ok": physical_direction_ok(
                        spec.channel, metrics["corrected_phi"]
                    ),
                }
            )
    return pd.DataFrame(rows)


def _summarize_group(group: pd.DataFrame, channel: str) -> dict[str, Any]:
    corrected = pd.to_numeric(group["corrected_phi"], errors="coerce").dropna()
    raw = pd.to_numeric(group["raw_phi"], errors="coerce").dropna()
    corrected_ok = group["corrected_direction_ok"].dropna().astype(bool)
    raw_ok = group["raw_direction_ok"].dropna().astype(bool)

    day_phi = (
        group.assign(
            corrected_phi_num=pd.to_numeric(group["corrected_phi"], errors="coerce")
        )
        .dropna(subset=["corrected_phi_num"])
        .groupby("event_date")["corrected_phi_num"]
        .median()
    )
    day_ok = (day_phi > 0.0) if channel == "PH" else (day_phi < 0.0)

    median_phi = float(corrected.median()) if not corrected.empty else np.nan
    mad_phi = _mad(corrected)
    relative_mad = (
        float(mad_phi) / max(abs(median_phi), 1e-12)
        if mad_phi is not None and np.isfinite(median_phi)
        else np.nan
    )
    corrected_rate = float(corrected_ok.mean()) if len(corrected_ok) else np.nan
    day_rate = float(day_ok.mean()) if len(day_ok) else np.nan
    raw_rate = float(raw_ok.mean()) if len(raw_ok) else np.nan
    stability = 1.0 / (1.0 + relative_mad) if np.isfinite(relative_mad) else 0.0
    rank_score = (
        (day_rate if np.isfinite(day_rate) else 0.0) * 0.50
        + (corrected_rate if np.isfinite(corrected_rate) else 0.0) * 0.35
        + stability * 0.15
    )
    return {
        "event_count": int(len(corrected)),
        "independent_days": int(len(day_phi)),
        "raw_direction_rate": raw_rate,
        "corrected_direction_rate": corrected_rate,
        "date_median_direction_rate": day_rate,
        # Descriptive apparent ratio for timing review; not a published MFAC gain.
        "apparent_corrected_phi_median": median_phi,
        "apparent_corrected_phi_mad": mad_phi,
        "apparent_corrected_phi_relative_mad": relative_mad,
        "diagnostic_rank_score": rank_score,
    }


def summarize_windows(details: pd.DataFrame) -> pd.DataFrame:
    """Summarize the all-TIMING cohort by channel/window."""
    if details.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    keys = ["channel", "window_start_min", "window_end_min", "window_label"]
    for key, group in details.groupby(keys, sort=False):
        channel, start_min, end_min, label = key
        rows.append(
            {
                "cohort_scope": "ALL_TIMING",
                "channel": channel,
                "window_start_min": start_min,
                "window_end_min": end_min,
                "window_label": label,
                **_summarize_group(group, channel),
                "is_legacy_3_13": bool(start_min == 3.0 and end_min == 13.0),
                "is_current_review_candidate": bool(
                    (channel == "PH" and start_min == 5.0 and end_min == 8.0)
                    or (channel == "SO2" and start_min == 10.0 and end_min == 12.0)
                ),
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values(
            ["channel", "diagnostic_rank_score", "event_count"],
            ascending=[True, False, False],
            kind="stable",
        )
        .reset_index(drop=True)
    )


def summarize_windows_by_evidence_class(details: pd.DataFrame) -> pd.DataFrame:
    """Expose STARTUP/SHUTDOWN/LOCAL_STEP stability separately."""
    if details.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    keys = [
        "flow_evidence_class",
        "channel",
        "window_start_min",
        "window_end_min",
        "window_label",
    ]
    for key, group in details.groupby(keys, sort=False):
        evidence_class, channel, start_min, end_min, label = key
        rows.append(
            {
                "cohort_scope": evidence_class,
                "channel": channel,
                "window_start_min": start_min,
                "window_end_min": end_min,
                "window_label": label,
                **_summarize_group(group, channel),
            }
        )
    return pd.DataFrame(rows).sort_values(
        ["flow_evidence_class" if "flow_evidence_class" in rows[0] else "cohort_scope", "channel", "diagnostic_rank_score"],
        ascending=[True, True, False],
        kind="stable",
    ).reset_index(drop=True)


def write_report(
    path: Path,
    event_audit: pd.DataFrame,
    summary: pd.DataFrame,
) -> None:
    timing_count = int(event_audit["timing_eligible"].sum()) if not event_audit.empty else 0
    local_count = int(event_audit["local_gain_eligible"].sum()) if not event_audit.empty else 0
    class_counts = (
        event_audit.loc[event_audit["timing_eligible"].astype(bool), "flow_evidence_class"]
        .value_counts()
        .to_dict()
        if not event_audit.empty
        else {}
    )
    lines = [
        "# Scheme2 historical dual-response window diagnostic V2",
        "",
        "> REVIEW ONLY. This output is not CALIBRATED and cannot activate MFAC.",
        "",
        "## Evidence separation",
        "",
        "- TIMING_ONLY may use clear STARTUP/SHUTDOWN steps to estimate response timing.",
        "- LOCAL_GAIN remains restricted to operating-flow -> operating-flow local steps.",
        "- Apparent phi from STARTUP/SHUTDOWN is descriptive only and cannot become a runtime prior.",
        "- yyq_LL formal feature/gate: false",
        "",
        "## Event funnel",
        "",
        "- detector candidates: %d" % len(event_audit),
        "- timing cohort: %d" % timing_count,
        "- local-gain diagnostic cohort: %d" % local_count,
        "- timing class counts: %s" % class_counts,
        "- historical flow-reached proxy: event.end_time (stable new plateau)",
        "",
        "## Window scan",
        "",
    ]
    if summary.empty:
        lines.append("No results.")
    else:
        show_columns = [
            "channel",
            "window_start_min",
            "window_end_min",
            "event_count",
            "independent_days",
            "raw_direction_rate",
            "corrected_direction_rate",
            "date_median_direction_rate",
            "apparent_corrected_phi_median",
            "apparent_corrected_phi_relative_mad",
            "is_legacy_3_13",
            "is_current_review_candidate",
        ]
        try:
            lines.append(summary[show_columns].to_markdown(index=False, floatfmt=".4f"))
        except ImportError:
            lines.append(summary[show_columns].to_csv(index=False))
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "1. Prioritize date_median_direction_rate, then corrected_direction_rate.",
            "2. Compare PH 5~8 min and SO2 10~12 min against legacy 3~13 min.",
            "3. Check window_scan_by_evidence_class.csv so one-sided STARTUP/SHUTDOWN behavior does not hide disagreement.",
            "4. Large raw-vs-corrected differences indicate pre-action trend/endogeneity.",
            "5. Do not use STARTUP/SHUTDOWN apparent phi as local MFAC gain.",
            "6. Do not lower existing minimum-event, independent-day, sign or blocked-validation gates.",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8-sig")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=r"files\new_data_train_10s.csv")
    parser.add_argument("--out", default=r"files\scheme2_response_window_scan")
    parser.add_argument("--tower-id", default="xst")
    parser.add_argument("--baseline-minutes", type=float, default=5.0)
    parser.add_argument("--min-abs-delta-q", type=float, default=2.0)
    parser.add_argument("--min-operating-flow", type=float, default=5.0)
    parser.add_argument("--max-response-end-minutes", type=float, default=13.0)
    parser.add_argument("--max-timing-action-duration-minutes", type=float, default=20.0)
    parser.add_argument("--max-timing-transition-count", type=int, default=3)
    args = parser.parse_args()

    frame, events, timestamp_column, ph_column = prepare_history(
        Path(args.csv), tower_id=str(args.tower_id)
    )
    audit = build_event_audit(
        frame,
        events,
        timestamp_column=timestamp_column,
        baseline_minutes=float(args.baseline_minutes),
        max_response_end_minutes=float(args.max_response_end_minutes),
        min_abs_delta_q=float(args.min_abs_delta_q),
        min_operating_flow=float(args.min_operating_flow),
        max_timing_action_duration_minutes=float(args.max_timing_action_duration_minutes),
        max_timing_transition_count=int(args.max_timing_transition_count),
    )
    details = evaluate_windows(
        frame,
        audit,
        timestamp_column=timestamp_column,
        ph_column=ph_column,
        baseline_minutes=float(args.baseline_minutes),
    )
    summary = summarize_windows(details)
    by_class = summarize_windows_by_evidence_class(details)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    audit.to_csv(out / "event_audit.csv", index=False, encoding="utf-8-sig")
    details.to_csv(out / "window_event_details.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(out / "window_scan_summary.csv", index=False, encoding="utf-8-sig")
    by_class.to_csv(
        out / "window_scan_by_evidence_class.csv", index=False, encoding="utf-8-sig"
    )
    write_report(out / "response_window_scan_report.md", audit, summary)

    print("Scheme2 response-window diagnostic V2 completed.")
    print("detector candidates: %d" % len(audit))
    print("timing cohort: %d" % int(audit["timing_eligible"].sum()))
    print("local-gain diagnostic cohort: %d" % int(audit["local_gain_eligible"].sum()))
    print("output: %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
