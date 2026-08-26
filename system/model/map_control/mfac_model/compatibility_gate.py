# -*- coding: utf-8 -*-
"""MFAC compatibility-gate contract for scheme-1 condition merges.

Phase 1 defines the evidence/result boundary only.  Numeric thresholds are not
frozen here because they must be calibrated from historical ActionResponseEvent
coverage before the gate is allowed to block or approve production mappings.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class MFACCompatibilityEvidence:
    left_context_id: str
    right_context_id: str

    left_valid_event_count: int = 0
    right_valid_event_count: int = 0
    left_independent_days: int = 0
    right_independent_days: int = 0

    left_phi_median: Optional[float] = None
    right_phi_median: Optional[float] = None
    left_phi_spread: Optional[float] = None
    right_phi_spread: Optional[float] = None

    left_negative_direction_ratio: Optional[float] = None
    right_negative_direction_ratio: Optional[float] = None

    left_delay_onset_p50_seconds: Optional[float] = None
    right_delay_onset_p50_seconds: Optional[float] = None
    left_delay_response_p50_seconds: Optional[float] = None
    right_delay_response_p50_seconds: Optional[float] = None

    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MFACCompatibilityDecision:
    """Result returned by a future calibrated compatibility implementation.

    ``compatible=None`` means evidence is insufficient; it must not be treated
    as an automatic approval.
    """

    compatible: Optional[bool]
    decision: str
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MFACCompatibilityGate(Protocol):
    def evaluate(
        self,
        evidence: MFACCompatibilityEvidence,
    ) -> MFACCompatibilityDecision:
        """Evaluate whether two MFAC contexts may share one context state."""
        ...


def insufficient_evidence_decision(*reasons: str) -> MFACCompatibilityDecision:
    """Conservative helper used until calibrated gate thresholds are frozen."""
    return MFACCompatibilityDecision(
        compatible=None,
        decision="INSUFFICIENT_EVIDENCE",
        reasons=[str(reason) for reason in reasons if str(reason)],
    )
