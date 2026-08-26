# -*- coding: utf-8 -*-
"""Adapt Scheme-1 supply-flow episodes into Scheme-2 MFAC learning events.

The existing slurry-policy engine already performs robust actual-flow event
segmentation and SO2/pH effect profiling.  Scheme 2 reuses that physical event
extraction and applies a stricter attribution gate instead of maintaining a
second detector with drifting semantics.
"""

import math
from typing import Any, Mapping, Optional

import pandas as pd

from system.model.config.standard_fields import TARGET_SO2_COLUMN, TIME_COLUMN

from .context_resolver import MFACContextResolver
from .mfac_eligibility import StrictMFACEligibilityGate
from .mfac_schema import ActionResponseEvent


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


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


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _time_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        timestamp = pd.Timestamp(value)
    except Exception:
        return ""
    if pd.isna(timestamp):
        return ""
    return timestamp.isoformat()


def _window(
    history: Optional[pd.DataFrame],
    start: Any,
    end: Any,
) -> pd.DataFrame:
    if history is None or history.empty or TIME_COLUMN not in history.columns:
        return pd.DataFrame()
    try:
        left = pd.Timestamp(start)
        right = pd.Timestamp(end)
    except Exception:
        return pd.DataFrame()
    timestamps = pd.to_datetime(history[TIME_COLUMN], errors="coerce")
    return history.loc[(timestamps >= left) & (timestamps <= right)].copy()


def _numeric_values(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty or not column or column not in frame.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").replace(
        [float("inf"), float("-inf")], float("nan")
    ).dropna()


def _median(frame: pd.DataFrame, column: str) -> Optional[float]:
    values = _numeric_values(frame, column)
    return float(values.median()) if not values.empty else None


def _range(frame: pd.DataFrame, column: str) -> Optional[float]:
    values = _numeric_values(frame, column)
    if values.empty:
        return None
    return float(values.max() - values.min())


