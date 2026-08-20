from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from .config_loader import enabled_towers
from .schema import time_column
from .signal_processing import clean_supply_flow_column
from .time_index import TimeWindowIndexer


@dataclass(frozen=True)
class SupplyFlowEvent:
    """One observed actual-slurry-flow trajectory before shape classification.

    Batch 2A intentionally stores continuous physical features only.  ``STEP``,
    ``PULSE`` and ``BOOST_STEP`` classification is added in the next batch so a
    classification mistake cannot affect event segmentation itself.
    """

    tower_id: str
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    baseline_flow: float
    final_flow: float
    peak_flow: float
    trough_flow: float
    peak_delta_flow: float
    final_delta_flow: float
    max_abs_delta_flow: float
    extra_slurry_volume: float
    deficit_slurry_volume: float
    signed_slurry_volume: float
    active_duration_minutes: float
    time_to_extreme_minutes: float
    time_from_extreme_to_end_minutes: float
    baseline_noise_sigma: float
    trigger_deadband: float
    transition_count: int
    complete: bool


@dataclass(frozen=True)
class _DetectionSettings:
    baseline_minutes: float
    backtrack_minutes: float
    stable_minutes: float
    max_transition_minutes: float
    trajectory_merge_gap_minutes: float
    noise_multiplier: float
    span_deadband_ratio: float
    level_deadband_ratio: float
    trigger_confirmation_points: int


def _settings(training: dict[str, Any]) -> _DetectionSettings:
    episode = training.get("episode", {})
    override = training.get("supply_flow_event_detection", {}) or {}
    max_transition = float(episode.get("max_action_duration_minutes", 20.0))
    response_window = float(episode.get("response_window_minutes", 10.0))
    return _DetectionSettings(
        baseline_minutes=float(episode.get("baseline_minutes", 5.0)),
        backtrack_minutes=float(episode.get("action_detection_window_minutes", 2.0)),
        stable_minutes=float(episode.get("action_end_stable_minutes", 1.5)),
        max_transition_minutes=max_transition,
        # Nearby transitions are deliberately chained before shape
        # classification.  This lets 0->60->0 and 10->50->20 remain one
        # trajectory instead of two unrelated actions.  The default follows
        # the existing response horizon and can later be overridden without a
        # plant-specific parameter.
        trajectory_merge_gap_minutes=float(
            override.get(
                "trajectory_merge_gap_minutes",
                min(response_window, max_transition / 2.0),
            )
        ),
        noise_multiplier=float(override.get("noise_multiplier", 4.0)),
        span_deadband_ratio=float(override.get("span_deadband_ratio", 0.01)),
        level_deadband_ratio=float(override.get("level_deadband_ratio", 0.005)),
        trigger_confirmation_points=max(
            1, int(override.get("trigger_confirmation_points", 2))
        ),
    )


def _robust_noise_sigma(values: pd.Series) -> float:
    """Estimate point noise from first differences without model fitting."""
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if len(numeric) < 3:
        return 0.0
    diffs = numeric.diff().dropna().to_numpy(dtype=float)
    if len(diffs) < 2:
        return 0.0
    center = float(np.median(diffs))
    mad = float(np.median(np.abs(diffs - center)))
    if not np.isfinite(mad) or mad <= 0.0:
        return 0.0
    # Difference noise has sqrt(2) times the standard deviation of the
    # underlying point noise.  1.4826 converts MAD to Gaussian-equivalent sigma.
    return float(1.4826 * mad / np.sqrt(2.0))


