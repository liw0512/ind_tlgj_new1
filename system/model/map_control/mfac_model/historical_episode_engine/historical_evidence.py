from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from .schema import time_column


HISTORICAL_EVIDENCE_SEMANTICS_VERSION = "SCHEME2_HISTORICAL_EVIDENCE_V1"
LOCAL_GAIN_EVIDENCE = "LOCAL_GAIN"
DYNAMIC_EVIDENCE = "DYNAMIC"
SAFETY_EVIDENCE = "SAFETY"
DEFAULT_DOSE_HORIZONS_MINUTES = (3, 5, 10, 20, 30)
DEFAULT_RESPONSE_HORIZON_MINUTES = 60


def _finite(value: Any) -> float | None:
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


def _tower(plant: Mapping[str, Any], tower_id: str) -> dict[str, Any]:
    matches = [
        dict(item)
        for item in plant.get("towers", []) or []
        if item.get("enabled", True) and str(item.get("tower_id")) == str(tower_id)
    ]
    if len(matches) != 1:
        raise ValueError("historical evidence requires exactly one matching enabled tower")
    return matches[0]


def _flow_column(tower: Mapping[str, Any]) -> str:
    values = [
        str(item.get("column") or "").strip()
        for item in tower.get("supply_flows", []) or []
        if str(item.get("column") or "").strip()
    ]
    if len(values) != 1:
        raise ValueError("historical evidence requires exactly one supply-flow feedback column per tower")
    return values[0]