class Scheme1EpisodeToMFACAdapter:
    """Convert one Scheme-1 episode row into an auditable MFAC event."""

    def __init__(
        self,
        resolver: MFACContextResolver,
        gate: StrictMFACEligibilityGate,
        *,
        inlet_so2_column: Optional[str] = None,
        load_column: Optional[str] = None,
        qbase_column: Optional[str] = None,
        target_column: str = TARGET_SO2_COLUMN,
        target_change_tolerance: float = 1e-9,
    ) -> None:
        self.resolver = resolver
        self.gate = gate
        self.inlet_so2_column = str(inlet_so2_column or "").strip()
        self.load_column = str(load_column or "").strip()
        self.qbase_column = str(qbase_column or "").strip()
        self.target_column = str(target_column or TARGET_SO2_COLUMN).strip()
        self.target_change_tolerance = max(0.0, float(target_change_tolerance))

    def adapt(
        self,
        episode: Mapping[str, Any],
        *,
        history: Optional[pd.DataFrame] = None,
    ) -> ActionResponseEvent:
        row = dict(episode)
        resolution = self.resolver.resolve(row)

        action_start = row.get("action_start_time")
        action_end = row.get("action_end_time")
        response_start = row.get("flow_effect_response_start_time")
        response_end = row.get("response_end_time")
        full_window = _window(history, action_start, response_end)

        context_available, context_changed = self._context_stability(full_window)
        target_available, target_changed, target_value = self._target_stability(
            full_window
        )
        qbase_available, qbase_before, qbase_after, qbase_drift = self._qbase(
            history,
            row,
        )

        fast_overlap = self._fast_overlap(full_window, row)
        equipment_changed = any(
            (
                _bool(row.get("flow_circulation_change"), False),
                _bool(row.get("flow_major_process_transition"), False),
                _bool(row.get("supply_pump_state_changed"), False),
            )
        )

        delta_q = _finite(row.get("flow_event_final_delta_flow"))
        delta_so2 = _finite(row.get("delta_outlet_so2"))
        inlet_change = (
            _range(full_window, self.inlet_so2_column)
            if self.inlet_so2_column
            else None
        )
        load_change = (
            _range(full_window, self.load_column)
            if self.load_column
            else None
        )

        evidence = {
            "flow_shape": row.get("flow_shape"),
            "flow_disturbance_class": row.get("flow_disturbance_class"),
            "scheme1_valid": _bool(row.get("valid"), False),
            "effect_complete": _bool(row.get("flow_effect_complete"), False),
            "flow_context_eligible": _bool(
                row.get("flow_learning_eligible"), False
            ),
            "followup_action_in_response": _bool(
                row.get("followup_action_in_response"), False
            ),
            "circulation_changed": _bool(
                row.get("flow_circulation_change"), False
            ),
            "major_process_transition": _bool(
                row.get("flow_major_process_transition"), False
            ),
            "equipment_changed": equipment_changed,
            "context_stability_evidence_available": context_available,
            "condition_context_changed": context_changed,
            "target_evidence_available": target_available,
            "target_changed": target_changed,
            "qbase_evidence_available": qbase_available,
            "qbase_before": qbase_before,
            "qbase_after": qbase_after,
            "qbase_drift": qbase_drift,
            "delta_q_actual": delta_q,
            "delta_so2": delta_so2,
        }
        decision = self.gate.evaluate(evidence)
        phi_event = _finite(decision.metrics.get("phi_event"))

        tower_id = _text(row.get("active_tower_ids")) or _text(
            row.get("flow_event_tower_id")
        )
        ph_before = _finite(row.get(f"before_ph__{tower_id}")) if tower_id else None
        ph_after = _finite(row.get(f"after_ph__{tower_id}")) if tower_id else None
        delta_ph = _finite(row.get(f"delta_ph__{tower_id}")) if tower_id else None

        reject_reason = "|".join(decision.reasons)
        event_id = _text(row.get("episode_id"))
        if not event_id:
            event_id = (
                f"MFAC-{resolution.mfac_context_id}-"
                f"{_time_text(action_start) or 'UNKNOWN'}"
            )

        metadata = {
            "scheme1_episode_id": _text(row.get("episode_id")),
            "scheme1_invalid_reason": _text(row.get("invalid_reason")),
            "scheme1_training_route": _text(row.get("training_route")),
            "flow_shape": _text(row.get("flow_shape")),
            "flow_direction": _text(row.get("flow_direction")),
            "flow_disturbance_class": _text(
                row.get("flow_disturbance_class")
            ),
            "flow_disturbance_state": _text(
                row.get("flow_disturbance_state")
            ),
            "eligibility_decision": decision.to_dict(),
            "observed_response_delay_minutes": _finite(
                row.get("flow_timing_observed_response_delay_minutes")
            ),
            "time_to_stable_minutes": _finite(
                row.get("flow_timing_time_to_stable_minutes")
            ),
            "qbase_column": self.qbase_column,
            "target_column": self.target_column,
        }

        return ActionResponseEvent(
            event_id=event_id,
            condition_snapshot_version=resolution.condition_snapshot_version,
            condition_label=resolution.condition_label,
            base_condition_id=resolution.base_condition_id,
            grid_id=resolution.grid_id,
            policy_region_id=resolution.policy_region_id,
            mfac_context_id=resolution.mfac_context_id,
            action_start_time=_time_text(action_start),
            action_reached_time=_time_text(action_end),
            response_start_time=_time_text(response_start),
            response_end_time=_time_text(response_end),
            action_source="HISTORICAL_ACTUAL_SUPPLY_FLOW",
            q_before=_finite(row.get("flow_event_baseline_flow")),
            q_after=_finite(row.get("flow_event_final_flow")),
            delta_q_actual=delta_q,
            qbase_before=qbase_before,
            qbase_after=qbase_after,
            qbase_drift=qbase_drift,
            so2_target=target_value,
            so2_before=_finite(row.get("before_outlet_so2")),
            so2_after=_finite(row.get("after_outlet_so2")),
            delta_so2=delta_so2,
            ph_before=ph_before,
            ph_after=ph_after,
            delta_ph=delta_ph,
            inlet_so2_change=inlet_change,
            load_change=load_change,
            fast_overlap=fast_overlap,
            equipment_changed=equipment_changed,
            target_changed=target_changed,
            condition_changed=context_changed,
            data_quality_ok=bool(
                _bool(row.get("flow_effect_complete"), False)
                and _bool(row.get("condition_valid"), False)
            ),
            learning_eligible=decision.eligible,
            reject_reason=reject_reason,
            phi_event=phi_event,
            quality_score=None,
            metadata=metadata,
        )

    def _context_stability(self, window: pd.DataFrame) -> tuple[bool, bool]:
        required = {
            "condition_snapshot_version",
            "condition_label",
            "base_condition_id",
        }
        if window.empty or not required.issubset(window.columns):
            return False, False
        context_ids: set[str] = set()
        for record in window.to_dict(orient="records"):
            try:
                context_ids.add(self.resolver.resolve(record).mfac_context_id)
            except (KeyError, TypeError, ValueError):
                return False, False
        return bool(context_ids), len(context_ids) > 1

    def _target_stability(
        self,
        window: pd.DataFrame,
    ) -> tuple[bool, bool, Optional[float]]:
        values = _numeric_values(window, self.target_column)
        if values.empty:
            return False, False, None
        changed = float(values.max() - values.min()) > self.target_change_tolerance
        return True, changed, float(values.median())

    def _qbase(
        self,
        history: Optional[pd.DataFrame],
        row: Mapping[str, Any],
    ) -> tuple[bool, Optional[float], Optional[float], Optional[float]]:
        if (
            history is None
            or history.empty
            or not self.qbase_column
            or self.qbase_column not in history.columns
        ):
            return False, None, None, None

        baseline_start = row.get("flow_effect_baseline_start_time")
        action_start = row.get("action_start_time")
        response_start = row.get("flow_effect_response_start_time")
        response_end = row.get("response_end_time")
        before = _window(history, baseline_start, action_start)
        after = _window(history, response_start, response_end)
        qbase_before = _median(before, self.qbase_column)
        qbase_after = _median(after, self.qbase_column)
        if qbase_before is None or qbase_after is None:
            return False, qbase_before, qbase_after, None
        return True, qbase_before, qbase_after, qbase_after - qbase_before

    @staticmethod
    def _fast_overlap(window: pd.DataFrame, row: Mapping[str, Any]) -> bool:
        if not window.empty and "fast_change_mode" in window.columns:
            modes = {
                str(value).strip().upper()
                for value in window["fast_change_mode"].dropna()
            }
            if modes & {"FAST_CHANGE", "FAST_RECOVERY"}:
                return True
        disturbance = str(row.get("flow_disturbance_class", "")).strip().upper()
        return disturbance in {"FAST", "RECOVERY"}


def adapt_episode_frame(
    episodes: pd.DataFrame,
    adapter: Scheme1EpisodeToMFACAdapter,
    *,
    history: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Adapt all Scheme-1 episode rows while preserving rejected events."""
    if episodes.empty:
        return pd.DataFrame()
    records = [
        adapter.adapt(row, history=history).to_dict()
        for row in episodes.to_dict(orient="records")
    ]
    return pd.DataFrame(records)
