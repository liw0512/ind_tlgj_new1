# -*- coding: utf-8 -*-
"""Adapt canonical HistoricalEpisodeEngine output for model-based LOCAL_GAIN.

The adapter does not reclassify large operator pulses as direct LOCAL_GAIN.
It selects reviewed-quality DYNAMIC evidence and builds an event-level frame for
``model_based_local_gain_trainer``.  This keeps event detection/condition
binding in the canonical historical engine and makes the model-based route
reproducible instead of depending on one-off CSV segmentation scripts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

import pandas as pd

from .context_resolver import MFACContextResolver


HISTORICAL_MODEL_BASED_GAIN_ADAPTER_VERSION = (
    "SCHEME2_HISTORICAL_MODEL_BASED_GAIN_ADAPTER_V1_CANONICAL_EPISODES"
)


@dataclass(frozen=True)
class HistoricalModelBasedGainAdapterConfig:
    tower_id: str
    allowed_shapes: Tuple[str, ...] = ("STEP", "PULSE", "BOOST_STEP")
    require_condition_valid: bool = True
    reject_safety_evidence: bool = True
    reject_followup_action: bool = True
    reject_condition_remap: bool = True

    def __post_init__(self) -> None:
        if not str(self.tower_id or "").strip():
            raise ValueError("tower_id is required")
        if not self.allowed_shapes:
            raise ValueError("allowed_shapes cannot be empty")


@dataclass(frozen=True)
class HistoricalModelBasedGainAdaptationSummary:
    input_episode_count: int
    accepted_event_count: int
    rejected_event_count: int
    rejection_counts: Dict[str, int]
    semantics_version: str = HISTORICAL_MODEL_BASED_GAIN_ADAPTER_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "是"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _numeric(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if pd.notna(number) else None


def _reject_reason(
    row: Mapping[str, Any],
    config: HistoricalModelBasedGainAdapterConfig,
) -> str:
    if not _bool(row.get("valid"), False):
        return "EPISODE_INVALID"
    if not _bool(row.get("flow_effect_complete"), False):
        return "FLOW_EFFECT_INCOMPLETE"
    if not _bool(row.get("mfac_dynamic_evidence_eligible"), False):
        return "NOT_DYNAMIC_EVIDENCE"
    if config.reject_safety_evidence and _bool(row.get("mfac_safety_evidence"), False):
        return "SAFETY_EVIDENCE_EXCLUDED_FROM_LOCAL_GAIN_MODEL"
    if config.require_condition_valid and not _bool(row.get("condition_valid"), False):
        return "CONDITION_INVALID"
    if config.reject_followup_action and _bool(row.get("followup_action_in_response"), False):
        return "FOLLOWUP_ACTION_IN_RESPONSE"
    if config.reject_condition_remap and (
        _bool(row.get("condition_remapped"), False)
        or int(_numeric(row.get("grid_change_count")) or 0) > 0
        or int(_numeric(row.get("condition_label_change_count")) or 0) > 0
    ):
        return "CONDITION_OR_GRID_CHANGED_DURING_EVENT"
    shape = _text(row.get("flow_shape")).upper()
    if shape not in {item.upper() for item in config.allowed_shapes}:
        return "FLOW_SHAPE_NOT_ALLOWED"
    required_numeric = {
        "flow_event_final_delta_flow": row.get("flow_event_final_delta_flow"),
        "delta_outlet_so2": row.get("delta_outlet_so2"),
        "before_condition_axis_1": row.get("before_condition_axis_1"),
        "before_outlet_so2": row.get("before_outlet_so2"),
        "flow_event_active_duration_minutes": row.get("flow_event_active_duration_minutes"),
        "before_condition_axis_1_rate": row.get("before_condition_axis_1_rate"),
        "before_outlet_so2_rate": row.get("before_outlet_so2_rate"),
        "flow_event_extra_slurry_volume": row.get("flow_event_extra_slurry_volume"),
        "before_ph": row.get("before_ph__%s" % config.tower_id),
        "delta_ph": row.get("delta_ph__%s" % config.tower_id),
    }
    if any(_numeric(value) is None for value in required_numeric.values()):
        return "REQUIRED_MODEL_FIELD_MISSING"
    if abs(float(required_numeric["flow_event_final_delta_flow"])) <= 1e-12:
        return "DELTA_Q_ZERO"
    snapshot = _text(row.get("condition_snapshot_version"))
    condition_label = _text(row.get("condition_label"))
    grid_id = _text(row.get("anchor_grid_id")) or _text(row.get("start_grid_id"))
    if not snapshot or not condition_label or not grid_id:
        return "CONDITION_BINDING_MISSING"
    return ""


def adapt_historical_episodes_for_model_based_gain(
    episodes: pd.DataFrame,
    config: HistoricalModelBasedGainAdapterConfig,
) -> tuple[pd.DataFrame, HistoricalModelBasedGainAdaptationSummary]:
    """Build the canonical event-level training frame plus rejection audit."""
    records = []
    rejection_counts: Dict[str, int] = {}
    resolver_cache: Dict[str, MFACContextResolver] = {}

    for row in episodes.to_dict(orient="records"):
        reason = _reject_reason(row, config)
        if reason:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            continue

        snapshot = _text(row.get("condition_snapshot_version"))
        resolver = resolver_cache.get(snapshot)
        if resolver is None:
            resolver = MFACContextResolver(snapshot)
            resolver_cache[snapshot] = resolver
        context = resolver.resolve(row)
        grid_id = _text(row.get("anchor_grid_id")) or _text(row.get("start_grid_id"))
        records.append(
            {
                "event_id": _text(row.get("episode_id")),
                "event_time": _text(row.get("action_start_time")),
                "condition_snapshot_version": snapshot,
                "condition_label": context.condition_label,
                "base_condition_id": context.base_condition_id,
                "grid_id": grid_id,
                "mfac_context_id": context.mfac_context_id,
                "flow_shape": _text(row.get("flow_shape")).upper(),
                "delta_q": float(row["flow_event_final_delta_flow"]),
                "so2_response": float(row["delta_outlet_so2"]),
                "ph_response": float(row["delta_ph__%s" % config.tower_id]),
                "inlet0": float(row["before_condition_axis_1"]),
                "ph0": float(row["before_ph__%s" % config.tower_id]),
                "out0": float(row["before_outlet_so2"]),
                "duration_s": float(row["flow_event_active_duration_minutes"]) * 60.0,
                "inlet_pretrend": float(row["before_condition_axis_1_rate"]),
                "so2_pretrend": float(row["before_outlet_so2_rate"]),
                "extra_volume_m3": float(row["flow_event_extra_slurry_volume"]),
                "evidence_weight": float(_numeric(row.get("evidence_weight")) or 1.0),
                "adapter_semantics_version": HISTORICAL_MODEL_BASED_GAIN_ADAPTER_VERSION,
            }
        )

    frame = pd.DataFrame(records)
    if not frame.empty:
        frame["event_time"] = pd.to_datetime(frame["event_time"], errors="coerce")
        frame = frame.dropna(subset=["event_time"]).sort_values(
            ["condition_snapshot_version", "mfac_context_id", "event_time"],
            kind="stable",
        ).reset_index(drop=True)

    accepted = int(len(frame))
    summary = HistoricalModelBasedGainAdaptationSummary(
        input_episode_count=int(len(episodes)),
        accepted_event_count=accepted,
        rejected_event_count=int(len(episodes) - accepted),
        rejection_counts=dict(sorted(rejection_counts.items())),
    )
    return frame, summary


__all__ = [
    "HISTORICAL_MODEL_BASED_GAIN_ADAPTER_VERSION",
    "HistoricalModelBasedGainAdapterConfig",
    "HistoricalModelBasedGainAdaptationSummary",
    "adapt_historical_episodes_for_model_based_gain",
]