def _slice(
    history: pd.DataFrame,
    timestamp_column: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    if history.empty or timestamp_column not in history.columns:
        return pd.DataFrame()
    timestamps = pd.to_datetime(history[timestamp_column], errors="coerce")
    return history.loc[(timestamps >= start) & (timestamps <= end)].copy()


def _integrate_positive_delta_volume(
    frame: pd.DataFrame,
    timestamp_column: str,
    flow_column: str,
    baseline_flow: float,
) -> float | None:
    if frame.empty or timestamp_column not in frame.columns or flow_column not in frame.columns:
        return None
    work = pd.DataFrame(
        {
            "time": pd.to_datetime(frame[timestamp_column], errors="coerce"),
            "flow": pd.to_numeric(frame[flow_column], errors="coerce"),
        }
    ).dropna()
    if len(work) < 2:
        return None
    work.sort_values("time", inplace=True, kind="stable")
    t_ns = pd.DatetimeIndex(work["time"]).asi8.astype(np.float64)
    dt_hours = np.diff(t_ns) / 3.6e12
    values = work["flow"].to_numpy(dtype=float)
    delta = np.maximum(values - float(baseline_flow), 0.0)
    if len(delta) < 2 or len(dt_hours) != len(delta) - 1:
        return None
    volume = 0.5 * (delta[:-1] + delta[1:]) * dt_hours
    return float(np.sum(volume))


def _duration_above(
    frame: pd.DataFrame,
    timestamp_column: str,
    value_column: str,
    threshold: float,
) -> float | None:
    if frame.empty or timestamp_column not in frame.columns or value_column not in frame.columns:
        return None
    work = pd.DataFrame(
        {
            "time": pd.to_datetime(frame[timestamp_column], errors="coerce"),
            "value": pd.to_numeric(frame[value_column], errors="coerce"),
        }
    ).dropna()
    if len(work) < 2:
        return None
    work.sort_values("time", inplace=True, kind="stable")
    times = pd.DatetimeIndex(work["time"])
    values = work["value"].to_numpy(dtype=float)
    dt_seconds = np.diff(times.asi8.astype(np.float64)) / 1e9
    active = values[:-1] > float(threshold)
    return float(np.sum(dt_seconds[active]) / 60.0)


@dataclass(frozen=True)
class HistoricalEvidenceRoutingConfig:
    """Offline-only routing policy for historical actual-flow events.

    Magnitude limits for LOCAL_GAIN intentionally have no defaults.  Historical
    pulses and large steps can still provide DYNAMIC/SAFETY evidence, but they
    cannot seed local MFAC sensitivity until a reviewed small-step envelope is
    supplied explicitly.
    """

    max_local_abs_delta_q: float | None = None
    max_local_extra_slurry_volume: float | None = None
    require_dual_physical_direction: bool = True
    require_ph_inside_operating_range: bool = True
    local_shapes: tuple[str, ...] = ("STEP",)
    dynamic_shapes: tuple[str, ...] = ("STEP", "PULSE", "BOOST_STEP")

    def __post_init__(self) -> None:
        for name in ("max_local_abs_delta_q", "max_local_extra_slurry_volume"):
            value = getattr(self, name)
            if value is None:
                continue
            parsed = _finite(value)
            if parsed is None or parsed <= 0.0:
                raise ValueError("%s must be finite and > 0 when configured" % name)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "HistoricalEvidenceRoutingConfig":
        payload = dict(value or {})
        if "local_shapes" in payload:
            payload["local_shapes"] = tuple(payload["local_shapes"])
        if "dynamic_shapes" in payload:
            payload["dynamic_shapes"] = tuple(payload["dynamic_shapes"])
        return cls(**payload)


@dataclass(frozen=True)
class HistoricalEvidenceDecision:
    roles: tuple[str, ...]
    local_gain_eligible: bool
    dynamic_eligible: bool
    safety_evidence: bool
    reasons: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
    semantics_version: str = HISTORICAL_EVIDENCE_SEMANTICS_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["roles"] = list(self.roles)
        value["reasons"] = list(self.reasons)
        return value


def _role_decision(
    episode: Mapping[str, Any],
    plant: Mapping[str, Any],
    config: HistoricalEvidenceRoutingConfig,
) -> HistoricalEvidenceDecision:
    row = dict(episode)
    tower_id = _text(row.get("active_tower_ids")) or _text(row.get("flow_event_tower_id"))
    tower = _tower(plant, tower_id)
    shape = _text(row.get("flow_shape")).upper()
    valid = _bool(row.get("valid"), False)
    complete = _bool(row.get("flow_effect_complete"), False)
    delta_q = _finite(row.get("flow_event_final_delta_flow"))
    delta_so2 = _finite(row.get("delta_outlet_so2"))
    delta_ph = _finite(row.get(f"delta_ph__{tower_id}"))
    total_extra = _finite(row.get("flow_event_extra_slurry_volume"))
    ph_before = _finite(row.get(f"before_ph__{tower_id}"))
    ph_after = _finite(row.get(f"after_ph__{tower_id}"))
    ph_response_max = _finite(row.get("flow_effect_response_tower_ph_max"))
    ph_response_min = _finite(row.get("flow_effect_response_tower_ph_min"))

    safe = tuple(map(float, tower.get("ph_safe_range", (float("-inf"), float("inf")))))
    operating = tuple(map(float, tower.get("ph_operating_range", safe)))
    safe_min, safe_max = safe
    operating_min, operating_max = operating

    ph_safe_violation = _bool(row.get(f"ph_out_of_range__{tower_id}"), False)
    if ph_response_max is not None and ph_response_max > safe_max:
        ph_safe_violation = True
    if ph_response_min is not None and ph_response_min < safe_min:
        ph_safe_violation = True

    ph_operating_violation = False
    observed_ph = [value for value in (ph_before, ph_after, ph_response_min, ph_response_max) if value is not None]
    if observed_ph:
        ph_operating_violation = min(observed_ph) < operating_min or max(observed_ph) > operating_max

    dynamic_eligible = bool(valid and complete and shape in {item.upper() for item in config.dynamic_shapes})
    safety_evidence = bool(ph_operating_violation or ph_safe_violation)

    reasons: list[str] = []
    if not valid:
        reasons.append("HISTORICAL_EPISODE_NOT_VALID")
    if not complete:
        reasons.append("HISTORICAL_EFFECT_INCOMPLETE")
    if safety_evidence:
        reasons.append("PH_OUTSIDE_OPERATING_ENVELOPE")
    if ph_safe_violation:
        reasons.append("PH_OUTSIDE_SAFE_ENVELOPE")

    local_gain_eligible = bool(
        valid
        and complete
        and shape in {item.upper() for item in config.local_shapes}
    )
    if config.max_local_abs_delta_q is None or config.max_local_extra_slurry_volume is None:
        local_gain_eligible = False
        reasons.append("LOCAL_GAIN_MAGNITUDE_LIMITS_UNCALIBRATED")
    else:
        if delta_q is None or abs(delta_q) > float(config.max_local_abs_delta_q):
            local_gain_eligible = False
            reasons.append("LOCAL_GAIN_DELTA_Q_TOO_LARGE_OR_MISSING")
        if total_extra is None or total_extra > float(config.max_local_extra_slurry_volume):
            local_gain_eligible = False
            reasons.append("LOCAL_GAIN_DOSE_TOO_LARGE_OR_MISSING")

    if config.require_ph_inside_operating_range and ph_operating_violation:
        local_gain_eligible = False
        reasons.append("LOCAL_GAIN_PH_NOT_IN_OPERATING_ENVELOPE")

    phi_so2 = None
    phi_ph = None
    if delta_q is not None and abs(delta_q) > 1e-12:
        if delta_so2 is not None:
            phi_so2 = delta_so2 / delta_q
        if delta_ph is not None:
            phi_ph = delta_ph / delta_q
    if config.require_dual_physical_direction:
        if phi_so2 is None or not math.isfinite(phi_so2) or phi_so2 >= 0.0:
            local_gain_eligible = False
            reasons.append("LOCAL_GAIN_SO2_DIRECTION_INVALID")
        if phi_ph is None or not math.isfinite(phi_ph) or phi_ph <= 0.0:
            local_gain_eligible = False
            reasons.append("LOCAL_GAIN_PH_DIRECTION_INVALID")

    roles: list[str] = []
    if local_gain_eligible:
        roles.append(LOCAL_GAIN_EVIDENCE)
    if dynamic_eligible:
        roles.append(DYNAMIC_EVIDENCE)
    if safety_evidence:
        roles.append(SAFETY_EVIDENCE)

    return HistoricalEvidenceDecision(
        roles=tuple(roles),
        local_gain_eligible=local_gain_eligible,
        dynamic_eligible=dynamic_eligible,
        safety_evidence=safety_evidence,
        reasons=tuple(dict.fromkeys(reasons)),
        metrics={
            "tower_id": tower_id,
            "flow_shape": shape,
            "delta_q_actual": delta_q,
            "delta_so2": delta_so2,
            "delta_ph": delta_ph,
            "phi_so2_event": phi_so2,
            "phi_ph_event": phi_ph,
            "extra_slurry_volume_m3": total_extra,
            "ph_before": ph_before,
            "ph_after": ph_after,
            "ph_response_min": ph_response_min,
            "ph_response_max": ph_response_max,
            "ph_operating_min": operating_min,
            "ph_operating_max": operating_max,
            "ph_safe_min": safe_min,
            "ph_safe_max": safe_max,
            "ph_operating_violation": ph_operating_violation,
            "ph_safe_violation": ph_safe_violation,
        },
    )


def _trajectory_metrics(
    episode: Mapping[str, Any],
    history: pd.DataFrame,
    plant: Mapping[str, Any],
    *,
    dose_horizons_minutes: Sequence[int] = DEFAULT_DOSE_HORIZONS_MINUTES,
    response_horizon_minutes: int = DEFAULT_RESPONSE_HORIZON_MINUTES,
) -> dict[str, Any]:
    row = dict(episode)
    tower_id = _text(row.get("active_tower_ids")) or _text(row.get("flow_event_tower_id"))
    tower = _tower(plant, tower_id)
    flow_column = _flow_column(tower)
    ph_column = str(tower.get("ph_column") or "").strip()
    timestamp_column = time_column(dict(plant))
    start_text = row.get("action_start_time")
    baseline_flow = _finite(row.get("flow_event_baseline_flow"))
    if baseline_flow is None:
        return {}
    try:
        start = pd.Timestamp(start_text)
    except Exception:
        return {}
    if pd.isna(start):
        return {}

    output: dict[str, Any] = {
        "flow_metric_column": flow_column,
        "ph_metric_column": ph_column,
    }
    for horizon in dose_horizons_minutes:
        end = start + pd.Timedelta(minutes=float(horizon))
        window = _slice(history, timestamp_column, start, end)
        dose = _integrate_positive_delta_volume(
            window,
            timestamp_column,
            flow_column,
            baseline_flow,
        )
        values = (
            pd.to_numeric(window[flow_column], errors="coerce").dropna()
            if flow_column in window.columns
            else pd.Series(dtype=float)
        )
        output[f"dose_{int(horizon)}m_m3"] = dose
        output[f"flow_mean_{int(horizon)}m"] = (
            float(values.mean()) if not values.empty else None
        )
        output[f"flow_peak_{int(horizon)}m"] = (
            float(values.max()) if not values.empty else None
        )

    response_end = start + pd.Timedelta(minutes=float(response_horizon_minutes))
    response = _slice(history, timestamp_column, start, response_end)
    if ph_column and ph_column in response.columns:
        ph_values = pd.to_numeric(response[ph_column], errors="coerce").dropna()
        if not ph_values.empty:
            operating = tuple(map(float, tower.get("ph_operating_range", tower.get("ph_safe_range", (-math.inf, math.inf)))))
            safe = tuple(map(float, tower.get("ph_safe_range", (-math.inf, math.inf))))
            output["ph_peak_response_horizon"] = float(ph_values.max())
            output["ph_min_response_horizon"] = float(ph_values.min())
            output["ph_over_operating_max_minutes"] = _duration_above(
                response,
                timestamp_column,
                ph_column,
                operating[1],
            )
            output["ph_over_safe_max_minutes"] = _duration_above(
                response,
                timestamp_column,
                ph_column,
                safe[1],
            )
    return output


def enrich_historical_episode_frame(
    episodes: pd.DataFrame,
    history: pd.DataFrame,
    plant: Mapping[str, Any],
    routing_config: HistoricalEvidenceRoutingConfig | Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Add dose/safety metrics and non-exclusive MFAC evidence roles."""
    if episodes.empty:
        return episodes.copy()
    config = (
        routing_config
        if isinstance(routing_config, HistoricalEvidenceRoutingConfig)
        else HistoricalEvidenceRoutingConfig.from_mapping(routing_config)
    )
    records: list[dict[str, Any]] = []
    for record in episodes.to_dict(orient="records"):
        enriched = dict(record)
        enriched.update(_trajectory_metrics(record, history, plant))
        decision = _role_decision(enriched, plant, config)
        enriched["mfac_evidence_roles"] = "|".join(decision.roles)
        enriched["mfac_local_gain_eligible"] = bool(decision.local_gain_eligible)
        enriched["mfac_dynamic_evidence_eligible"] = bool(decision.dynamic_eligible)
        enriched["mfac_safety_evidence"] = bool(decision.safety_evidence)
        enriched["mfac_evidence_reasons"] = "|".join(decision.reasons)
        enriched["mfac_evidence_metrics"] = json.dumps(
            decision.metrics,
            ensure_ascii=False,
            sort_keys=True,
        )
        enriched["mfac_evidence_semantics_version"] = decision.semantics_version
        records.append(enriched)
    return pd.DataFrame(records)


__all__ = [
    "HISTORICAL_EVIDENCE_SEMANTICS_VERSION",
    "LOCAL_GAIN_EVIDENCE",
    "DYNAMIC_EVIDENCE",
    "SAFETY_EVIDENCE",
    "DEFAULT_DOSE_HORIZONS_MINUTES",
    "HistoricalEvidenceRoutingConfig",
    "HistoricalEvidenceDecision",
    "enrich_historical_episode_frame",
]
