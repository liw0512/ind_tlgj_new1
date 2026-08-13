from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ConditionContext:
    condition_snapshot_version: str
    condition_label: str
    raw_grid_id: str = "UNKNOWN"
    raw_condition_label: str = "UNKNOWN"
    condition_stable: bool = False
    condition_switch_state: str = "UNKNOWN"
    condition_valid: bool = False
    out_of_range_clipped: bool = False
    state_key: str = ""


@dataclass
class RealtimeState:
    timestamp: str
    condition: ConditionContext
    process: Dict[str, Any]
    load_rate: float
    inlet_so2_rate: float
    outlet_so2_rate: float
    disturbance_mode: str
    control_mode: str
    fast_context: Dict[str, Any]
    policy_state_key: str
    policy_state_key_no_grid: str


@dataclass
class ControlDemand:
    commanded_target: float
    effective_target: float
    current_so2: float
    error: float
    demand_level: str
    desired_so2_response: str
    acceptable_effect_directions: List[str]
    maximum_action_magnitude: str
    safety_level: str
    target_changed: bool = False
    reason_codes: List[str] = field(default_factory=list)


@dataclass
class Candidate:
    source: str
    owner_id: str
    state_key: str
    action_id: str
    profile: Dict[str, Any]
    source_priority: int
    synthetic: bool = False
    reject_reasons: List[str] = field(default_factory=list)
    rank_key: Optional[tuple] = None
    # 在线目标匹配、预计SO2/pH等可解释诊断；不写回离线模型事实源。
    evaluation: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ResolvedAction:
    action_id: str
    action_family: str
    action_direction: str
    action_magnitude: str
    recommended_valve_deltas: Dict[str, float]
    projected_valve_openings: Dict[str, float]
    active_valve_ids: List[str]
    active_tower_ids: List[str]
    reason_codes: List[str] = field(default_factory=list)


@dataclass
class Decision:
    decision_id: str
    timestamp: str
    model_version: Optional[str]
    condition_snapshot_version: Optional[str]
    condition_label: Optional[str]
    raw_grid_id: Optional[str]
    control_mode: str
    disturbance_mode: str
    current_so2: Optional[float]
    commanded_target: Optional[float]
    effective_target: Optional[float]
    desired_so2_response: str
    experience_source: str
    action_id: str
    action_family: str
    action_direction: str
    action_magnitude: str
    recommended_valve_deltas: Dict[str, float]
    projected_valve_openings: Dict[str, float]
    historical_reliability: Optional[float]
    historical_safety_score: Optional[float]
    historical_direction_consistency: Optional[float]
    decision_status: str
    reason_codes: List[str]
    debug: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
