# -*- coding: utf-8 -*-
"""Fail-closed configuration/builder for the formal Scheme-2 MFAC runtime.

No plant timing, sensitivity or pH safety threshold is guessed here.  The
repository default is explicitly ``enabled=False`` / ``DISABLED_UNCALIBRATED``.
A coordinator can be constructed only after every plant-specific section is
supplied and validates through the component dataclasses.

Current production permission remains fixed at:

    LEARN = 0
    Residual = 0
    DCS write = off

Those permissions are deliberately checked again by this builder even though
the coordinator itself also carries enable flags.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .continuous_target import ContinuousTargetConfig
from .mfac_eligibility import MFACEligibilityConfig
from .online_adaptation import MFACOnlineAdaptationConfig
from .ph_adaptation import PHOnlineAdaptationConfig
from .ph_arbitration import PHResidualArbitrationConfig
from .ph_response import PHResponseConfig
from .process_response import ProcessResponseConfig
from .residual_control import MFACResidualConfig
from .runtime_coordinator import (
    Scheme2RuntimeCoordinator,
    Scheme2RuntimeCoordinatorConfig,
)
from .runtime_store import Scheme2RuntimeStore
from .supply_flow_tracking import SupplyFlowTrackingConfig


MFAC_RUNTIME_CONFIG_VERSION = "SCHEME2_MFAC_RUNTIME_CONFIG_V1"
DEFAULT_RUNTIME_DIR = (
    Path(__file__).resolve().parent / "mfac_model_output" / "runtime"
)

# Empty plant-specific sections are intentional.  They make the absence of
# calibration visible instead of silently substituting test/example values.
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
    "ph_arbitration": (
        "operating_min",
        "operating_max",
        "safe_min",
        "safe_max",
        "guard_band",
    ),
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


def build_mfac_runtime(
    config: Optional[Mapping[str, Any]] = None,
) -> MFACRuntimeBuildResult:
    """Build a calibrated Shadow coordinator or return an explicit safe state.

    ``enabled=False`` is a normal production-safe state and returns without
    validating plant-specific sections.  ``enabled=True`` requires a complete
    dual-response calibration and still refuses any request to enable learning,
    non-zero residual control or DCS writing at the current activation stage.
    """

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
            error="required plant calibration is incomplete",
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
        ph_arbitration = PHResidualArbitrationConfig(
            **_as_mapping(value.get("ph_arbitration"), "ph_arbitration")
        )
        continuous_target = ContinuousTargetConfig(
            **_as_mapping(value.get("continuous_target"), "continuous_target")
        )
        eligibility = _eligibility_config(
            _as_mapping(value.get("eligibility"), "eligibility")
        )

        runtime_dir = str(value.get("runtime_dir") or DEFAULT_RUNTIME_DIR).strip()
        if not runtime_dir:
            raise ValueError("runtime_dir cannot be empty")
        persist_runtime = bool(value.get("persist_runtime", True))
        store = Scheme2RuntimeStore(
            runtime_dir,
            enabled=persist_runtime,
        )
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
        coordinator = Scheme2RuntimeCoordinator(
            coordinator_config,
            store,
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
        status="CONFIGURED_SHADOW",
        coordinator=coordinator,
    )


__all__ = [
    "MFAC_RUNTIME_CONFIG_VERSION",
    "DEFAULT_MFAC_RUNTIME_CONFIG",
    "MFACRuntimeBuildResult",
    "build_mfac_runtime",
]
