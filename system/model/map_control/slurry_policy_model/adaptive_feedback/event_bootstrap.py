from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

import numpy as np
import pandas as pd

from .config import InitialTrainingConfig


@dataclass(frozen=True)
class ActionEvent:
    event_id: str
    segment_id: int
    action_index: int
    action_end_index: int
    action_time: pd.Timestamp
    q_before_m3h: float
    q_after_m3h: float
    delta_q_actual_m3h: float
    direction: str
    condition_label: str
    quality_grade: str
    event_weight: float
    learnable: bool
    rejection_reason: str
    inlet_so2_change_mg_nm3: float
    gas_relative_change: float
    next_action_seconds: float

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "segment_id": self.segment_id,
            "action_index": self.action_index,
            "action_end_index": self.action_end_index,
            "action_time": self.action_time,
            "q_before_m3h": self.q_before_m3h,
            "q_after_m3h": self.q_after_m3h,
            "delta_q_actual_m3h": self.delta_q_actual_m3h,
            "direction": self.direction,
            "condition_label": self.condition_label,
            "quality_grade": self.quality_grade,
            "event_weight": self.event_weight,
            "learnable": self.learnable,
            "rejection_reason": self.rejection_reason,
            "inlet_so2_change_mg_nm3": self.inlet_so2_change_mg_nm3,
            "gas_relative_change": self.gas_relative_change,
            "next_action_seconds": self.next_action_seconds,
        }


def prepare_response_frame(frame: pd.DataFrame, config: InitialTrainingConfig) -> pd.DataFrame:
    required = (
        config.timestamp_column,
        config.actual_flow_column,
        config.inlet_so2_column,
        config.gas_flow_column,
        config.outlet_so2_column,
        config.ph_column,
    )
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise KeyError("missing response columns: %s" % ", ".join(missing))

    retained = list(required)
    if config.condition_column in frame.columns:
        retained.append(config.condition_column)
    for column in config.topology_columns:
        if column in frame.columns and column not in retained:
            retained.append(column)

    work = frame.loc[:, retained].copy()
    work["timestamp"] = pd.to_datetime(work[config.timestamp_column], errors="coerce")
    for column in (
        config.actual_flow_column,
        config.inlet_so2_column,
        config.gas_flow_column,
        config.outlet_so2_column,
        config.ph_column,
    ):
        work[column] = pd.to_numeric(work[column], errors="coerce")
    work = work.dropna(subset=["timestamp"]).sort_values("timestamp", kind="stable").reset_index(drop=True)

    delta_seconds = work["timestamp"].diff().dt.total_seconds()
    max_gap = float(config.sample_seconds) * float(config.max_gap_multiple)
    starts = delta_seconds.isna() | (delta_seconds <= 0.0) | (delta_seconds > max_gap)
    work["continuous_segment_id"] = starts.cumsum().astype(int) - 1
    if config.condition_column not in work.columns:
        work[config.condition_column] = "GLOBAL_ONLY"
    else:
        work[config.condition_column] = work[config.condition_column].fillna("UNKNOWN").astype(str)
    return work


def _median(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.median()) if not values.empty else float("nan")


def _stable_label(series: pd.Series) -> Tuple[str, bool]:
    clean = series.dropna().astype(str)
    if clean.empty:
        return "UNKNOWN", False
    counts = clean.value_counts()
    return str(counts.index[0]), bool(len(counts) == 1)


def _topology_stable(work: pd.DataFrame, columns: Sequence[str], start: int, end: int) -> bool:
    for column in columns:
        if column not in work.columns:
            continue
        values = work.iloc[start:end][column].dropna().astype(str)
        if not values.empty and values.nunique() > 1:
            return False
    return True


def _cluster_candidates(indices: Sequence[int], max_gap_steps: int) -> List[Tuple[int, int]]:
    if not indices:
        return []
    clusters: List[Tuple[int, int]] = []
    start = previous = int(indices[0])
    for raw in indices[1:]:
        current = int(raw)
        if current - previous <= max_gap_steps:
            previous = current
            continue
        clusters.append((start, previous))
        start = previous = current
    clusters.append((start, previous))
    return clusters


