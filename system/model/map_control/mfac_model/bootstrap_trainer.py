# -*- coding: utf-8 -*-
"""Evidence-first offline bootstrap trainer for Scheme 2 MFAC V1.

This module intentionally separates robust sensitivity estimation from the
confidence/permission policy.  Historical data should first prove what the
local sensitivity and delay distributions look like; control-authority scores
can then be calibrated without burying arbitrary thresholds in the estimator.
"""

from dataclasses import asdict, dataclass, field
import math
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from .mfac_schema import (
    MFAC_SEMANTICS_VERSION,
    ActionResponseEvent,
    DelayProfile,
    MFACBootstrapProfile,
)


@dataclass(frozen=True)
class MFACReplayConfig:
    eta: float
    mu: float
    max_single_update_abs: Optional[float] = None
    phi_lower_bound: Optional[float] = None
    phi_upper_bound: float = -1e-12

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.eta)) or float(self.eta) <= 0.0:
            raise ValueError("eta must be finite and > 0")
        if not math.isfinite(float(self.mu)) or float(self.mu) <= 0.0:
            raise ValueError("mu must be finite and > 0")
        if float(self.phi_upper_bound) >= 0.0:
            raise ValueError("phi_upper_bound must remain negative")
        if (
            self.phi_lower_bound is not None
            and float(self.phi_lower_bound) >= float(self.phi_upper_bound)
        ):
            raise ValueError("phi_lower_bound must be < phi_upper_bound")


@dataclass
class MFACBootstrapEvidence:
    condition_snapshot_version: str
    mfac_context_id: str
    phi_seed: float
    phi_replayed: float
    phi_distribution: Dict[str, float]
    delay_profile: DelayProfile
    valid_event_count: int
    independent_days: int
    condition_labels: List[str] = field(default_factory=list)
    base_condition_ids: List[str] = field(default_factory=list)
    event_ids: List[str] = field(default_factory=list)
    rejected_replay_event_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = MFAC_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["delay_profile"] = self.delay_profile.to_dict()
        return value


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _quantile(values: Sequence[float], q: float) -> float:
    series = pd.Series(list(values), dtype=float)
    return float(series.quantile(q, interpolation="linear"))


def _mad(values: Sequence[float]) -> float:
    center = float(median(values))
    return float(median(abs(float(value) - center) for value in values))


def _event_phi(event: ActionResponseEvent) -> Optional[float]:
    if event.phi_event is not None:
        value = _finite(event.phi_event)
        if value is not None:
            return value
    delta_q = _finite(event.delta_q_actual)
    delta_so2 = _finite(event.delta_so2)
    if delta_q is None or delta_so2 is None or abs(delta_q) <= 1e-12:
        return None
    return delta_so2 / delta_q


def _quality_weight(event: ActionResponseEvent) -> float:
    value = _finite(event.quality_score)
    if value is None:
        return 1.0
    return min(1.0, max(0.0, value))


def _event_time(event: ActionResponseEvent) -> pd.Timestamp:
    try:
        value = pd.Timestamp(event.action_start_time)
    except Exception:
        return pd.Timestamp.max
    return value if not pd.isna(value) else pd.Timestamp.max


def _delay_seconds(
    event: ActionResponseEvent,
    metadata_key: str,
) -> Optional[float]:
    value = _finite((event.metadata or {}).get(metadata_key))
    if value is None or value < 0.0:
        return None
    return value * 60.0


def _delay_profile(events: Sequence[ActionResponseEvent]) -> DelayProfile:
    onset = [
        value
        for event in events
        if (value := _delay_seconds(event, "observed_response_delay_minutes"))
        is not None
    ]
    response = [
        value
        for event in events
        if (value := _delay_seconds(event, "time_to_stable_minutes"))
        is not None
    ]
    return DelayProfile(
        onset_p50_seconds=_quantile(onset, 0.50) if onset else None,
        onset_p90_seconds=_quantile(onset, 0.90) if onset else None,
        response_p50_seconds=_quantile(response, 0.50) if response else None,
        response_p90_seconds=_quantile(response, 0.90) if response else None,
    )


