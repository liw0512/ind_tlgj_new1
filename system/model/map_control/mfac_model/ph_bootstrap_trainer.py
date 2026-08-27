# -*- coding: utf-8 -*-
"""Evidence-first offline bootstrap for the positive pH response channel.

Only LOCAL_GAIN events may seed ``phi_ph``.  Historical pulse/boost actions are
kept for dynamic and safety evidence but are intentionally excluded from this
local sensitivity estimator.
"""

from dataclasses import asdict, dataclass, field
import math
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from .mfac_schema import ActionResponseEvent


PH_BOOTSTRAP_SEMANTICS_VERSION = "SCHEME2_PH_BOOTSTRAP_V1_LOCAL_GAIN"


@dataclass(frozen=True)
class PHReplayConfig:
    eta: float
    mu: float
    max_single_update_abs: Optional[float] = None
    phi_lower_bound: float = 1e-12
    phi_upper_bound: Optional[float] = None

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.eta)) or float(self.eta) <= 0.0:
            raise ValueError("eta must be finite and > 0")
        if not math.isfinite(float(self.mu)) or float(self.mu) <= 0.0:
            raise ValueError("mu must be finite and > 0")
        if not math.isfinite(float(self.phi_lower_bound)) or float(self.phi_lower_bound) <= 0.0:
            raise ValueError("phi_lower_bound must remain positive")
        if self.phi_upper_bound is not None:
            upper = float(self.phi_upper_bound)
            if not math.isfinite(upper) or upper <= float(self.phi_lower_bound):
                raise ValueError("phi_upper_bound must be > phi_lower_bound")
        if self.max_single_update_abs is not None:
            limit = float(self.max_single_update_abs)
            if not math.isfinite(limit) or limit <= 0.0:
                raise ValueError("max_single_update_abs must be finite and > 0")


@dataclass
class PHBootstrapEvidence:
    condition_snapshot_version: str
    mfac_context_id: str
    phi_seed: float
    phi_replayed: float
    phi_distribution: Dict[str, float]
    valid_event_count: int
    independent_days: int
    event_ids: List[str] = field(default_factory=list)
    rejected_replay_event_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = PH_BOOTSTRAP_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PHBootstrapProfile:
    condition_snapshot_version: str
    mfac_context_id: str
    phi_prior: float
    phi_live0: float
    confidence: float
    valid_event_count: int
    independent_days: int
    phi_distribution: Dict[str, Any] = field(default_factory=dict)
    training_window: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = PH_BOOTSTRAP_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)



def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _event_time(event: ActionResponseEvent) -> pd.Timestamp:
    try:
        value = pd.Timestamp(event.action_start_time)
    except Exception:
        return pd.Timestamp.max
    return value if not pd.isna(value) else pd.Timestamp.max


def _historical_local_gain_allowed(event: ActionResponseEvent) -> bool:
    source = str(event.action_source or "").upper()
    if not source.startswith("HISTORICAL_"):
        return True
    metadata = dict(event.metadata or {})
    if metadata.get("ph_out_of_safe_range") is True:
        return False
    if source == "HISTORICAL_ACTUAL_SUPPLY_FLOW_LOCAL_GAIN":
        return bool(metadata.get("historical_local_gain_eligible", True))
    return metadata.get("historical_local_gain_eligible") is True


def _event_phi(event: ActionResponseEvent) -> Optional[float]:
    delta_q = _finite(event.delta_q_actual)
    delta_ph = _finite(event.delta_ph)
    if delta_q is None or delta_ph is None or abs(delta_q) <= 1e-12:
        return None
    value = delta_ph / delta_q
    return value if math.isfinite(value) else None


def _quantile(values: Sequence[float], q: float) -> float:
    series = pd.Series(list(values), dtype=float)
    return float(series.quantile(q, interpolation="linear"))


def _mad(values: Sequence[float]) -> float:
    center = float(median(values))
    return float(median(abs(float(value) - center) for value in values))