def _segment_span(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if len(numeric) < 2:
        return 0.0
    q05, q95 = np.nanquantile(numeric.to_numpy(dtype=float), [0.05, 0.95])
    span = float(q95 - q05)
    return span if np.isfinite(span) and span > 0.0 else 0.0


def _trigger_deadband(
    baseline_values: pd.Series,
    baseline_flow: float,
    segment_span: float,
    settings: _DetectionSettings,
) -> tuple[float, float]:
    noise_sigma = _robust_noise_sigma(baseline_values)
    candidates = [
        settings.noise_multiplier * noise_sigma,
        settings.span_deadband_ratio * segment_span,
        settings.level_deadband_ratio * abs(float(baseline_flow)),
    ]
    finite = [float(value) for value in candidates if np.isfinite(value) and value > 0]
    deadband = max(finite) if finite else 0.0
    return deadband, noise_sigma


def _is_stable(values: pd.Series, deadband: float, noise_sigma: float) -> bool:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if len(numeric) < 2:
        return False
    stable_band = max(0.50 * float(deadband), 2.5 * float(noise_sigma))
    if stable_band <= 0.0:
        return bool(float(numeric.max() - numeric.min()) == 0.0)
    return bool(float(numeric.max() - numeric.min()) <= stable_band)


def _integrate_delta_volume(
    timestamps: pd.Series,
    values: pd.Series,
    baseline_flow: float,
) -> tuple[float, float, float]:
    """Integrate m3/h flow deviation over real timestamps, returning m3."""
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime(timestamps, errors="coerce"),
            "flow": pd.to_numeric(values, errors="coerce"),
        }
    ).dropna()
    if len(frame) < 2:
        return 0.0, 0.0, 0.0

    frame.sort_values("time", inplace=True, kind="stable")
    t_ns = pd.DatetimeIndex(frame["time"]).asi8.astype(np.float64)
    dt_hours = np.diff(t_ns) / 3.6e12
    delta = frame["flow"].to_numpy(dtype=float) - float(baseline_flow)
    if len(delta) < 2 or len(dt_hours) != len(delta) - 1:
        return 0.0, 0.0, 0.0

    positive = np.maximum(delta, 0.0)
    negative = np.maximum(-delta, 0.0)
    signed = 0.5 * (delta[:-1] + delta[1:]) * dt_hours
    extra = 0.5 * (positive[:-1] + positive[1:]) * dt_hours
    deficit = 0.5 * (negative[:-1] + negative[1:]) * dt_hours
    return float(np.sum(extra)), float(np.sum(deficit)), float(np.sum(signed))


def _build_event(
    frame: pd.DataFrame,
    *,
    timestamp_column: str,
    flow_column: str,
    tower_id: str,
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
    baseline_flow: float,
    baseline_noise_sigma: float,
    trigger_deadband: float,
    stable_minutes: float,
    transition_count: int,
    complete: bool,
) -> SupplyFlowEvent | None:
    indexer = TimeWindowIndexer(frame, timestamp_column)
    event_window = indexer.slice(start_time, end_time)
    if event_window.empty:
        return None

    values = pd.to_numeric(event_window[flow_column], errors="coerce")
    valid = values.dropna()
    if valid.empty:
        return None

    peak_flow = float(valid.max())
    trough_flow = float(valid.min())
    positive_delta = peak_flow - float(baseline_flow)
    negative_delta = trough_flow - float(baseline_flow)
    if abs(positive_delta) >= abs(negative_delta):
        peak_delta = float(positive_delta)
        extreme_value = peak_flow
    else:
        peak_delta = float(negative_delta)
        extreme_value = trough_flow

    extreme_mask = np.isclose(
        pd.to_numeric(event_window[flow_column], errors="coerce").to_numpy(dtype=float),
        extreme_value,
        equal_nan=False,
    )
    extreme_positions = np.flatnonzero(extreme_mask)
    extreme_time = (
        pd.Timestamp(event_window.iloc[int(extreme_positions[0])][timestamp_column])
        if len(extreme_positions)
        else pd.Timestamp(start_time)
    )

    final_start = pd.Timestamp(end_time) - pd.Timedelta(minutes=stable_minutes)
    final_window = indexer.slice(final_start, end_time)
    final_values = pd.to_numeric(final_window[flow_column], errors="coerce").dropna()
    if final_values.empty:
        final_flow = float(valid.iloc[-1])
    else:
        final_flow = float(final_values.median())

    extra, deficit, signed = _integrate_delta_volume(
        event_window[timestamp_column],
        event_window[flow_column],
        baseline_flow,
    )
    duration_minutes = max(
        0.0,
        (pd.Timestamp(end_time) - pd.Timestamp(start_time)).total_seconds() / 60.0,
    )
    to_extreme = max(
        0.0,
        (extreme_time - pd.Timestamp(start_time)).total_seconds() / 60.0,
    )
    from_extreme = max(
        0.0,
        (pd.Timestamp(end_time) - extreme_time).total_seconds() / 60.0,
    )

    return SupplyFlowEvent(
        tower_id=str(tower_id),
        start_time=pd.Timestamp(start_time),
        end_time=pd.Timestamp(end_time),
        baseline_flow=float(baseline_flow),
        final_flow=final_flow,
        peak_flow=peak_flow,
        trough_flow=trough_flow,
        peak_delta_flow=peak_delta,
        final_delta_flow=final_flow - float(baseline_flow),
        max_abs_delta_flow=abs(peak_delta),
        extra_slurry_volume=extra,
        deficit_slurry_volume=deficit,
        signed_slurry_volume=signed,
        active_duration_minutes=duration_minutes,
        time_to_extreme_minutes=to_extreme,
        time_from_extreme_to_end_minutes=from_extreme,
        baseline_noise_sigma=float(baseline_noise_sigma),
        trigger_deadband=float(trigger_deadband),
        transition_count=int(transition_count),
        complete=bool(complete),
    )


