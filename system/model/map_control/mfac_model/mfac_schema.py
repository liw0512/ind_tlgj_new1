# -*- coding: utf-8 -*-
"""Serializable data contracts for Scheme 2 MFAC V1.

The first implementation deliberately keeps MFAC state outside
``condition_model``.  Every artifact is bound to the condition snapshot
version that produced its context so later seven-day condition updates can be
migrated explicitly instead of silently reusing stale semantics.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


MFAC_SEMANTICS_VERSION = "SCHEME2_MFAC_V1"


@dataclass
class DelayProfile:
    onset_p50_seconds: Optional[float] = None
    onset_p90_seconds: Optional[float] = None
    response_p50_seconds: Optional[float] = None
    response_p90_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Optional[Dict[str, Any]]) -> "DelayProfile":
        return cls(**dict(value or {}))


@dataclass(frozen=True)
class MFACContextResolution:
    condition_snapshot_version: str
    condition_label: str
    base_condition_id: str
    grid_id: str
    policy_region_id: str
    mfac_context_id: str
    resolution_source: str


@dataclass
class ActionResponseEvent:
    event_id: str
    condition_snapshot_version: str
    condition_label: str
    base_condition_id: str
    grid_id: str
    policy_region_id: str
    mfac_context_id: str

    action_start_time: str = ""
    action_reached_time: str = ""
    response_start_time: str = ""
    response_end_time: str = ""
    action_source: str = "unknown"

    q_before: Optional[float] = None
    q_after: Optional[float] = None
    delta_q_actual: Optional[float] = None
    qbase_before: Optional[float] = None
    qbase_after: Optional[float] = None
    qbase_drift: Optional[float] = None

    so2_target: Optional[float] = None
    so2_before: Optional[float] = None
    so2_after: Optional[float] = None
    delta_so2: Optional[float] = None
    ph_before: Optional[float] = None
    ph_after: Optional[float] = None
    delta_ph: Optional[float] = None

    inlet_so2_change: Optional[float] = None
    load_change: Optional[float] = None
    fast_overlap: bool = False
    equipment_changed: bool = False
    target_changed: bool = False
    condition_changed: bool = False
    data_quality_ok: bool = True

    learning_eligible: bool = False
    reject_reason: str = ""
    phi_event: Optional[float] = None
    quality_score: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = MFAC_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ActionResponseEvent":
        return cls(**dict(value))


@dataclass
class MFACBootstrapProfile:
    condition_snapshot_version: str
    mfac_context_id: str
    phi_prior: float
    phi_live0: float
    confidence: float
    valid_event_count: int
    independent_days: int
    delay_profile: DelayProfile = field(default_factory=DelayProfile)
    condition_labels: List[str] = field(default_factory=list)
    base_condition_ids: List[str] = field(default_factory=list)
    phi_distribution: Dict[str, Any] = field(default_factory=dict)
    training_window: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = MFAC_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["delay_profile"] = self.delay_profile.to_dict()
        return value

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "MFACBootstrapProfile":
        data = dict(value)
        data["delay_profile"] = DelayProfile.from_dict(data.get("delay_profile"))
        return cls(**data)


@dataclass
class MFACRuntimeState:
    condition_snapshot_version: str
    mfac_context_id: str
    phi_live: float
    confidence_live: float
    bias_live: float = 0.0
    valid_event_count: int = 0
    last_event_id: str = ""
    last_update_time: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = MFAC_SEMANTICS_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "MFACRuntimeState":
        return cls(**dict(value))
