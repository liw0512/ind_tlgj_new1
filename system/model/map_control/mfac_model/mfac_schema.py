# -*- coding: utf-8 -*-
"""Serializable data contracts for Scheme 2 MFAC V1.

The first implementation deliberately keeps MFAC state outside
``condition_model``.  Every artifact is bound to the condition snapshot
version that produced its context so later seven-day condition updates can be
migrated explicitly instead of silently reusing stale semantics.

Backward compatibility note
---------------------------
The legacy ``phi_live`` / ``confidence_live`` fields remain the canonical SO2
channel fields.  Dual-response support adds an independent positive-direction
pH channel without changing old bootstrap/runtime artifacts.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


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


@dataclass(frozen=True)
class QbaseResult:
    """Auditable result of one online Dynamic Qbase calculation."""

    tower_id: str
    valid: bool
    status: str
    qbase_raw: Optional[float]
    qbase_effective: Optional[float]
    inlet_so2: Optional[float]
    target_so2: Optional[float]
    gas_flow: Optional[float]
    slurry_density: Optional[float]
    solid_fraction: Optional[float]
    ca_s_ratio: Optional[float]
    ph_value: Optional[float]
    formula_version: str
    reason_codes: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["reason_codes"] = list(self.reason_codes)
        return value


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
    # Legacy fields are the SO2 channel and remain backward-compatible.
    phi_live: float
    confidence_live: float
    bias_live: float = 0.0
    valid_event_count: int = 0
    last_event_id: str = ""
    last_update_time: str = ""

    # Independent pH response channel.  Positive phi is the physical direction:
    # more slurry -> higher pH, less slurry -> lower pH.
    phi_ph_live: Optional[float] = None
    confidence_ph_live: float = 0.0
    ph_valid_event_count: int = 0
    ph_last_event_id: str = ""
    ph_last_update_time: str = ""

    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = MFAC_SEMANTICS_VERSION

    @property
    def phi_so2_live(self) -> float:
        return self.phi_live

    @property
    def confidence_so2_live(self) -> float:
        return self.confidence_live

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        # Public aliases make the dual-channel contract explicit while keeping
        # old serialized keys available to existing V1 consumers.
        value["phi_so2_live"] = self.phi_live
        value["confidence_so2_live"] = self.confidence_live
        return value

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "MFACRuntimeState":
        data = dict(value)
        if "phi_live" not in data and "phi_so2_live" in data:
            data["phi_live"] = data["phi_so2_live"]
        if "confidence_live" not in data and "confidence_so2_live" in data:
            data["confidence_live"] = data["confidence_so2_live"]
        data.pop("phi_so2_live", None)
        data.pop("confidence_so2_live", None)
        return cls(**data)
