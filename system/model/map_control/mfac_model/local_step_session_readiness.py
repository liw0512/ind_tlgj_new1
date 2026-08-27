# -*- coding: utf-8 -*-
"""Unified readiness gate for supervised Scheme-2 LOCAL_GAIN sessions.

Readiness is deliberately stricter than any individual configuration object.
A manual identification session may be considered only when:

1. the identification/safety design is fully reviewed;
2. the independent tracking + SO2/pH observation profile is fully reviewed;
3. the requested trial-matrix level is reviewed and has explicit evidence
   requirements;
4. duplicated safety/check semantics across those reviewed profiles are
   consistent instead of silently overriding each other.

This module is read-only. It does not schedule, execute or approve a plant
command and cannot grant runtime learning or DCS authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Tuple

from .local_step_design_profile import LocalStepIdentificationDesignProfile
from .local_step_observation_profile import LocalStepObservationProfile
from .local_step_trial_matrix import LocalStepTrialMatrix


LOCAL_STEP_SESSION_READINESS_VERSION = (
    "SCHEME2_LOCAL_STEP_SESSION_READINESS_V2_CROSS_PROFILE_CONSISTENCY"
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
    cross_profile_consistent: bool = False
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


def _cross_profile_blockers(
    design: LocalStepIdentificationDesignProfile,
    observation: LocalStepObservationProfile,
) -> Tuple[str, ...]:
    if not design.can_build_manual_trial_configs or not observation.can_build_monitors:
        return ()

    manual = design.build_manual_trial_configs()
    tracking = dict(observation.reviewed_tracking)
    so2 = dict(observation.reviewed_so2_response)
    ph = dict(observation.reviewed_ph_response)
    blockers = []

    # Data continuity is one physical fact. If all monitoring/evidence layers
    # use it, they must agree rather than rely on last-writer-wins semantics.
    trial_gap = float(manual.trial.max_sample_gap_seconds)
    gap_values = (
        float(tracking["max_sample_gap_seconds"]),
        float(so2["max_sample_gap_seconds"]),
        float(ph["max_sample_gap_seconds"]),
    )
    if any(abs(value - trial_gap) > 1e-9 for value in gap_values):
        blockers.append("MAX_SAMPLE_GAP_PROFILE_MISMATCH")

    # The tracker must be able to see the reviewed identification step as a
    # material target change.
    if float(tracking["target_change_deadband"]) >= float(
        manual.identification.step_up_m3_h
    ):
        blockers.append("TRACKING_DEADBAND_NOT_BELOW_IDENTIFICATION_STEP")

    # Tracking tolerance must not be looser than the later evidence gate that
    # checks whether the real delta-Q matches the reviewed test step.
    if float(tracking["reach_tolerance"]) > float(
        manual.trial.max_abs_step_error_m3_h
    ):
        blockers.append("TRACKING_REACH_TOLERANCE_EXCEEDS_TRIAL_STEP_ERROR")

    # The configured monitor horizon is measured from actual_flow_reached_time:
    # delay_onset + observation. It must cover the evidence minimum required by
    # the trial protocol; otherwise a monitor could finish before the trial is
    # even eligible for promotion.
    so2_horizon = float(so2["delay_onset_seconds"]) + float(
        so2["observation_seconds"]
    )
    ph_horizon = float(ph["delay_onset_seconds"]) + float(
        ph["observation_seconds"]
    )
    if so2_horizon < float(manual.trial.minimum_so2_observation_seconds):
        blockers.append("SO2_MONITOR_HORIZON_SHORTER_THAN_TRIAL_REQUIREMENT")
    if ph_horizon < float(manual.trial.minimum_ph_observation_seconds):
        blockers.append("PH_MONITOR_HORIZON_SHORTER_THAN_TRIAL_REQUIREMENT")

    return tuple(dict.fromkeys(blockers))


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

    consistency_blockers = _cross_profile_blockers(design, observation)
    blockers.extend(consistency_blockers)
    cross_profile_consistent = (
        design_ready and observation_ready and not consistency_blockers
    )

    blockers = tuple(dict.fromkeys(blockers))
    ready = (
        design_ready
        and observation_ready
        and matrix_level_ready
        and cross_profile_consistent
        and not blockers
    )
    return LocalStepSessionReadiness(
        ready=ready,
        status="READY_FOR_SUPERVISED_MANUAL_SESSION" if ready else "NOT_READY",
        level_id=str(level_id or ""),
        blockers=blockers,
        design_ready=design_ready,
        observation_ready=observation_ready,
        matrix_level_ready=matrix_level_ready,
        cross_profile_consistent=cross_profile_consistent,
        automatic_execution_allowed=False,
        dcs_write_enabled=False,
        learning_permission=False,
        metadata={
            "design_id": design.design_id,
            "observation_profile_id": observation.profile_id,
            "matrix_id": matrix.matrix_id,
            "manual_human_approval_still_required_after_readiness": True,
            "normal_runtime_activation_allowed": False,
            "cross_profile_semantics": {
                "max_sample_gap": "MUST_MATCH",
                "tracking_target_deadband": "MUST_BE_BELOW_TEST_STEP",
                "tracking_reach_tolerance": "MUST_NOT_EXCEED_STEP_ERROR_GATE",
                "response_horizon": "MUST_COVER_TRIAL_MINIMUM_OBSERVATION",
            },
        },
    )


__all__ = [
    "LOCAL_STEP_SESSION_READINESS_VERSION",
    "LocalStepSessionReadiness",
    "evaluate_local_step_session_readiness",
]