def _detect_tower_transitions(
    frame: pd.DataFrame,
    *,
    timestamp_column: str,
    flow_column: str,
    tower_id: str,
    settings: _DetectionSettings,
) -> list[SupplyFlowEvent]:
    if frame.empty or flow_column not in frame.columns:
        return []

    indexer = TimeWindowIndexer(frame, timestamp_column)
    timestamps = pd.DatetimeIndex(frame[timestamp_column])
    segment_span = _segment_span(frame[flow_column])
    transitions: list[SupplyFlowEvent] = []
    n = len(frame)
    i = 0

    while i < n:
        current_time = timestamps[i]
        baseline_left = indexer.left(
            current_time - pd.Timedelta(minutes=settings.baseline_minutes)
        )
        baseline_window = frame.iloc[baseline_left:i]
        baseline_values = pd.to_numeric(
            baseline_window[flow_column], errors="coerce"
        ).dropna()
        if len(baseline_values) < 3:
            i += 1
            continue

        baseline_flow = float(baseline_values.median())
        deadband, noise_sigma = _trigger_deadband(
            baseline_values,
            baseline_flow,
            segment_span,
            settings,
        )
        current_flow = pd.to_numeric(
            pd.Series([frame.iloc[i][flow_column]]), errors="coerce"
        ).iloc[0]
        if pd.isna(current_flow) or deadband <= 0.0:
            i += 1
            continue

        current_delta = float(current_flow) - baseline_flow
        if abs(current_delta) < deadband:
            i += 1
            continue

        # Reject isolated meter spikes.  Confirmation is point-based rather than
        # second-based so the same code works for different plant historian
        # sampling intervals without a per-plant timing parameter.
        sign = 1.0 if current_delta > 0 else -1.0
        confirmed = 0
        confirmation_end = min(n, i + settings.trigger_confirmation_points)
        for k in range(i, confirmation_end):
            value = pd.to_numeric(
                pd.Series([frame.iloc[k][flow_column]]), errors="coerce"
            ).iloc[0]
            if pd.isna(value):
                continue
            delta = float(value) - baseline_flow
            if sign * delta >= 0.75 * deadband:
                confirmed += 1
        if confirmed < settings.trigger_confirmation_points:
            i += 1
            continue

        # Backtrack within the existing detection horizon to recover the first
        # meaningful departure from the old baseline.
        start_idx = i
        earliest = indexer.left(
            current_time - pd.Timedelta(minutes=settings.backtrack_minutes)
        )
        for k in range(i - 1, earliest - 1, -1):
            value = pd.to_numeric(
                pd.Series([frame.iloc[k][flow_column]]), errors="coerce"
            ).iloc[0]
            if pd.isna(value):
                break
            if abs(float(value) - baseline_flow) > 0.25 * deadband:
                start_idx = k
            else:
                break

        start_time = timestamps[start_idx]
        # Recompute the event baseline before the recovered start time.
        baseline_left = indexer.left(
            start_time - pd.Timedelta(minutes=settings.baseline_minutes)
        )
        baseline_window = frame.iloc[baseline_left:start_idx]
        baseline_values = pd.to_numeric(
            baseline_window[flow_column], errors="coerce"
        ).dropna()
        if len(baseline_values) >= 3:
            baseline_flow = float(baseline_values.median())
            deadband, noise_sigma = _trigger_deadband(
                baseline_values,
                baseline_flow,
                segment_span,
                settings,
            )

        max_end = start_time + pd.Timedelta(minutes=settings.max_transition_minutes)
        j = max(i, start_idx + 1)
        found_end = False
        end_idx = j
        while j < n and timestamps[j] <= max_end:
            t = timestamps[j]
            if t - start_time < pd.Timedelta(minutes=settings.stable_minutes):
                j += 1
                continue
            stable_start = t - pd.Timedelta(minutes=settings.stable_minutes)
            stable_window = indexer.slice(stable_start, t)
            if _is_stable(stable_window[flow_column], deadband, noise_sigma):
                plateau_values = pd.to_numeric(
                    stable_window[flow_column], errors="coerce"
                ).dropna()
                if not plateau_values.empty:
                    plateau = float(plateau_values.median())
                    excursion = pd.to_numeric(
                        indexer.slice(start_time, t)[flow_column], errors="coerce"
                    ).dropna()
                    if not excursion.empty:
                        max_excursion = float(
                            np.max(np.abs(excursion.to_numpy(dtype=float) - baseline_flow))
                        )
                        # A stable new plateau or a stable return after a real
                        # excursion closes this elementary transition.
                        if max_excursion >= deadband and (
                            abs(plateau - baseline_flow) >= deadband
                            or abs(plateau - baseline_flow) <= 0.75 * deadband
                        ):
                            found_end = True
                            end_idx = j
                            break
            end_idx = j
            j += 1

        if end_idx <= start_idx:
            i += 1
            continue

        event = _build_event(
            frame,
            timestamp_column=timestamp_column,
            flow_column=flow_column,
            tower_id=tower_id,
            start_time=start_time,
            end_time=timestamps[end_idx],
            baseline_flow=baseline_flow,
            baseline_noise_sigma=noise_sigma,
            trigger_deadband=deadband,
            stable_minutes=settings.stable_minutes,
            transition_count=1,
            complete=found_end,
        )
        if event is not None and event.max_abs_delta_flow >= deadband:
            transitions.append(event)
            i = end_idx + 1
        else:
            i += 1

    return transitions


