# -*- coding: utf-8 -*-
"""Fail-closed configuration/builder for the formal Scheme-2 MFAC runtime.

Plant facts stay in ``PLANT_CONFIG``. Runtime configuration owns only calibrated
algorithm/dynamic parameters. The current repository default remains disabled,
and production permission remains LEARN=0, Residual=0, DCS write=off.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from system.model.config.mfac_paths import MFAC_RUNTIME_DIR
from system.model.config.mfac_plant_contract import (
    ph_arbitration_plant_values,
    target_supply_flow_contract,
)

from .continuous_target import ContinuousTargetConfig
from .flow_trajectory_planner import FlowTrajectoryPlannerConfig
from .mfac_eligibility import MFACEligibilityConfig
from .online_adaptation import MFACOnlineAdaptationConfig
from .pending_dose_guard import PendingDoseGuardConfig
from .ph_adaptation import PHOnlineAdaptationConfig
from .ph_arbitration import PHResidualArbitrationConfig
from .ph_response import PHResponseConfig
from .process_response import ProcessResponseConfig
from .residual_control import MFACResidualConfig
from .runtime_coordinator import Scheme2RuntimeCoordinator
from .runtime_store import Scheme2RuntimeStore
from .supply_flow_tracking import SupplyFlowTrackingConfig
from .trajectory_coordinator import Scheme2TrajectoryShadowCoordinator


MFAC_RUNTIME_CONFIG_VERSION = "SCHEME2_MFAC_RUNTIME_CONFIG_V4_PENDING_TRAJECTORY"
DEFAULT_RUNTIME_DIR = MFAC_RUNTIME_DIR

DEFAULT_MFAC_RUNTIME_CONFIG: Dict[str, Any] = {
    "config_version": MFAC_RUNTIME_CONFIG_VERSION,
    "enabled": False,
    "status": "DISABLED_UNCALIBRATED",
    "learning_enabled": False,
    "residual_control_enabled": False,
    "dcs_write_enabled": False,
    "persist_runtime": True,
    "runtime_dir": str(DEFAULT_RUNTIME_DIR),
    "startup_setpoint_target": None,
    "continuous_target": {},
    "tracking": {},
    "so2_response": {},
    "so2_adaptation": {},
    "residual": {},
    "ph_response": {},
    "ph_adaptation": {},
    "ph_arbitration": {},
    # Dynamic pH-memory calibration. These are intentionally empty until
    # historical/field evidence freezes onset/peak/memory parameters.
    "pending_dose": {},
    # Staircase shaping parameters. Empty by default: no guessed production
    # step size or hold duration is hidden in code.
    "trajectory_planner": {},
    "eligibility": {},
}


_REQUIRED_FIELDS: Dict[str, Tuple[str, ...]] = {
    "tracking": (
        "target_change_deadband",
        "reach_tolerance",
        "required_sustain_seconds",
        "execution_timeout_seconds",
        "max_sample_gap_seconds",
    ),
    "so2_response": (
        "baseline_window_seconds",
        "delay_onset_seconds",
        "observation_seconds",
        "measurement_window_seconds",
        "max_sample_gap_seconds",
        "target_change_tolerance",
        "min_baseline_samples",
        "min_response_samples",
    ),
    "so2_adaptation": (
        "eta",
        "mu",
        "phi_lower_bound",
        "phi_upper_bound",
        "max_single_update_abs",
    ),
    "residual": (
        "rho",
        "lambda_regularization",
        "max_abs_residual",
        "min_confidence",
    ),
    "ph_response": (
        "baseline_window_seconds",
        "delay_onset_seconds",
        "observation_seconds",
        "measurement_window_seconds",
        "max_sample_gap_seconds",
        "target_change_tolerance",
        "min_baseline_samples",
        "min_response_samples",
    ),
    "ph_adaptation": (
        "eta",
        "mu",
        "phi_lower_bound",
        "phi_upper_bound",
        "max_single_update_abs",
    ),
    "pending_dose": (
        "flow_change_deadband",
        "response_onset_seconds",
        "response_peak_seconds",
        "response_memory_seconds",
        "max_sample_gap_seconds",
    ),
    "trajectory_planner": (
        "max_step_up",
        "max_step_down",
        "min_hold_seconds",
    ),
}

_PLANT_OWNED_TARGET_FIELDS = {
    "hard_min_supply_flow",
    "hard_max_supply_flow",
}
_PLANT_OWNED_PH_FIELDS = {
    "operating_min",
    "operating_max",
    "safe_min",
    "safe_max",
    "guard_band",
}


@dataclass
class MFACRuntimeBuildResult:
    configured: bool
    status: str
    coordinator: Optional[Scheme2RuntimeCoordinator] = None
    error: str = ""
    missing_fields: Tuple[str, ...] = ()
    config_version: str = MFAC_RUNTIME_CONFIG_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "configured": bool(self.configured),
            "status": self.status,
            "error": self.error,
            "missing_fields": list(self.missing_fields),
            "config_version": self.config_version,
            "learn_enabled": False,
            "residual_enabled": False,
            "dcs_write_enabled": False,
        }


def _as_mapping(value: Any, name: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("%s must be a mapping" % name)
    return dict(value)


def _missing_calibration_fields(config: Mapping[str, Any]) -> Tuple[str, ...]:
    missing = []
    for section, fields in _REQUIRED_FIELDS.items():
        values = _as_mapping(config.get(section), section)
        for field_name in fields:
            if field_name not in values or values.get(field_name) in (None, ""):
                missing.append("%s.%s" % (section, field_name))
    return tuple(missing)


def _eligibility_config(value: Mapping[str, Any]) -> MFACEligibilityConfig:
    payload = dict(value or {})
    if "allowed_shapes" in payload:
        payload["allowed_shapes"] = tuple(payload["allowed_shapes"])
    if "allowed_disturbance_classes" in payload:
        payload["allowed_disturbance_classes"] = tuple(
            payload["allowed_disturbance_classes"]
        )
    return MFACEligibilityConfig(**payload)


def _continuous_target_config(value: Mapping[str, Any]) -> ContinuousTargetConfig:
    payload = dict(value or {})
    overrides = sorted(_PLANT_OWNED_TARGET_FIELDS.intersection(payload))
    if overrides:
        raise ValueError(
            "continuous_target cannot override plant-owned fields: %s"
            % ", ".join(overrides)
        )
    contract = target_supply_flow_contract()
    return ContinuousTargetConfig(
        hard_min_supply_flow=float(contract["minimum"]),
        hard_max_supply_flow=float(contract["maximum"]),
    )


def _ph_arbitration_config(value: Mapping[str, Any]) -> PHResidualArbitrationConfig:
    payload = dict(value or {})
    overrides = sorted(_PLANT_OWNED_PH_FIELDS.intersection(payload))
    if overrides:
        raise ValueError(
            "ph_arbitration cannot override plant-owned fields: %s"
            % ", ".join(overrides)
        )
    plant_values = ph_arbitration_plant_values()
    plant_values.update(payload)
    return PHResidualArbitrationConfig(**plant_values)


def build_mfac_runtime(
    config: Optional[Mapping[str, Any]] = None,
) -> MFACRuntimeBuildResult:
    """Build the calibrated dual-response + trajectory Shadow runtime."""
    value = deepcopy(DEFAULT_MFAC_RUNTIME_CONFIG)
    if config is not None:
        for key, item in dict(config).items():
            value[key] = deepcopy(item)

    if not bool(value.get("enabled", False)):
        return MFACRuntimeBuildResult(
            configured=False,
            status=str(value.get("status") or "DISABLED_UNCALIBRATED"),
        )

    if bool(value.get("learning_enabled", False)):
        raise ValueError("MFAC runtime LEARN must remain 0")
    if bool(value.get("residual_control_enabled", False)):
        raise ValueError("MFAC runtime Residual must remain 0")
    if bool(value.get("dcs_write_enabled", False)):
        raise ValueError("MFAC runtime DCS write must remain off")

    missing = _missing_calibration_fields(value)
    if missing:
        return MFACRuntimeBuildResult(
            configured=False,
            status="INVALID_INCOMPLETE_CALIBRATION",
            error="required MFAC calibration is incomplete",
            missing_fields=missing,
        )

    try:
        tracking = SupplyFlowTrackingConfig(
            **_as_mapping(value.get("tracking"), "tracking")
        )
        so2_response = ProcessResponseConfig(
            **_as_mapping(value.get("so2_response"), "so2_response")
        )
        so2_adaptation = MFACOnlineAdaptationConfig(
            **_as_mapping(value.get("so2_adaptation"), "so2_adaptation")
        )
        residual = MFACResidualConfig(
            **_as_mapping(value.get("residual"), "residual")
        )
        ph_response = PHResponseConfig(
            **_as_mapping(value.get("ph_response"), "ph_response")
        )
        ph_adaptation = PHOnlineAdaptationConfig(
            **_as_mapping(value.get("ph_adaptation"), "ph_adaptation")
        )
        ph_arbitration = _ph_arbitration_config(
            _as_mapping(value.get("ph_arbitration"), "ph_arbitration")
        )
        pending_dose = PendingDoseGuardConfig(
            **_as_mapping(value.get("pending_dose"), "pending_dose")
        )
        trajectory_planner = FlowTrajectoryPlannerConfig(
            **_as_mapping(value.get("trajectory_planner"), "trajectory_planner")
        )
        continuous_target = _continuous_target_config(
            _as_mapping(value.get("continuous_target"), "continuous_target")
        )
        eligibility = _eligibility_config(
            _as_mapping(value.get("eligibility"), "eligibility")
        )

        runtime_dir = str(value.get("runtime_dir") or DEFAULT_RUNTIME_DIR).strip()
        if not runtime_dir:
            raise ValueError("runtime_dir cannot be empty")
        persist_runtime = bool(value.get("persist_runtime", True))
        store = Scheme2RuntimeStore(runtime_dir, enabled=persist_runtime)
        from .runtime_coordinator import Scheme2RuntimeCoordinatorConfig
        coordinator_config = Scheme2RuntimeCoordinatorConfig(
            tracking=tracking,
            response=so2_response,
            online_adaptation=so2_adaptation,
            residual=residual,
            continuous_target=continuous_target,
            eligibility=eligibility,
            ph_response=ph_response,
            ph_online_adaptation=ph_adaptation,
            ph_arbitration=ph_arbitration,
            learning_enabled=False,
            residual_control_enabled=False,
            persist_runtime=persist_runtime,
        )
        coordinator = Scheme2TrajectoryShadowCoordinator(
            coordinator_config,
            store,
            pending_dose_config=pending_dose,
            trajectory_planner_config=trajectory_planner,
            startup_setpoint_target=value.get("startup_setpoint_target"),
        )
    except Exception as exc:
        return MFACRuntimeBuildResult(
            configured=False,
            status="INVALID_CALIBRATION_CONFIG",
            error=str(exc),
        )

    return MFACRuntimeBuildResult(
        configured=True,
        status="CONFIGURED_TRAJECTORY_SHADOW",
        coordinator=coordinator,
    )


__all__ = [
    "MFAC_RUNTIME_CONFIG_VERSION",
    "DEFAULT_MFAC_RUNTIME_CONFIG",
    "MFACRuntimeBuildResult",
    "build_mfac_runtime",
]
