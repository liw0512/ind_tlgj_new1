# -*- coding: utf-8 -*-
"""Dimensionless online confidence update for Scheme-2 dual-response MFAC.

Historical confidence is only an initialization prior.  Once clean online
response events arrive, confidence must reflect the accumulated online evidence
instead of remaining frozen forever.

The update is deliberately dimensionless:

* support grows with effective accepted-event weight;
* direction consistency checks the known physical sign;
* innovation consistency compares observed vs. predicted response using a
  scale-free relative error;
* the historical/initial confidence is gradually replaced by online evidence.

Confounded/ineligible events must not call this module.  A clean event whose
physical direction is wrong may still be submitted with ``direction_ok=False``
to lower confidence without forcing phi across its physical sign boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Dict, Mapping, Optional, Tuple


ONLINE_CONFIDENCE_SEMANTICS_VERSION = (
    "SCHEME2_ONLINE_CONFIDENCE_V1_DIRECTION_INNOVATION_SUPPORT"
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class OnlineConfidenceConfig:
    reference_event_count: float = 5.0

    def __post_init__(self) -> None:
        value = _finite(self.reference_event_count)
        if value is None or value <= 0.0:
            raise ValueError("reference_event_count must be finite and > 0")


@dataclass(frozen=True)
class OnlineConfidenceUpdate:
    old_confidence: float
    new_confidence: float
    effective_event_count: float
    support: float
    direction_consistency: float
    innovation_consistency: float
    evidence_confidence: float
    direction_ok: bool
    quality_weight: float
    innovation_score: float
    metadata: Dict[str, Any]
    semantics_version: str = ONLINE_CONFIDENCE_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def update_online_confidence(
    *,
    current_confidence: Any,
    metadata: Optional[Mapping[str, Any]],
    metadata_key: str,
    observed_response: Any,
    predicted_response: Any,
    direction_ok: bool,
    quality_weight: Any,
    config: OnlineConfidenceConfig,
) -> Tuple[OnlineConfidenceUpdate, Dict[str, Any]]:
    old = _finite(current_confidence)
    if old is None or not 0.0 <= old <= 1.0:
        raise ValueError("current_confidence must be within [0, 1]")
    observed = _finite(observed_response)
    predicted = _finite(predicted_response)
    if observed is None or predicted is None:
        raise ValueError("observed_response and predicted_response must be finite")
    weight = _finite(quality_weight)
    if weight is None:
        weight = 1.0
    weight = max(0.0, min(1.0, weight))
    if weight <= 0.0:
        raise ValueError("quality_weight must be > 0 for an online confidence event")

    state_metadata = dict(metadata or {})
    previous = dict(state_metadata.get(metadata_key) or {})
    initial_confidence = _finite(previous.get("initial_confidence"))
    if initial_confidence is None:
        initial_confidence = old

    effective = _finite(previous.get("effective_event_count")) or 0.0
    direction_weight = _finite(previous.get("direction_success_weight")) or 0.0
    innovation_weight = _finite(previous.get("innovation_score_weight")) or 0.0

    denominator = max(abs(observed), abs(predicted), 1e-12)
    relative_innovation = abs(observed - predicted) / denominator
    innovation_score = 1.0 / (1.0 + relative_innovation)

    effective_new = effective + weight
    direction_weight_new = direction_weight + weight * (1.0 if direction_ok else 0.0)
    innovation_weight_new = innovation_weight + weight * innovation_score
    direction_consistency = direction_weight_new / effective_new
    innovation_consistency = innovation_weight_new / effective_new
    evidence_confidence = math.sqrt(
        max(0.0, direction_consistency * innovation_consistency)
    )
    reference = float(config.reference_event_count)
    support = effective_new / (effective_new + reference)
    new_confidence = (
        (1.0 - support) * float(initial_confidence)
        + support * evidence_confidence
    )
    new_confidence = max(0.0, min(1.0, new_confidence))

    evidence_metadata = {
        "semantics_version": ONLINE_CONFIDENCE_SEMANTICS_VERSION,
        "initial_confidence": float(initial_confidence),
        "effective_event_count": float(effective_new),
        "direction_success_weight": float(direction_weight_new),
        "innovation_score_weight": float(innovation_weight_new),
        "direction_consistency": float(direction_consistency),
        "innovation_consistency": float(innovation_consistency),
        "evidence_confidence": float(evidence_confidence),
        "support": float(support),
        "last_direction_ok": bool(direction_ok),
        "last_quality_weight": float(weight),
        "last_innovation_score": float(innovation_score),
        "last_observed_response": float(observed),
        "last_predicted_response": float(predicted),
    }
    state_metadata[metadata_key] = evidence_metadata
    result = OnlineConfidenceUpdate(
        old_confidence=float(old),
        new_confidence=float(new_confidence),
        effective_event_count=float(effective_new),
        support=float(support),
        direction_consistency=float(direction_consistency),
        innovation_consistency=float(innovation_consistency),
        evidence_confidence=float(evidence_confidence),
        direction_ok=bool(direction_ok),
        quality_weight=float(weight),
        innovation_score=float(innovation_score),
        metadata=evidence_metadata,
    )
    return result, state_metadata


__all__ = [
    "ONLINE_CONFIDENCE_SEMANTICS_VERSION",
    "OnlineConfidenceConfig",
    "OnlineConfidenceUpdate",
    "update_online_confidence",
]
