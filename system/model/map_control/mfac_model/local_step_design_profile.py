# -*- coding: utf-8 -*-
"""Typed review boundary for manual LOCAL_GAIN identification design artifacts.

Historical/review-candidate values are intentionally separated from reviewed
site values. This module can build manual-only proposal/trial configs only when
every required reviewed field is present and the design status explicitly
states that manual identification has been reviewed. It never activates the
normal MFAC runtime and never enables DCS write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple, Union

from .local_step_identification import LocalStepIdentificationConfig
from .local_step_trial_protocol import LocalStepTrialProtocolConfig


LOCAL_STEP_DESIGN_PROFILE_VERSION = (
    "SCHEME2_LOCAL_STEP_DESIGN_PROFILE_V2_PERMISSION_HARDENED"
)


_GATE_KEYS: Tuple[str, ...] = (
    "step_up_m3_h",
    "ph_lower_margin_inside_operating",
    "ph_upper_margin_inside_operating",
    "outlet_so2_headroom_to_safe_max",
    "min_quiet_seconds",
    "min_candidate_interval_seconds",
    "max_abs_actual_minus_qbase",
    "max_actual_flow_baseline_range_m3_h",
    "max_abs_qbase_drift_m3_h",
    "max_relative_qbase_drift",
    "max_abs_inlet_so2_change",
    "max_outlet_so2_baseline_range_mg_nm3",
    "max_ph_baseline_range",
)

_TRIAL_KEYS: Tuple[str, ...] = (
    "max_sample_gap_seconds",
    "max_abs_step_error_m3_h",
    "max_abs_qbase_drift_m3_h",
    "max_relative_qbase_drift",
    "max_abs_inlet_so2_change",
    "min_abs_delta_so2",
    "min_abs_delta_ph",
    "minimum_so2_observation_seconds",
    "minimum_ph_observation_seconds",
    "outlet_so2_headroom_to_safe_max",
)


@dataclass(frozen=True)
class LocalStepManualConfigs:
    identification: LocalStepIdentificationConfig
    trial: LocalStepTrialProtocolConfig
    design_id: str
    review_status: str
    manual_only: bool = True
    dcs_write_enabled: bool = False
    normal_runtime_activation_allowed: bool = False


@dataclass(frozen=True)
class LocalStepIdentificationDesignProfile:
    design_id: str
    status: str
    activation_status: str
    automatic_execution_allowed: bool = False
    automatic_escalation_allowed: bool = False
    dcs_write_enabled: bool = False
    learning_permission: bool = False
    review_candidate_parameters: Dict[str, Any] = field(default_factory=dict)
    reviewed_parameters: Dict[str, Any] = field(default_factory=dict)
    trial_matrix: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = LOCAL_STEP_DESIGN_PROFILE_VERSION

    def __post_init__(self) -> None:
        if not str(self.design_id or "").strip():
            raise ValueError("design_id is required")
        if str(self.activation_status) != "NOT_ACTIVATABLE":
            raise ValueError("local-step design must remain NOT_ACTIVATABLE")
        protected_flags = {
            "automatic_execution_allowed": self.automatic_execution_allowed,
            "automatic_escalation_allowed": self.automatic_escalation_allowed,
            "dcs_write_enabled": self.dcs_write_enabled,
            "learning_permission": self.learning_permission,
        }
        enabled = [name for name, value in protected_flags.items() if bool(value)]
        if enabled:
            raise ValueError(
                "manual local-step design cannot enable: %s" % ",".join(enabled)
            )

    @property
    def required_reviewed_keys(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(_GATE_KEYS + _TRIAL_KEYS))

    @property
    def missing_reviewed_keys(self) -> Tuple[str, ...]:
        reviewed = dict(self.reviewed_parameters or {})
        return tuple(
            key
            for key in self.required_reviewed_keys
            if reviewed.get(key) is None
        )

    @property
    def reviewed_complete(self) -> bool:
        return not self.missing_reviewed_keys

    @property
    def can_build_manual_trial_configs(self) -> bool:
        return (
            self.reviewed_complete
            and str(self.status) == "REVIEWED_MANUAL_ONLY"
            and str(self.activation_status) == "NOT_ACTIVATABLE"
            and not self.automatic_execution_allowed
            and not self.automatic_escalation_allowed
            and not self.dcs_write_enabled
            and not self.learning_permission
        )

    def build_manual_trial_configs(self) -> LocalStepManualConfigs:
        if not self.can_build_manual_trial_configs:
            missing = ",".join(self.missing_reviewed_keys)
            raise ValueError(
                "local-step design is not fully reviewed for manual use; "
                "status=%s missing=%s" % (self.status, missing)
            )
        values = dict(self.reviewed_parameters)
        identification = LocalStepIdentificationConfig(
            step_up_m3_h=values["step_up_m3_h"],
            ph_lower_margin_inside_operating=values[
                "ph_lower_margin_inside_operating"
            ],
            ph_upper_margin_inside_operating=values[
                "ph_upper_margin_inside_operating"
            ],
            outlet_so2_headroom_to_safe_max=values[
                "outlet_so2_headroom_to_safe_max"
            ],
            min_quiet_seconds=values["min_quiet_seconds"],
            min_candidate_interval_seconds=values[
                "min_candidate_interval_seconds"
            ],
            max_abs_actual_minus_qbase=values["max_abs_actual_minus_qbase"],
            max_actual_flow_baseline_range=values[
                "max_actual_flow_baseline_range_m3_h"
            ],
            max_abs_qbase_drift=values["max_abs_qbase_drift_m3_h"],
            max_relative_qbase_drift=values["max_relative_qbase_drift"],
            max_abs_inlet_so2_change=values["max_abs_inlet_so2_change"],
            max_outlet_so2_baseline_range=values[
                "max_outlet_so2_baseline_range_mg_nm3"
            ],
            max_ph_baseline_range=values["max_ph_baseline_range"],
        )
        trial = LocalStepTrialProtocolConfig(
            max_sample_gap_seconds=values["max_sample_gap_seconds"],
            max_abs_step_error_m3_h=values["max_abs_step_error_m3_h"],
            max_abs_qbase_drift=values["max_abs_qbase_drift_m3_h"],
            max_relative_qbase_drift=values["max_relative_qbase_drift"],
            max_abs_inlet_so2_change=values["max_abs_inlet_so2_change"],
            min_abs_delta_so2=values["min_abs_delta_so2"],
            min_abs_delta_ph=values["min_abs_delta_ph"],
            minimum_so2_observation_seconds=values[
                "minimum_so2_observation_seconds"
            ],
            minimum_ph_observation_seconds=values[
                "minimum_ph_observation_seconds"
            ],
            outlet_so2_abort_headroom_to_safe_max=values[
                "outlet_so2_headroom_to_safe_max"
            ],
        )
        return LocalStepManualConfigs(
            identification=identification,
            trial=trial,
            design_id=self.design_id,
            review_status=self.status,
        )

    def to_runtime_config(self) -> Dict[str, Any]:
        raise ValueError(
            "manual local-step identification design cannot activate normal MFAC runtime"
        )

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
    ) -> "LocalStepIdentificationDesignProfile":
        data = dict(payload or {})
        return cls(
            design_id=str(data.get("design_id") or ""),
            status=str(data.get("status") or ""),
            activation_status=str(data.get("activation_status") or ""),
            automatic_execution_allowed=bool(
                data.get("automatic_execution_allowed", False)
            ),
            automatic_escalation_allowed=bool(
                data.get("automatic_escalation_allowed", False)
            ),
            dcs_write_enabled=bool(data.get("dcs_write_enabled", False)),
            learning_permission=bool(data.get("learning_permission", False)),
            review_candidate_parameters=dict(
                data.get("review_candidate_parameters") or {}
            ),
            reviewed_parameters=dict(data.get("reviewed_parameters") or {}),
            trial_matrix=dict(data.get("trial_matrix") or {}),
            metadata={
                "source_profile_id": data.get("source_profile_id"),
                "source_semantics_version": data.get("semantics_version"),
                "trial_protocol_version": data.get("trial_protocol_version"),
                "trial_matrix_version": data.get("trial_matrix_version"),
            },
        )


def load_local_step_design_profile(
    path: Union[str, Path],
) -> LocalStepIdentificationDesignProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("local-step design JSON must contain an object")
    return LocalStepIdentificationDesignProfile.from_mapping(payload)


__all__ = [
    "LOCAL_STEP_DESIGN_PROFILE_VERSION",
    "LocalStepManualConfigs",
    "LocalStepIdentificationDesignProfile",
    "load_local_step_design_profile",
]
