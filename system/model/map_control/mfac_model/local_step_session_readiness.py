# -*- coding: utf-8 -*-
"""Unified readiness gate for supervised Scheme-2 LOCAL_GAIN sessions.

Readiness is deliberately stricter than any individual configuration object.
A manual identification session may be considered only when:

1. the identification/safety design is fully reviewed;
2. the independent tracking + SO2/pH observation profile is fully reviewed;
3. the requested trial-matrix level is reviewed and has explicit evidence
   requirements.

This module is read-only.  It does not schedule, execute or approve a plant
command and cannot grant runtime learning or DCS authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Tuple

from .local_step_design_profile import LocalStepIdentificationDesignProfile
from .local_step_observation_profile import LocalStepObservationProfile
from .local_step_trial_matrix import LocalStepTrialMatrix


LOCAL_STEP_SESSION_READINESS_VERSION = (
    "SCHEME2_LOCAL_STEP_SESSION_READINESS_V1_THREE_GATE"
)


@dataclass(frozen=True)
class LocalStepSessionReadiness:
    ready: bool
    status: str
    level_id: str
    blockers: Tuple[str, ...] = ()
    design_ready: bool = False
    observation_ready: bool = False
    matrix_level_ready: bool = False
    automatic_execution_allowed: bool = False
    dcs_write_enabled: bool = False
    learning_permission: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = LOCAL_STEP_SESSION_READINESS_VERSION

    def __post_init__(self) -> None:
        if bool(self.automatic_execution_allowed):
            raise ValueError("session readiness cannot enable automatic execution")
        if bool(self.dcs_write_enabled):
            raise ValueError("session readiness cannot enable DCS write")
        if bool(self.learning_permission):
            raise ValueError("session readiness cannot grant learning permission")

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["blockers"] = list(self.blockers)
        return value


def evaluate_local_step_session_readiness(
    design: LocalStepIdentificationDesignProfile,
    observation: LocalStepObservationProfile,
    matrix: LocalStepTrialMatrix,
    *,
    level_id: str,
) -> LocalStepSessionReadiness:
    blockers = []

    design_ready = bool(design.can_build_manual_trial_configs)
    if not design_ready:
        blockers.append("IDENTIFICATION_DESIGN_NOT_REVIEWED")
        blockers.extend(
            "DESIGN_MISSING:%s" % key for key in design.missing_reviewed_keys
        )

    observation_ready = bool(observation.can_build_monitors)
    if not observation_ready:
        blockers.append("OBSERVATION_PROFILE_NOT_REVIEWED")
        blockers.extend(
            "OBSERVATION_MISSING:%s" % key
            for key in observation.missing_reviewed_keys
        )

    level = next((item for item in matrix.levels if item.level_id == level_id), None)
    matrix_level_ready = bool(level is not None and level.ready_for_manual_session)
    if level is None:
        blockers.append("TRIAL_LEVEL_NOT_FOUND")
    elif not matrix_level_ready:
        blockers.append("TRIAL_LEVEL_NOT_REVIEWED")
        if not level.evidence_requirements_complete:
            if level.required_valid_trials is None:
                blockers.append("TRIAL_LEVEL_REQUIRED_VALID_TRIALS_UNRESOLVED")
            if level.required_independent_days is None:
                blockers.append("TRIAL_LEVEL_REQUIRED_INDEPENDENT_DAYS_UNRESOLVED")

    blockers = tuple(dict.fromkeys(blockers))
    ready = design_ready and observation_ready and matrix_level_ready and not blockers
    return LocalStepSessionReadiness(
        ready=ready,
        status="READY_FOR_SUPERVISED_MANUAL_SESSION" if ready else "NOT_READY",
        level_id=str(level_id or ""),
        blockers=blockers,
        design_ready=design_ready,
        observation_ready=observation_ready,
        matrix_level_ready=matrix_level_ready,
        automatic_execution_allowed=False,
        dcs_write_enabled=False,
        learning_permission=False,
        metadata={
            "design_id": design.design_id,
            "observation_profile_id": observation.profile_id,
            "matrix_id": matrix.matrix_id,
            "manual_human_approval_still_required_after_readiness": True,
            "normal_runtime_activation_allowed": False,
        },
    )


__all__ = [
    "LOCAL_STEP_SESSION_READINESS_VERSION",
    "LocalStepSessionReadiness",
    "evaluate_local_step_session_readiness",
]
