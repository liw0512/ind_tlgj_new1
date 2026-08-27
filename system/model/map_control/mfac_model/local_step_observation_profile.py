# -*- coding: utf-8 -*-
"""Reviewed observation boundary for manual LOCAL_GAIN trials.

The proposal/evidence protocol must not silently borrow tracking or response
windows from the production MFAC runtime.  This module owns a separate,
manual-only observation profile that can instantiate the existing
SupplyFlowTrackingMonitor, ProcessResponseMonitor and PHResponseMonitor only
when every required parameter has been explicitly reviewed.

It has no actuator API and cannot activate the normal runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple, Union

from .ph_response import PHResponseConfig, PHResponseMonitor
from .process_response import ProcessResponseConfig, ProcessResponseMonitor
from .supply_flow_tracking import SupplyFlowTrackingConfig, SupplyFlowTrackingMonitor


LOCAL_STEP_OBSERVATION_PROFILE_VERSION = (
    "SCHEME2_LOCAL_STEP_OBSERVATION_PROFILE_V1_MANUAL_ONLY"
)

_TRACKING_KEYS: Tuple[str, ...] = (
    "target_change_deadband",
    "reach_tolerance",
    "required_sustain_seconds",
    "execution_timeout_seconds",
    "max_sample_gap_seconds",
)
_RESPONSE_KEYS: Tuple[str, ...] = (
    "baseline_window_seconds",
    "delay_onset_seconds",
    "observation_seconds",
    "measurement_window_seconds",
    "max_sample_gap_seconds",
    "target_change_tolerance",
    "min_baseline_samples",
    "min_response_samples",
)


@dataclass(frozen=True)
class LocalStepObservationMonitors:
    tracking: SupplyFlowTrackingMonitor
    so2_response: ProcessResponseMonitor
    ph_response: PHResponseMonitor
    profile_id: str
    manual_only: bool = True
    automatic_execution_allowed: bool = False
    dcs_write_enabled: bool = False
    normal_runtime_activation_allowed: bool = False


@dataclass(frozen=True)
class LocalStepObservationProfile:
    profile_id: str
    status: str
    activation_status: str
    automatic_execution_allowed: bool = False
    dcs_write_enabled: bool = False
    review_candidate_tracking: Dict[str, Any] = field(default_factory=dict)
    review_candidate_so2_response: Dict[str, Any] = field(default_factory=dict)
    review_candidate_ph_response: Dict[str, Any] = field(default_factory=dict)
    reviewed_tracking: Dict[str, Any] = field(default_factory=dict)
    reviewed_so2_response: Dict[str, Any] = field(default_factory=dict)
    reviewed_ph_response: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = LOCAL_STEP_OBSERVATION_PROFILE_VERSION

    def __post_init__(self) -> None:
        if not str(self.profile_id or "").strip():
            raise ValueError("profile_id is required")
        if str(self.activation_status) != "NOT_ACTIVATABLE":
            raise ValueError("local-step observation profile must remain NOT_ACTIVATABLE")
        if bool(self.automatic_execution_allowed):
            raise ValueError("local-step observation cannot enable automatic execution")
        if bool(self.dcs_write_enabled):
            raise ValueError("local-step observation cannot enable DCS write")
        if str(self.semantics_version) != LOCAL_STEP_OBSERVATION_PROFILE_VERSION:
            raise ValueError("unsupported local-step observation profile semantics")

    @staticmethod
    def _missing(mapping: Mapping[str, Any], keys: Tuple[str, ...], prefix: str):
        values = dict(mapping or {})
        return tuple(
            "%s.%s" % (prefix, key)
            for key in keys
            if values.get(key) is None
        )

    @property
    def missing_reviewed_keys(self) -> Tuple[str, ...]:
        return (
            self._missing(self.reviewed_tracking, _TRACKING_KEYS, "tracking")
            + self._missing(self.reviewed_so2_response, _RESPONSE_KEYS, "so2_response")
            + self._missing(self.reviewed_ph_response, _RESPONSE_KEYS, "ph_response")
        )

    @property
    def reviewed_complete(self) -> bool:
        return not self.missing_reviewed_keys

    @property
    def can_build_monitors(self) -> bool:
        return (
            self.reviewed_complete
            and str(self.status) == "REVIEWED_MANUAL_ONLY"
            and str(self.activation_status) == "NOT_ACTIVATABLE"
            and not self.automatic_execution_allowed
            and not self.dcs_write_enabled
        )

    def build_monitors(self) -> LocalStepObservationMonitors:
        if not self.can_build_monitors:
            raise ValueError(
                "local-step observation profile is not fully reviewed; "
                "status=%s missing=%s"
                % (self.status, ",".join(self.missing_reviewed_keys))
            )
        tracking_config = SupplyFlowTrackingConfig(**dict(self.reviewed_tracking))
        so2_config = ProcessResponseConfig(**dict(self.reviewed_so2_response))
        ph_config = PHResponseConfig(**dict(self.reviewed_ph_response))
        return LocalStepObservationMonitors(
            tracking=SupplyFlowTrackingMonitor(tracking_config),
            so2_response=ProcessResponseMonitor(so2_config),
            ph_response=PHResponseMonitor(ph_config),
            profile_id=self.profile_id,
        )

    def to_runtime_config(self) -> Dict[str, Any]:
        raise ValueError(
            "manual local-step observation profile cannot activate normal MFAC runtime"
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "LocalStepObservationProfile":
        data = dict(payload or {})
        candidates = dict(data.get("review_candidate_parameters") or {})
        reviewed = dict(data.get("reviewed_parameters") or {})
        return cls(
            profile_id=str(data.get("profile_id") or ""),
            status=str(data.get("status") or ""),
            activation_status=str(data.get("activation_status") or ""),
            automatic_execution_allowed=bool(
                data.get("automatic_execution_allowed", False)
            ),
            dcs_write_enabled=bool(data.get("dcs_write_enabled", False)),
            review_candidate_tracking=dict(candidates.get("tracking") or {}),
            review_candidate_so2_response=dict(candidates.get("so2_response") or {}),
            review_candidate_ph_response=dict(candidates.get("ph_response") or {}),
            reviewed_tracking=dict(reviewed.get("tracking") or {}),
            reviewed_so2_response=dict(reviewed.get("so2_response") or {}),
            reviewed_ph_response=dict(reviewed.get("ph_response") or {}),
            metadata={
                "source_profile_id": data.get("source_profile_id"),
                "source_noise_audit_id": data.get("source_noise_audit_id"),
                "notes": list(data.get("notes") or []),
            },
            semantics_version=str(
                data.get("semantics_version")
                or LOCAL_STEP_OBSERVATION_PROFILE_VERSION
            ),
        )


def load_local_step_observation_profile(
    path: Union[str, Path],
) -> LocalStepObservationProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("local-step observation JSON must contain an object")
    return LocalStepObservationProfile.from_mapping(payload)


__all__ = [
    "LOCAL_STEP_OBSERVATION_PROFILE_VERSION",
    "LocalStepObservationMonitors",
    "LocalStepObservationProfile",
    "load_local_step_observation_profile",
]