def _merge_nearby_transitions(
    frame: pd.DataFrame,
    *,
    timestamp_column: str,
    flow_column: str,
    transitions: list[SupplyFlowEvent],
    settings: _DetectionSettings,
) -> list[SupplyFlowEvent]:
    if not transitions:
        return []

    ordered = sorted(transitions, key=lambda item: item.start_time)
    merged: list[SupplyFlowEvent] = []
    current_group: list[SupplyFlowEvent] = [ordered[0]]
    merge_gap = pd.Timedelta(minutes=settings.trajectory_merge_gap_minutes)

    def flush(group: list[SupplyFlowEvent]) -> None:
        first = group[0]
        last = group[-1]
        rebuilt = _build_event(
            frame,
            timestamp_column=timestamp_column,
            flow_column=flow_column,
            tower_id=first.tower_id,
            start_time=first.start_time,
            end_time=last.end_time,
            baseline_flow=first.baseline_flow,
            baseline_noise_sigma=first.baseline_noise_sigma,
            trigger_deadband=first.trigger_deadband,
            stable_minutes=settings.stable_minutes,
            transition_count=len(group),
            complete=all(item.complete for item in group),
        )
        if rebuilt is not None:
            merged.append(rebuilt)

    for transition in ordered[1:]:
        previous = current_group[-1]
        gap = transition.start_time - previous.end_time
        if pd.Timedelta(0) <= gap <= merge_gap:
            current_group.append(transition)
        else:
            flush(current_group)
            current_group = [transition]
    flush(current_group)
    return merged


def detect_supply_flow_events(
    df: pd.DataFrame,
    plant: dict[str, Any],
    training: dict[str, Any],
    progress: Callable[[float, str], None] | None = None,
) -> list[SupplyFlowEvent]:
    """Detect actual slurry-flow trajectory events without classifying shape.

    The function is intentionally not wired into ``episode_extractor`` in Batch
    2A.  It runs alongside the legacy valve detector so historical segmentation
    can be reviewed before the policy semantics are switched.
    """
    if df.empty:
        if progress:
            progress(1.0, "没有可检测的供浆流量数据")
        return []

    settings = _settings(training)
    ts_col = time_column(plant)
    segment_key = "continuous_segment_id"
    segments = list(df.groupby(segment_key, sort=False)) if segment_key in df.columns else [(0, df)]
    towers = enabled_towers(plant)
    total_jobs = max(1, len(segments) * len(towers))
    completed_jobs = 0
    events: list[SupplyFlowEvent] = []

    for _, segment in segments:
        segment = segment.sort_values(ts_col, kind="stable").reset_index(drop=True)
        for tower in towers:
            tower_id = str(tower["tower_id"])
            flow_col = clean_supply_flow_column(tower_id)
            if flow_col not in segment.columns:
                completed_jobs += 1
                continue
            transitions = _detect_tower_transitions(
                segment,
                timestamp_column=ts_col,
                flow_column=flow_col,
                tower_id=tower_id,
                settings=settings,
            )
            events.extend(
                _merge_nearby_transitions(
                    segment,
                    timestamp_column=ts_col,
                    flow_column=flow_col,
                    transitions=transitions,
                    settings=settings,
                )
            )
            completed_jobs += 1
            if progress:
                progress(
                    completed_jobs / total_jobs,
                    f"供浆流量事件切分 {completed_jobs}/{total_jobs}，已发现 {len(events)} 个",
                )

    events.sort(key=lambda item: (item.start_time, item.tower_id))
    if progress:
        progress(1.0, f"供浆流量事件切分完成，共发现 {len(events)} 个轨迹事件")
    return events


def supply_flow_events_to_frame(events: list[SupplyFlowEvent]) -> pd.DataFrame:
    """Convert events to an audit-friendly DataFrame for manual review."""
    if not events:
        return pd.DataFrame(columns=list(SupplyFlowEvent.__dataclass_fields__))
    return pd.DataFrame([event.__dict__ for event in events])