def extract_action_events(
    frame: pd.DataFrame,
    config: InitialTrainingConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Extract actual-flow actions with A/B/rejected evidence grades.

    Sparse local history is not treated as a reason to disable control. These
    grades only determine how strongly an event contributes to offline response
    knowledge.
    """

    work = prepare_response_frame(frame, config)
    flow = work[config.actual_flow_column]
    candidate_mask = flow.diff().abs() >= float(config.candidate_flow_diff_m3h)
    candidate_indices = list(np.flatnonzero(candidate_mask.fillna(False).to_numpy()))
    cluster_steps = max(int(round(config.action_cluster_seconds / float(config.sample_seconds))), 1)
    clusters = _cluster_candidates(candidate_indices, cluster_steps)
    pre_steps = max(int(round(config.action_pre_seconds / float(config.sample_seconds))), 1)
    post_steps = max(int(round(config.action_post_seconds / float(config.sample_seconds))), 1)

    events: List[ActionEvent] = []
    for cluster_number, (start, end) in enumerate(clusters, start=1):
        segment_id = int(work.loc[start, "continuous_segment_id"])
        pre_start = start - pre_steps
        post_start = end + 1
        post_end = post_start + post_steps
        reasons: List[str] = []

        if pre_start < 0 or post_end > len(work):
            reasons.append("INCOMPLETE_PRE_POST_WINDOW")
        elif (
            int(work.loc[pre_start, "continuous_segment_id"]) != segment_id
            or int(work.loc[post_end - 1, "continuous_segment_id"]) != segment_id
        ):
            reasons.append("CROSSES_DATA_GAP")

        if reasons:
            q_before = q_after = delta_q = float("nan")
            condition_label = "UNKNOWN"
            condition_stable = False
            inlet_change = gas_relative_change = float("nan")
            topology_stable = False
        else:
            q_before = _median(work.iloc[pre_start:start][config.actual_flow_column])
            q_after = _median(work.iloc[post_start:post_end][config.actual_flow_column])
            delta_q = q_after - q_before
            condition_label, condition_stable = _stable_label(
                work.iloc[pre_start:post_end][config.condition_column]
            )
            inlet_before = _median(work.iloc[pre_start:start][config.inlet_so2_column])
            inlet_after = _median(work.iloc[post_start:post_end][config.inlet_so2_column])
            gas_before = _median(work.iloc[pre_start:start][config.gas_flow_column])
            gas_after = _median(work.iloc[post_start:post_end][config.gas_flow_column])
            inlet_change = inlet_after - inlet_before
            gas_relative_change = (
                (gas_after - gas_before) / max(abs(gas_before), 1e-9)
                if np.isfinite(gas_before) and np.isfinite(gas_after)
                else float("nan")
            )
            topology_stable = _topology_stable(work, config.topology_columns, pre_start, post_end)
            if not np.isfinite(delta_q) or abs(delta_q) < config.min_action_delta_m3h:
                reasons.append("ACTION_DELTA_TOO_SMALL")
            if not condition_stable and condition_label != "GLOBAL_ONLY":
                reasons.append("CONDITION_CHANGED")
            if not topology_stable:
                reasons.append("TOPOLOGY_CHANGED")

        next_action_seconds = float("inf")
        if cluster_number < len(clusters):
            next_start = clusters[cluster_number][0]
            if int(work.loc[next_start, "continuous_segment_id"]) == segment_id:
                next_action_seconds = float(
                    (work.loc[next_start, "timestamp"] - work.loc[start, "timestamp"]).total_seconds()
                )
        if next_action_seconds < config.action_refractory_seconds:
            reasons.append("OVERLAPPING_ACTION")

        strict_disturbance = (
            np.isfinite(inlet_change)
            and abs(inlet_change) <= config.grade_a_inlet_so2_change
            and np.isfinite(gas_relative_change)
            and abs(gas_relative_change) <= config.grade_a_gas_relative_change
        )
        medium_disturbance = (
            np.isfinite(inlet_change)
            and abs(inlet_change) <= config.grade_b_inlet_so2_change
            and np.isfinite(gas_relative_change)
            and abs(gas_relative_change) <= config.grade_b_gas_relative_change
        )

        if reasons:
            grade, weight, learnable = "REJECT", 0.0, False
        elif strict_disturbance and next_action_seconds >= config.max_response_horizon_seconds:
            grade, weight, learnable = "A", config.grade_a_weight, True
        elif medium_disturbance:
            grade, weight, learnable = "B", config.grade_b_weight, True
        else:
            grade, weight, learnable = "REJECT", 0.0, False
            reasons.append("DISTURBANCE_TOO_LARGE")

        direction = (
            "INCREASE" if np.isfinite(delta_q) and delta_q > 0.0
            else "DECREASE" if np.isfinite(delta_q) and delta_q < 0.0
            else "UNKNOWN"
        )
        events.append(
            ActionEvent(
                event_id="EVT%06d" % cluster_number,
                segment_id=segment_id,
                action_index=int(start),
                action_end_index=int(end),
                action_time=work.loc[start, "timestamp"],
                q_before_m3h=float(q_before),
                q_after_m3h=float(q_after),
                delta_q_actual_m3h=float(delta_q),
                direction=direction,
                condition_label=condition_label,
                quality_grade=grade,
                event_weight=float(weight),
                learnable=bool(learnable),
                rejection_reason=";".join(reasons),
                inlet_so2_change_mg_nm3=float(inlet_change),
                gas_relative_change=float(gas_relative_change),
                next_action_seconds=float(next_action_seconds),
            )
        )

    return pd.DataFrame([event.to_dict() for event in events]), work
