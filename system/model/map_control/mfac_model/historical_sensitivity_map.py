# -*- coding: utf-8 -*-
"""Hierarchical online mapping of historical dual-response MFAC sensitivity priors.

This module is deliberately separate from the online recursive adapter.  A
historical map supplies a context/work-point prior only when no persisted online
state exists.  Once online state exists it remains authoritative and is never
replaced every cycle by the historical map.

Resolution order:

1. exact MFAC context;
2. exact fixed-grid cell;
3. neighboring grid cells with confidence-weighted interpolation;
4. explicitly supplied plant-pooled fallback profile;
5. unavailable (Dynamic Qbase remains independent and can still publish).

Within one profile the sensitivity is a continuous local response surface rather
than an exact-state lookup.  Out-of-support queries are allowed only within an
explicit normalized extrapolation limit and receive a confidence penalty.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


HISTORICAL_SENSITIVITY_MAP_VERSION = (
    "SCHEME2_HISTORICAL_SENSITIVITY_MAP_V1_HIERARCHICAL_CONTINUOUS"
)
HISTORICAL_SENSITIVITY_SURFACE_VERSION = (
    "SCHEME2_MODEL_BASED_LOCAL_GAIN_SURFACE_V1_LINEAR_LOCAL"
)

_GRID_RE = re.compile(r"^P(?P<p>\d+)-S(?P<s>\d+)$")
_FEATURE_NAMES = (
    "qbase",
    "inlet_so2",
    "ph",
    "gas_flow",
    "outlet_so2",
)


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _grid_coordinate(grid_id: str) -> Optional[Tuple[int, int]]:
    match = _GRID_RE.match(str(grid_id or "").strip())
    if match is None:
        return None
    return int(match.group("p")), int(match.group("s"))


@dataclass(frozen=True)
class HistoricalSensitivityMapConfig:
    max_neighbor_grid_distance: int
    neighbor_confidence_penalty: float
    pooled_confidence_penalty: float
    max_profile_extrapolation_distance: float

    def __post_init__(self) -> None:
        if int(self.max_neighbor_grid_distance) < 1:
            raise ValueError("max_neighbor_grid_distance must be >= 1")
        for name in ("neighbor_confidence_penalty", "pooled_confidence_penalty"):
            value = _finite(getattr(self, name))
            if value is None or not 0.0 < value <= 1.0:
                raise ValueError("%s must be finite within (0, 1]" % name)
        distance = _finite(self.max_profile_extrapolation_distance)
        if distance is None or distance < 0.0:
            raise ValueError(
                "max_profile_extrapolation_distance must be finite and >= 0"
            )


@dataclass(frozen=True)
class HistoricalSensitivityQuery:
    condition_snapshot_version: str
    mfac_context_id: str
    grid_id: str = ""
    qbase: Optional[float] = None
    inlet_so2: Optional[float] = None
    ph: Optional[float] = None
    gas_flow: Optional[float] = None
    outlet_so2: Optional[float] = None

    def feature_values(self) -> Dict[str, Optional[float]]:
        return {
            name: _finite(getattr(self, name))
            for name in _FEATURE_NAMES
        }


@dataclass(frozen=True)
class HistoricalSensitivitySurface:
    profile_id: str
    condition_snapshot_version: str
    mfac_context_id: str
    grid_id: str
    phi_so2_prior: float
    phi_ph_prior: float
    confidence_so2: float
    confidence_ph: float
    event_count: int
    independent_days: int
    feature_center: Dict[str, float] = field(default_factory=dict)
    feature_scale: Dict[str, float] = field(default_factory=dict)
    support_min: Dict[str, float] = field(default_factory=dict)
    support_max: Dict[str, float] = field(default_factory=dict)
    phi_so2_coefficients: Dict[str, float] = field(default_factory=dict)
    phi_ph_coefficients: Dict[str, float] = field(default_factory=dict)
    condition_labels: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = HISTORICAL_SENSITIVITY_SURFACE_VERSION

    def __post_init__(self) -> None:
        if not str(self.profile_id or "").strip():
            raise ValueError("profile_id is required")
        if not str(self.condition_snapshot_version or "").strip():
            raise ValueError("condition_snapshot_version is required")
        so2 = _finite(self.phi_so2_prior)
        ph = _finite(self.phi_ph_prior)
        if so2 is None or so2 >= 0.0:
            raise ValueError("phi_so2_prior must remain negative")
        if ph is None or ph <= 0.0:
            raise ValueError("phi_ph_prior must remain positive")
        for name in ("confidence_so2", "confidence_ph"):
            value = _finite(getattr(self, name))
            if value is None or not 0.0 <= value <= 1.0:
                raise ValueError("%s must be finite within [0, 1]" % name)
        if int(self.event_count) < 1:
            raise ValueError("event_count must be >= 1")
        if int(self.independent_days) < 1:
            raise ValueError("independent_days must be >= 1")

        for name in _FEATURE_NAMES:
            center = self.feature_center.get(name)
            scale = self.feature_scale.get(name)
            low = self.support_min.get(name)
            high = self.support_max.get(name)
            values = (center, scale, low, high)
            if all(value is None for value in values):
                continue
            if any(_finite(value) is None for value in values):
                raise ValueError("feature %s support contract is incomplete" % name)
            if float(scale) <= 0.0:
                raise ValueError("feature %s scale must be > 0" % name)
            if float(low) > float(high):
                raise ValueError("feature %s support_min must be <= support_max" % name)
        for coefficients in (
            self.phi_so2_coefficients,
            self.phi_ph_coefficients,
        ):
            for name, value in coefficients.items():
                if name not in _FEATURE_NAMES or _finite(value) is None:
                    raise ValueError("invalid sensitivity coefficient %s" % name)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["condition_labels"] = list(self.condition_labels)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HistoricalSensitivitySurface":
        data = dict(value)
        data["condition_labels"] = tuple(data.get("condition_labels") or ())
        return cls(**data)

    def evaluate(
        self,
        query: HistoricalSensitivityQuery,
    ) -> Optional[Dict[str, Any]]:
        if query.condition_snapshot_version != self.condition_snapshot_version:
            return None

        features = query.feature_values()
        so2 = float(self.phi_so2_prior)
        ph = float(self.phi_ph_prior)
        outside_components: Dict[str, float] = {}
        used_features: List[str] = []

        for name in _FEATURE_NAMES:
            value = features.get(name)
            if value is None:
                continue
            if name in self.feature_center:
                center = float(self.feature_center[name])
                scale = float(self.feature_scale[name])
                normalized = (value - center) / scale
                so2 += float(self.phi_so2_coefficients.get(name, 0.0)) * normalized
                ph += float(self.phi_ph_coefficients.get(name, 0.0)) * normalized
                used_features.append(name)

                low = float(self.support_min[name])
                high = float(self.support_max[name])
                if value < low:
                    outside_components[name] = (low - value) / scale
                elif value > high:
                    outside_components[name] = (value - high) / scale

        if not math.isfinite(so2) or not math.isfinite(ph) or so2 >= 0.0 or ph <= 0.0:
            return None

        extrapolation_distance = math.sqrt(
            sum(value * value for value in outside_components.values())
        )
        penalty = 1.0 / (1.0 + extrapolation_distance)
        return {
            "phi_so2": so2,
            "phi_ph": ph,
            "confidence_so2": float(self.confidence_so2) * penalty,
            "confidence_ph": float(self.confidence_ph) * penalty,
            "extrapolation_distance": extrapolation_distance,
            "extrapolated": bool(outside_components),
            "outside_support": dict(outside_components),
            "used_features": used_features,
        }


@dataclass(frozen=True)
class HistoricalSensitivityDecision:
    status: str
    available: bool
    phi_so2: Optional[float]
    phi_ph: Optional[float]
    confidence_so2: float
    confidence_ph: float
    mapping_source: str
    source_profile_ids: Tuple[str, ...]
    extrapolated: bool
    extrapolation_distance: float
    reason_codes: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = HISTORICAL_SENSITIVITY_MAP_VERSION

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["source_profile_ids"] = list(self.source_profile_ids)
        value["reason_codes"] = list(self.reason_codes)
        return value


class HistoricalSensitivityMap:
    """Resolve context/work-point historical priors without exact-state lookup."""

    def __init__(
        self,
        condition_snapshot_version: str,
        profiles: Iterable[HistoricalSensitivitySurface],
        config: HistoricalSensitivityMapConfig,
        *,
        pooled_profile: Optional[HistoricalSensitivitySurface] = None,
    ) -> None:
        snapshot = str(condition_snapshot_version or "").strip()
        if not snapshot:
            raise ValueError("condition_snapshot_version is required")
        self.condition_snapshot_version = snapshot
        self.config = config
        self.profiles = tuple(profiles)
        self.pooled_profile = pooled_profile
        for profile in self.profiles:
            if profile.condition_snapshot_version != snapshot:
                raise ValueError("profile snapshot mismatch")
        if pooled_profile is not None and pooled_profile.condition_snapshot_version != snapshot:
            raise ValueError("pooled profile snapshot mismatch")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "semantics_version": HISTORICAL_SENSITIVITY_MAP_VERSION,
            "condition_snapshot_version": self.condition_snapshot_version,
            "config": asdict(self.config),
            "profiles": [profile.to_dict() for profile in self.profiles],
            "pooled_profile": (
                self.pooled_profile.to_dict()
                if self.pooled_profile is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HistoricalSensitivityMap":
        data = dict(value)
        if data.get("semantics_version") != HISTORICAL_SENSITIVITY_MAP_VERSION:
            raise ValueError("historical sensitivity map semantics mismatch")
        return cls(
            condition_snapshot_version=str(data.get("condition_snapshot_version") or ""),
            profiles=[
                HistoricalSensitivitySurface.from_dict(item)
                for item in data.get("profiles") or []
            ],
            config=HistoricalSensitivityMapConfig(**dict(data.get("config") or {})),
            pooled_profile=(
                HistoricalSensitivitySurface.from_dict(data["pooled_profile"])
                if data.get("pooled_profile") is not None
                else None
            ),
        )

    def resolve(self, query: HistoricalSensitivityQuery) -> HistoricalSensitivityDecision:
        if query.condition_snapshot_version != self.condition_snapshot_version:
            return self._unavailable("SNAPSHOT_MISMATCH")

        exact_context = [
            profile
            for profile in self.profiles
            if profile.mfac_context_id
            and profile.mfac_context_id == query.mfac_context_id
        ]
        decision = self._best_or_blend(
            query,
            exact_context,
            mapping_source="EXACT_CONTEXT",
            mapping_penalty=1.0,
        )
        if decision is not None:
            return decision

        exact_grid = [
            profile
            for profile in self.profiles
            if query.grid_id and profile.grid_id == query.grid_id
        ]
        decision = self._best_or_blend(
            query,
            exact_grid,
            mapping_source="EXACT_GRID",
            mapping_penalty=1.0,
        )
        if decision is not None:
            return decision

        query_coord = _grid_coordinate(query.grid_id)
        if query_coord is not None:
            neighbors: List[Tuple[int, HistoricalSensitivitySurface]] = []
            for profile in self.profiles:
                coord = _grid_coordinate(profile.grid_id)
                if coord is None:
                    continue
                distance = abs(coord[0] - query_coord[0]) + abs(coord[1] - query_coord[1])
                if 1 <= distance <= int(self.config.max_neighbor_grid_distance):
                    neighbors.append((distance, profile))
            if neighbors:
                min_distance = min(item[0] for item in neighbors)
                nearest = [profile for distance, profile in neighbors if distance == min_distance]
                decision = self._best_or_blend(
                    query,
                    nearest,
                    mapping_source="NEIGHBOR_INTERPOLATED",
                    mapping_penalty=(
                        float(self.config.neighbor_confidence_penalty) ** min_distance
                    ),
                    grid_distance=min_distance,
                )
                if decision is not None:
                    return decision

        if self.pooled_profile is not None:
            decision = self._best_or_blend(
                query,
                [self.pooled_profile],
                mapping_source="POOLED_FALLBACK",
                mapping_penalty=float(self.config.pooled_confidence_penalty),
            )
            if decision is not None:
                return decision

        return self._unavailable("NO_SUPPORTED_HISTORICAL_PRIOR")

    def _best_or_blend(
        self,
        query: HistoricalSensitivityQuery,
        profiles: Sequence[HistoricalSensitivitySurface],
        *,
        mapping_source: str,
        mapping_penalty: float,
        grid_distance: int = 0,
    ) -> Optional[HistoricalSensitivityDecision]:
        evaluated = []
        for profile in profiles:
            value = profile.evaluate(query)
            if value is None:
                continue
            if (
                float(value["extrapolation_distance"])
                > float(self.config.max_profile_extrapolation_distance)
            ):
                continue
            weight = max(
                1e-12,
                0.5 * (
                    float(value["confidence_so2"])
                    + float(value["confidence_ph"])
                ),
            )
            evaluated.append((profile, value, weight))
        if not evaluated:
            return None

        total_weight = sum(item[2] for item in evaluated)
        phi_so2 = sum(item[1]["phi_so2"] * item[2] for item in evaluated) / total_weight
        phi_ph = sum(item[1]["phi_ph"] * item[2] for item in evaluated) / total_weight
        confidence_so2 = (
            sum(item[1]["confidence_so2"] * item[2] for item in evaluated)
            / total_weight
            * mapping_penalty
        )
        confidence_ph = (
            sum(item[1]["confidence_ph"] * item[2] for item in evaluated)
            / total_weight
            * mapping_penalty
        )
        extrapolated = any(bool(item[1]["extrapolated"]) for item in evaluated)
        max_distance = max(float(item[1]["extrapolation_distance"]) for item in evaluated)
        reasons = []
        if mapping_source != "EXACT_CONTEXT":
            reasons.append(mapping_source)
        if extrapolated:
            reasons.append("WORKPOINT_EXTRAPOLATED")
        return HistoricalSensitivityDecision(
            status="RESOLVED",
            available=True,
            phi_so2=float(phi_so2),
            phi_ph=float(phi_ph),
            confidence_so2=max(0.0, min(1.0, confidence_so2)),
            confidence_ph=max(0.0, min(1.0, confidence_ph)),
            mapping_source=mapping_source,
            source_profile_ids=tuple(item[0].profile_id for item in evaluated),
            extrapolated=extrapolated or mapping_source != "EXACT_CONTEXT",
            extrapolation_distance=max_distance,
            reason_codes=tuple(reasons),
            metadata={
                "grid_distance": int(grid_distance),
                "profile_count": len(evaluated),
                "historical_prior_only": True,
                "online_state_has_priority": True,
                "qbase_availability_independent": True,
            },
        )

    @staticmethod
    def _unavailable(reason: str) -> HistoricalSensitivityDecision:
        return HistoricalSensitivityDecision(
            status="UNAVAILABLE",
            available=False,
            phi_so2=None,
            phi_ph=None,
            confidence_so2=0.0,
            confidence_ph=0.0,
            mapping_source="NONE",
            source_profile_ids=(),
            extrapolated=False,
            extrapolation_distance=0.0,
            reason_codes=(str(reason),),
            metadata={
                "historical_prior_only": True,
                "qbase_availability_independent": True,
            },
        )


__all__ = [
    "HISTORICAL_SENSITIVITY_MAP_VERSION",
    "HISTORICAL_SENSITIVITY_SURFACE_VERSION",
    "HistoricalSensitivityMapConfig",
    "HistoricalSensitivityQuery",
    "HistoricalSensitivitySurface",
    "HistoricalSensitivityDecision",
    "HistoricalSensitivityMap",
]