def _recursive_replay(
    events: Sequence[ActionResponseEvent],
    phi_seed: float,
    config: MFACReplayConfig,
) -> tuple[float, List[str]]:
    phi = float(phi_seed)
    rejected: List[str] = []
    for event in sorted(events, key=_event_time):
        delta_q = _finite(event.delta_q_actual)
        delta_so2 = _finite(event.delta_so2)
        if delta_q is None or delta_so2 is None or abs(delta_q) <= 1e-12:
            rejected.append(event.event_id)
            continue

        residual = delta_so2 - phi * delta_q
        update = (
            float(config.eta)
            * _quality_weight(event)
            * delta_q
            * residual
            / (float(config.mu) + delta_q * delta_q)
        )
        if config.max_single_update_abs is not None:
            limit = abs(float(config.max_single_update_abs))
            update = max(-limit, min(limit, update))
        candidate = phi + update

        if config.phi_lower_bound is not None:
            candidate = max(float(config.phi_lower_bound), candidate)
        candidate = min(float(config.phi_upper_bound), candidate)
        if not math.isfinite(candidate) or candidate >= 0.0:
            rejected.append(event.event_id)
            continue
        phi = candidate
    return phi, rejected


def build_bootstrap_evidence(
    events: Iterable[ActionResponseEvent],
    replay_config: MFACReplayConfig,
) -> List[MFACBootstrapEvidence]:
    """Build one evidence bundle per (snapshot version, MFAC context)."""
    groups: Dict[tuple[str, str], List[ActionResponseEvent]] = {}
    for event in events:
        if not event.learning_eligible:
            continue
        phi = _event_phi(event)
        if phi is None or phi >= 0.0:
            continue
        key = (
            str(event.condition_snapshot_version),
            str(event.mfac_context_id),
        )
        groups.setdefault(key, []).append(event)

    output: List[MFACBootstrapEvidence] = []
    for (snapshot_version, context_id), group in sorted(groups.items()):
        phi_values = [float(_event_phi(event)) for event in group]
        phi_seed = float(median(phi_values))
        phi_replayed, rejected = _recursive_replay(
            group,
            phi_seed,
            replay_config,
        )
        event_days = {
            _event_time(event).date().isoformat()
            for event in group
            if _event_time(event) != pd.Timestamp.max
        }
        distribution = {
            "p10": _quantile(phi_values, 0.10),
            "p25": _quantile(phi_values, 0.25),
            "median": _quantile(phi_values, 0.50),
            "p75": _quantile(phi_values, 0.75),
            "p90": _quantile(phi_values, 0.90),
            "mad": _mad(phi_values),
            "min": float(min(phi_values)),
            "max": float(max(phi_values)),
        }
        output.append(
            MFACBootstrapEvidence(
                condition_snapshot_version=snapshot_version,
                mfac_context_id=context_id,
                phi_seed=phi_seed,
                phi_replayed=phi_replayed,
                phi_distribution=distribution,
                delay_profile=_delay_profile(group),
                valid_event_count=len(group),
                independent_days=len(event_days),
                condition_labels=sorted(
                    {str(event.condition_label) for event in group}
                ),
                base_condition_ids=sorted(
                    {str(event.base_condition_id) for event in group}
                ),
                event_ids=[event.event_id for event in sorted(group, key=_event_time)],
                rejected_replay_event_ids=rejected,
                metadata={
                    "confidence_status": "NOT_CALIBRATED",
                    "replay_eta": float(replay_config.eta),
                    "replay_mu": float(replay_config.mu),
                },
            )
        )
    return output


def finalize_bootstrap_profile(
    evidence: MFACBootstrapEvidence,
    *,
    confidence: float,
    training_window: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> MFACBootstrapProfile:
    """Convert reviewed evidence into a deployable profile.

    Confidence is an explicit caller input until the project calibrates and
    freezes a confidence-scoring policy from historical replay.
    """
    confidence_value = float(confidence)
    if not math.isfinite(confidence_value) or not 0.0 <= confidence_value <= 1.0:
        raise ValueError("confidence must be finite within [0, 1]")
    combined_metadata = dict(evidence.metadata)
    combined_metadata.update(metadata or {})
    combined_metadata["confidence_status"] = "EXPLICITLY_ASSIGNED"
    return MFACBootstrapProfile(
        condition_snapshot_version=evidence.condition_snapshot_version,
        mfac_context_id=evidence.mfac_context_id,
        phi_prior=evidence.phi_seed,
        phi_live0=evidence.phi_replayed,
        confidence=confidence_value,
        valid_event_count=evidence.valid_event_count,
        independent_days=evidence.independent_days,
        delay_profile=evidence.delay_profile,
        condition_labels=list(evidence.condition_labels),
        base_condition_ids=list(evidence.base_condition_ids),
        phi_distribution=dict(evidence.phi_distribution),
        training_window=dict(training_window or {}),
        metadata=combined_metadata,
    )
