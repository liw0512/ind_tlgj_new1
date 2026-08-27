# -*- coding: utf-8 -*-
"""Non-authorizing activation-readiness review for Scheme-2 dual response.

Calibration evidence must never self-authorize production behavior. This module
only evaluates whether a fully calibrated profile and surrounding engineering
reviews are complete enough to enter a later human activation review.

Even a READY result keeps LEARN=0, Residual=0 and DCS write=off. There is no
approval/enable API in this V1 module and no runtime config conversion.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Tuple

from .dual_response_calibration_profile import DualResponseCalibrationProfile


DUAL_RESPONSE_ACTIVATION_REVIEW_VERSION = (
    "SCHEME2_DUAL_RESPONSE_ACTIVATION_REVIEW_V1_NO_PERMISSION"
)


@dataclass(frozen=True)
class DualResponseActivationPrerequisites:
    """Explicit review facts supplied by the engineering review process."""

    expected_condition_snapshot_version: str
    expected_mfac_context_id: str
    plant_contract_match_reviewed: bool
    runtime_parameter_reviewed: bool
    shadow_validation_reviewed: bool
    causal_target_application_reviewed: bool
    persistence_restore_reviewed: bool
    rollback_plan_reviewed: bool

    def __post_init__(self) -> None:
        if not str(self.expected_condition_snapshot_version or "").strip():
            raise ValueError("expected_condition_snapshot_version is required")
        if not str(self.expected_mfac_context_id or "").strip():
            raise ValueError("expected_mfac_context_id is required")


@dataclass(frozen=True)
class DualResponseActivationReadiness:
    status: str
    blockers: Tuple[str, ...]
    profile_id: str
    condition_snapshot_version: str
    mfac_context_id: str
    so2_calibrated: bool
    ph_calibrated: bool
    profile_load_evidence_ready: bool
    online_learning_evidence_ready: bool
    residual_control_evidence_ready: bool
    ready_for_human_activation_review: bool
    learning_enabled: bool = False
    residual_control_enabled: bool = False
    dcs_write_enabled: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = DUAL_RESPONSE_ACTIVATION_REVIEW_VERSION

    def __post_init__(self) -> None:
        if self.learning_enabled or self.residual_control_enabled or self.dcs_write_enabled:
            raise ValueError("activation readiness cannot grant runtime permissions")
        expected_status = (
            "READY_FOR_HUMAN_ACTIVATION_REVIEW"
            if self.ready_for_human_activation_review
            else "NOT_READY"
        )
        if self.status != expected_status:
            raise ValueError("activation readiness status is inconsistent")

    @property
    def can_enable_learning(self) -> bool:
        return False

    @property
    def can_enable_residual(self) -> bool:
        return False

    @property
    def can_enable_dcs(self) -> bool:
        return False

    def to_runtime_config(self) -> Dict[str, Any]:
        raise ValueError(
            "activation-readiness review is non-authorizing; no runtime config may be generated"
        )

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["blockers"] = list(self.blockers)
        return value


def evaluate_dual_response_activation_readiness(
    profile: DualResponseCalibrationProfile,
    prerequisites: DualResponseActivationPrerequisites,
) -> DualResponseActivationReadiness:
    """Evaluate readiness without granting any activation permission."""

    blockers = []

    if profile.condition_snapshot_version != prerequisites.expected_condition_snapshot_version:
        blockers.append("CONDITION_SNAPSHOT_MISMATCH")
    if profile.mfac_context_id != prerequisites.expected_mfac_context_id:
        blockers.append("MFAC_CONTEXT_MISMATCH")
    if not profile.so2_calibrated:
        blockers.append("SO2_CHANNEL_NOT_CALIBRATED")
    if not profile.ph_calibrated:
        blockers.append("PH_CHANNEL_NOT_CALIBRATED")
    if not prerequisites.plant_contract_match_reviewed:
        blockers.append("PLANT_CONTRACT_MATCH_NOT_REVIEWED")
    if not prerequisites.runtime_parameter_reviewed:
        blockers.append("RUNTIME_PARAMETER_REVIEW_NOT_COMPLETE")
    if not prerequisites.shadow_validation_reviewed:
        blockers.append("SHADOW_VALIDATION_NOT_REVIEWED")
    if not prerequisites.causal_target_application_reviewed:
        blockers.append("CAUSAL_TARGET_APPLICATION_NOT_REVIEWED")
    if not prerequisites.persistence_restore_reviewed:
        blockers.append("PERSISTENCE_RESTORE_NOT_REVIEWED")
    if not prerequisites.rollback_plan_reviewed:
        blockers.append("ROLLBACK_PLAN_NOT_REVIEWED")

    snapshot_context_match = not any(
        item in blockers
        for item in ("CONDITION_SNAPSHOT_MISMATCH", "MFAC_CONTEXT_MISMATCH")
    )
    both_calibrated = profile.both_channels_calibrated
    profile_load_ready = (
        both_calibrated
        and snapshot_context_match
        and prerequisites.plant_contract_match_reviewed
        and prerequisites.runtime_parameter_reviewed
        and prerequisites.persistence_restore_reviewed
    )
    online_learning_ready = (
        profile_load_ready
        and prerequisites.shadow_validation_reviewed
        and prerequisites.causal_target_application_reviewed
        and prerequisites.rollback_plan_reviewed
    )
    residual_ready = (
        profile_load_ready
        and prerequisites.shadow_validation_reviewed
        and prerequisites.causal_target_application_reviewed
        and prerequisites.rollback_plan_reviewed
        and profile.ph_calibrated
    )

    blockers = tuple(dict.fromkeys(blockers))
    ready = online_learning_ready and residual_ready and not blockers
    return DualResponseActivationReadiness(
        status=(
            "READY_FOR_HUMAN_ACTIVATION_REVIEW"
            if ready
            else "NOT_READY"
        ),
        blockers=blockers,
        profile_id=profile.profile_id,
        condition_snapshot_version=profile.condition_snapshot_version,
        mfac_context_id=profile.mfac_context_id,
        so2_calibrated=profile.so2_calibrated,
        ph_calibrated=profile.ph_calibrated,
        profile_load_evidence_ready=profile_load_ready,
        online_learning_evidence_ready=online_learning_ready,
        residual_control_evidence_ready=residual_ready,
        ready_for_human_activation_review=ready,
        learning_enabled=False,
        residual_control_enabled=False,
        dcs_write_enabled=False,
        metadata={
            "calibration_profile_activation_status": profile.activation_status,
            "review_is_non_authorizing": True,
            "separate_future_activation_approval_required": True,
            "production_permissions_unchanged": True,
        },
    )


__all__ = [
    "DUAL_RESPONSE_ACTIVATION_REVIEW_VERSION",
    "DualResponseActivationPrerequisites",
    "DualResponseActivationReadiness",
    "evaluate_dual_response_activation_readiness",
]
