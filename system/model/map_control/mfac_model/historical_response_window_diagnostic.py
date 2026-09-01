# -*- coding: utf-8 -*-
"""Historical pH/SO2 response-window diagnostic for Scheme 2 MFAC.

This module is intentionally diagnostic-only.  It reuses the current historical
actual-slurry-flow detector, then evaluates pH and outlet-SO2 with independent
measurement windows and an explicit pre-action-trend correction.

It does NOT mutate production configuration, publish a historical prior, enable
online learning/residual control, or write DCS commands.

Current review candidates are deliberately not called CALIBRATED:
- pH:  flow-reached proxy + 5~8 min
- SO2: flow-reached proxy + 10~12 min

The legacy shared 3~13 min window is kept in the scan only for A/B comparison.
``yyq_LL`` is not used as a formal MFAC feature/gate in this diagnostic.
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
from system.model.map_control.mfac_model.historical_episode_engine.schema import (
    time_column,
)
from system.model.map_control.mfac_model.historical_episode_engine.signal_processing import (
    add_clean_supply_flow_columns,
    clean_supply_flow_column,
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

    # Remove only extreme baseline residual outliers and refit once.  This keeps
    # the diagnostic transparent while preventing one instrument spike from
    # dominating the no-action trend projection.
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
    """Calculate raw and pretrend-corrected response/phi for one window."""
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


def physical_direction_ok(channel: str, phi: Any) -> Optional[bool]:
    value = _finite(phi)
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
    clean_flow_column = clean_supply_flow_column(tower_id)
    return frame, events, timestamp_column, ph_column


def build_event_audit(
    frame: pd.DataFrame,
    events: list[Any],
    *,
    timestamp_column: str,
    baseline_minutes: float,
    max_response_end_minutes: float,
    min_abs_delta_q: float,
    min_operating_flow: float,
) -> pd.DataFrame:
    """Create a deliberately simple timing-study cohort and retain rejects."""
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
        reasons: list[str] = []
        if not bool(getattr(event, "complete", False)):
            reasons.append("EVENT_INCOMPLETE")
        if delta_q is None or abs(delta_q) < float(min_abs_delta_q):
            reasons.append("DELTA_Q_BELOW_DIAGNOSTIC_MIN")
        if baseline_flow is None or baseline_flow < float(min_operating_flow):
            reasons.append("BASELINE_FLOW_BELOW_DIAGNOSTIC_MIN")
        if final_flow is None or final_flow < float(min_operating_flow):
            reasons.append("FINAL_FLOW_BELOW_DIAGNOSTIC_MIN")
        if followup:
            reasons.append("FOLLOWUP_ACTION_WITHIN_RESPONSE_HORIZON")
        if baseline.empty:
            reasons.append("BASELINE_EMPTY")

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

        rows.append(
            {
                "event_index": index,
                "event_start_time": start,
                "flow_reached_proxy_time": reached_proxy,
                "event_date": start.date().isoformat(),
                "baseline_flow": baseline_flow,
                "final_flow": final_flow,
                "delta_q_actual": delta_q,
                "active_duration_minutes": _finite(
                    getattr(event, "active_duration_minutes", None)
                ),
                "transition_count": getattr(event, "transition_count", None),
                "followup_action": followup,
                "inlet_so2_baseline_median": inlet_before,
                "inlet_so2_horizon_median": inlet_after,
                "inlet_so2_horizon_change": (
                    inlet_after - inlet_before
                    if inlet_before is not None and inlet_after is not None
                    else None
                ),
                "diagnostic_eligible": not reasons,
                "diagnostic_reasons": ";".join(reasons) if reasons else "OK",
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
    rows: list[dict[str, Any]] = []
    windows = [
        *(WindowSpec("PH", start, end) for start, end in PH_WINDOWS),
        *(WindowSpec("SO2", start, end) for start, end in SO2_WINDOWS),
    ]

    for event in event_audit.itertuples(index=False):
        if not bool(event.diagnostic_eligible):
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


def summarize_windows(details: pd.DataFrame) -> pd.DataFrame:
    if details.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    keys = ["channel", "window_start_min", "window_end_min", "window_label"]
    for key, group in details.groupby(keys, sort=False):
        channel, start_min, end_min, label = key
        corrected = pd.to_numeric(group["corrected_phi"], errors="coerce").dropna()
        raw_direction = group["raw_direction_ok"].dropna().astype(bool)
        corrected_direction = group["corrected_direction_ok"].dropna().astype(bool)
        day_phi = (
            group.assign(
                corrected_phi_num=pd.to_numeric(group["corrected_phi"], errors="coerce")
            )
            .dropna(subset=["corrected_phi_num"])
            .groupby("event_date")["corrected_phi_num"]
            .median()
        )
        day_direction = (day_phi > 0.0) if channel == "PH" else (day_phi < 0.0)
        phi_median = float(corrected.median()) if not corrected.empty else np.nan
        phi_mad = _mad(corrected)
        relative_mad = (
            float(phi_mad) / max(abs(phi_median), 1e-12)
            if phi_mad is not None and np.isfinite(phi_median)
            else np.nan
        )
        event_direction_rate = (
            float(corrected_direction.mean()) if len(corrected_direction) else np.nan
        )
        date_direction_rate = float(day_direction.mean()) if len(day_direction) else np.nan
        raw_direction_rate = (
            float(raw_direction.mean()) if len(raw_direction) else np.nan
        )
        stability_component = (
            1.0 / (1.0 + relative_mad) if np.isfinite(relative_mad) else 0.0
        )
        diagnostic_rank = (
            (date_direction_rate if np.isfinite(date_direction_rate) else 0.0) * 0.50
            + (event_direction_rate if np.isfinite(event_direction_rate) else 0.0) * 0.35
            + stability_component * 0.15
        )
        rows.append(
            {
                "channel": channel,
                "window_start_min": start_min,
                "window_end_min": end_min,
                "window_label": label,
                "event_count": int(len(corrected)),
                "independent_days": int(len(day_phi)),
                "raw_direction_rate": raw_direction_rate,
                "corrected_direction_rate": event_direction_rate,
                "date_median_direction_rate": date_direction_rate,
                "corrected_phi_median": phi_median,
                "corrected_phi_mad": phi_mad,
                "corrected_phi_relative_mad": relative_mad,
                "diagnostic_rank_score": diagnostic_rank,
                "is_legacy_3_13": bool(start_min == 3.0 and end_min == 13.0),
                "is_current_review_candidate": bool(
                    (channel == "PH" and start_min == 5.0 and end_min == 8.0)
                    or (channel == "SO2" and start_min == 10.0 and end_min == 12.0)
                ),
            }
        )

    return pd.DataFrame(rows).sort_values(
        ["channel", "diagnostic_rank_score", "event_count"],
        ascending=[True, False, False],
        kind="stable",
    ).reset_index(drop=True)


def _markdown_table(frame: pd.DataFrame, columns: list[str]) -> list[str]:
    if frame.empty:
        return ["No results."]
    rows = ["| " + " | ".join(columns) + " |"]
    rows.append("| " + " | ".join(["---"] * len(columns)) + " |")
    for record in frame[columns].to_dict("records"):
        values: list[str] = []
        for column in columns:
            value = record[column]
            if isinstance(value, float):
                values.append("" if not np.isfinite(value) else "%.4f" % value)
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return rows


def write_report(
    path: Path,
    event_audit: pd.DataFrame,
    summary: pd.DataFrame,
) -> None:
    columns = [
        "channel",
        "window_start_min",
        "window_end_min",
        "event_count",
        "independent_days",
        "raw_direction_rate",
        "corrected_direction_rate",
        "date_median_direction_rate",
        "corrected_phi_median",
        "corrected_phi_relative_mad",
        "is_legacy_3_13",
        "is_current_review_candidate",
    ]
    lines = [
        "# Scheme2 historical dual-response window diagnostic",
        "",
        "> REVIEW ONLY. This output is not CALIBRATED and cannot activate MFAC.",
        "",
        "## Event funnel",
        "",
        "- detector candidates: %d" % len(event_audit),
        "- diagnostic cohort: %d"
        % (int(event_audit["diagnostic_eligible"].sum()) if not event_audit.empty else 0),
        "- yyq_LL formal feature/gate: false",
        "- historical flow-reached proxy: event.end_time (stable new plateau)",
        "",
        "## Window scan",
        "",
        *_markdown_table(summary, columns),
        "",
        "## Interpretation",
        "",
        "1. Prioritize date_median_direction_rate, then corrected_direction_rate.",
        "2. Compare candidate PH 5~8 min and SO2 10~12 min against legacy 3~13 min.",
        "3. Large raw-vs-corrected differences indicate pre-action trend/endogeneity.",
        "4. Do not lower existing minimum-event, independent-day, sign or blocked-validation gates.",
        "5. Send this report plus window_scan_summary.csv and event_audit.csv for review.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8-sig")


def run_diagnostic(
    *,
    csv_path: Path,
    out_dir: Path,
    tower_id: str = "xst",
    baseline_minutes: float = 5.0,
    min_abs_delta_q: float = 2.0,
    min_operating_flow: float = 5.0,
    max_response_end_minutes: float = 13.0,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    frame, events, timestamp_column, ph_column = prepare_history(
        csv_path, tower_id=tower_id
    )
    audit = build_event_audit(
        frame,
        events,
        timestamp_column=timestamp_column,
        baseline_minutes=baseline_minutes,
        max_response_end_minutes=max_response_end_minutes,
        min_abs_delta_q=min_abs_delta_q,
        min_operating_flow=min_operating_flow,
    )
    details = evaluate_windows(
        frame,
        audit,
        timestamp_column=timestamp_column,
        ph_column=ph_column,
        baseline_minutes=baseline_minutes,
    )
    summary = summarize_windows(details)

    out_dir.mkdir(parents=True, exist_ok=True)
    audit.to_csv(out_dir / "event_audit.csv", index=False, encoding="utf-8-sig")
    details.to_csv(
        out_dir / "window_event_details.csv", index=False, encoding="utf-8-sig"
    )
    summary.to_csv(
        out_dir / "window_scan_summary.csv", index=False, encoding="utf-8-sig"
    )
    write_report(out_dir / "response_window_scan_report.md", audit, summary)
    return audit, details, summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scheme2 historical PH/SO2 response-window diagnostic"
    )
    parser.add_argument("--csv", default=r"files\new_data_train_10s.csv")
    parser.add_argument(
        "--out", default=r"files\scheme2_response_window_scan"
    )
    parser.add_argument("--tower-id", default="xst")
    parser.add_argument("--baseline-minutes", type=float, default=5.0)
    parser.add_argument("--min-abs-delta-q", type=float, default=2.0)
    parser.add_argument("--min-operating-flow", type=float, default=5.0)
    parser.add_argument("--max-response-end-minutes", type=float, default=13.0)
    args = parser.parse_args()

    audit, _, summary = run_diagnostic(
        csv_path=Path(args.csv),
        out_dir=Path(args.out),
        tower_id=str(args.tower_id),
        baseline_minutes=float(args.baseline_minutes),
        min_abs_delta_q=float(args.min_abs_delta_q),
        min_operating_flow=float(args.min_operating_flow),
        max_response_end_minutes=float(args.max_response_end_minutes),
    )
    print("Scheme2 historical response-window diagnostic completed")
    print("detected events:", len(audit))
    print(
        "diagnostic cohort:",
        int(audit["diagnostic_eligible"].sum()) if not audit.empty else 0,
    )
    if not summary.empty:
        for channel in ("PH", "SO2"):
            part = summary.loc[summary["channel"] == channel]
            if not part.empty:
                top = part.iloc[0]
                print(
                    "%s top review window: %g~%g min, event-direction=%.3f, date-direction=%.3f"
                    % (
                        channel,
                        top["window_start_min"],
                        top["window_end_min"],
                        top["corrected_direction_rate"],
                        top["date_median_direction_rate"],
                    )
                )
    print("output:", Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