def _recursive_replay(
    events: Sequence[ActionResponseEvent],
    phi_seed: float,
    config: PHReplayConfig,
) -> tuple[float, List[str]]:
    phi = float(phi_seed)
    rejected: List[str] = []
    for event in sorted(events, key=_event_time):
        delta_q = _finite(event.delta_q_actual)
        delta_ph = _finite(event.delta_ph)
        if delta_q is None or delta_ph is None or abs(delta_q) <= 1e-12:
            rejected.append(event.event_id)
            continue
        observed_phi = delta_ph / delta_q
        if not math.isfinite(observed_phi) or observed_phi <= 0.0:
            rejected.append(event.event_id)
            continue

        residual = delta_ph - phi * delta_q
        update = (
            float(config.eta)
            * delta_q
            * residual
            / (float(config.mu) + delta_q * delta_q)
        )
        if config.max_single_update_abs is not None:
            limit = abs(float(config.max_single_update_abs))
            update = max(-limit, min(limit, update))
        candidate = max(float(config.phi_lower_bound), phi + update)
        if config.phi_upper_bound is not None:
            candidate = min(float(config.phi_upper_bound), candidate)
        if not math.isfinite(candidate) or candidate <= 0.0:
            rejected.append(event.event_id)
            continue
        phi = candidate
    return phi, rejected


def build_ph_bootstrap_evidence(
    events: Iterable[ActionResponseEvent],
    replay_config: PHReplayConfig,
) -> List[PHBootstrapEvidence]:
    groups: Dict[tuple[str, str], List[ActionResponseEvent]] = {}
    for event in events:
        if not event.learning_eligible:
            continue
        if not _historical_local_gain_allowed(event):
            continue
        phi = _event_phi(event)
        if phi is None or phi <= 0.0:
            continue
        key = (
            str(event.condition_snapshot_version),
            str(event.mfac_context_id),
        )
        groups.setdefault(key, []).append(event)

    output: List[PHBootstrapEvidence] = []
    for (snapshot_version, context_id), group in sorted(groups.items()):
        values = [float(_event_phi(event)) for event in group]
        phi_seed = float(median(values))
        phi_replayed, rejected = _recursive_replay(group, phi_seed, replay_config)
        days = {
            _event_time(event).date().isoformat()
            for event in group
            if _event_time(event) != pd.Timestamp.max
        }
        output.append(
            PHBootstrapEvidence(
                condition_snapshot_version=snapshot_version,
                mfac_context_id=context_id,
                phi_seed=phi_seed,
                phi_replayed=phi_replayed,
                phi_distribution={
                    "p10": _quantile(values, 0.10),
                    "p25": _quantile(values, 0.25),
                    "median": _quantile(values, 0.50),
                    "p75": _quantile(values, 0.75),
                    "p90": _quantile(values, 0.90),
                    "mad": _mad(values),
                    "min": float(min(values)),
                    "max": float(max(values)),
                },
                valid_event_count=len(group),
                independent_days=len(days),
                event_ids=[event.event_id for event in sorted(group, key=_event_time)],
                rejected_replay_event_ids=rejected,
                metadata={
                    "confidence_status": "NOT_CALIBRATED",
                    "evidence_role_required": "LOCAL_GAIN",
                    "operator_action_imitation": False,
                    "replay_eta": float(replay_config.eta),
                    "replay_mu": float(replay_config.mu),
                },
            )
        )
    return output


def finalize_ph_bootstrap_profile(
    evidence: PHBootstrapEvidence,
    *,
    confidence: float,
    training_window: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> PHBootstrapProfile:
    confidence_value = float(confidence)
    if not math.isfinite(confidence_value) or not 0.0 <= confidence_value <= 1.0:
        raise ValueError("confidence must be finite within [0, 1]")
    combined = dict(evidence.metadata)
    combined.update(metadata or {})
    combined["confidence_status"] = "EXPLICITLY_ASSIGNED"
    return PHBootstrapProfile(
        condition_snapshot_version=evidence.condition_snapshot_version,
        mfac_context_id=evidence.mfac_context_id,
        phi_prior=evidence.phi_seed,
        phi_live0=evidence.phi_replayed,
        confidence=confidence_value,
        valid_event_count=evidence.valid_event_count,
        independent_days=evidence.independent_days,
        phi_distribution=dict(evidence.phi_distribution),
        training_window=dict(training_window or {}),
        metadata=combined,
    )


__all__ = [
    "PH_BOOTSTRAP_SEMANTICS_VERSION",
    "PHReplayConfig",
    "PHBootstrapEvidence",
    "PHBootstrapProfile",
    "build_ph_bootstrap_evidence",
    "finalize_ph_bootstrap_profile",
]
