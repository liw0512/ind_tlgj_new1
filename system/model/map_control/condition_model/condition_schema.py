# -*- coding: utf-8 -*-
"""Serializable domain records used by the V3 condition model.

The current schema contains no action-event, confidence, or slurry-flow fields.
Legacy snapshots are migrated in memory while being read.
"""

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


def _finite_or_none(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _finite_or_none(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_finite_or_none(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_finite_or_none(item) for item in value)
    return value


def _migrate_statistics(value: Dict[str, Any]) -> Dict[str, Any]:
    statistics = dict(_finite_or_none(value or {}))

    # New snapshots use additive means because the current accumulator can
    # update them exactly.  Older median fields are accepted as approximations.
    fallback_map = {
        "mean_liquid_gas": ("median_liquid_gas",),
        "mean_xst_ph": ("mean_ph", "median_ph"),
        "mean_apt_ph": ("median_apt_ph",),
        "mean_net_so2": ("median_net_so2",),
    }
    for target, sources in fallback_map.items():
        if statistics.get(target) is None:
            for source in sources:
                if statistics.get(source) is not None:
                    statistics[target] = statistics[source]
                    break

    for retired in (
        "median_liquid_gas",
        "median_ph",
        "mean_ph",
        "median_apt_ph",
        "mean_total_coal",
        "median_total_coal",
        "median_net_so2",
        "median_slurry_flow",
        "mean_slurry_flow",
    ):
        statistics.pop(retired, None)
    return statistics


def _migrate_accumulators(value: Dict[str, Any]) -> Dict[str, Any]:
    accumulators = dict(_finite_or_none(value or {}))
    numeric = dict(accumulators.get("numeric") or {})

    # Old generic pH represented the first-stage tower pH.
    if "xst_ph" not in numeric and "ph" in numeric:
        numeric["xst_ph"] = numeric["ph"]
    numeric.pop("ph", None)
    numeric.pop("slurry_flow", None)
    numeric.pop("total_coal", None)

    accumulators["numeric"] = numeric
    return accumulators


@dataclass
class GridCell:
    grid_id: str
    load_level: int
    inlet_so2_level: int
    load_range: Tuple[float, float]
    inlet_so2_range: Tuple[float, float]
    validity: str = "VALID"
    coverage_status: str = "EMPTY"
    policy_region_id: str = ""
    sample_count: int = 0
    clipped_count: int = 0
    state_profiles: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    pump_distribution: Dict[str, int] = field(default_factory=dict)
    statistics: Dict[str, Any] = field(default_factory=dict)
    accumulators: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["load_range"] = list(self.load_range)
        value["inlet_so2_range"] = list(self.inlet_so2_range)
        return value

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "GridCell":
        data = dict(value)
        data.pop("action_event_count", None)
        data.pop("confidence", None)
        data["load_range"] = tuple(data["load_range"])
        data["inlet_so2_range"] = tuple(data["inlet_so2_range"])

        clean_profiles: Dict[str, Dict[str, Any]] = {}
        for state_key, profile in (data.get("state_profiles") or {}).items():
            clean_profile = dict(_finite_or_none(profile or {}))
            clean_profile.pop("action_event_count", None)
            clean_profiles[str(state_key)] = clean_profile
        data["state_profiles"] = clean_profiles
        data["statistics"] = _migrate_statistics(data.get("statistics") or {})
        data["accumulators"] = _migrate_accumulators(data.get("accumulators") or {})
        return cls(**data)


@dataclass
class PolicyRegion:
    region_id: str
    member_grid_ids: List[str]
    status: str = "INDEPENDENT"
    evidence: Dict[str, Any] = field(default_factory=dict)
    condition_label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "PolicyRegion":
        data = dict(_finite_or_none(value))
        data.pop("confidence", None)
        return cls(**data)


@dataclass
class ConditionSnapshot:
    snapshot_version: str
    build_time: str
    grid_config: Dict[str, Any]
    grid_catalog: Dict[str, GridCell]
    grid_adjacency: Dict[str, List[str]]
    policy_regions: Dict[str, PolicyRegion]
    previous_snapshot_version: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OnlineConditionResult:
    """One online condition-classification result.

    ``raw_*`` fields describe the current single-sample grid lookup.
    ``stable_*`` fields describe the majority result over the configured
    sliding window.  Compatibility fields ``grid_id`` and
    ``condition_label`` always mirror the stable result and are the fields
    downstream online policy selection should consume.
    """

    grid_id: Optional[str]
    policy_region_id: Optional[str]
    state_key: str
    coverage_status: str
    experience_source: str
    condition_label: Optional[str] = None
    raw_grid_id: Optional[str] = None
    raw_condition_label: Optional[str] = None
    stable_grid_id: Optional[str] = None
    stable_condition_label: Optional[str] = None
    condition_valid: bool = True
    condition_stable: bool = False
    out_of_range_clipped: bool = False
    clip_axis: str = "none"
    condition_switch_state: str = "INITIALIZING"
    stability_mode: str = "MAJORITY"
    stability_window_size: int = 6
    stability_sample_count: int = 0
    majority_count: int = 0
    majority_tied: bool = False
    economic_exploration_allowed: bool = False
    reason: Optional[str] = None
