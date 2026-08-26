# -*- coding: utf-8 -*-
"""Scheme 2 condition-aware MFAC package.

V1 starts as a sidecar to ``condition_model``.  Online runtime pieces are
introduced incrementally and remain isolated from DCS write paths until their
shadow contracts have been validated.
"""

from .bootstrap_trainer import (
    MFACBootstrapEvidence,
    MFACReplayConfig,
    build_bootstrap_evidence,
    finalize_bootstrap_profile,
)
from .continuous_target import (
    CONTINUOUS_TARGET_SEMANTICS_VERSION,
    COUNTERFACTUAL_SHADOW,
    ONLINE_SHADOW,
    ContinuousTargetConfig,
    ContinuousTargetDecision,
    ContinuousTargetPublisher,
)
from .context_resolver import MFACContextResolver
from .episode_adapter import Scheme1EpisodeToMFACAdapter, adapt_episode_frame
from .mfac_eligibility import (
    MFACEligibilityConfig,
    MFACEligibilityDecision,
    StrictMFACEligibilityGate,
)
from .mfac_schema import (
    MFAC_SEMANTICS_VERSION,
    ActionResponseEvent,
    DelayProfile,
    MFACBootstrapProfile,
    MFACContextResolution,
    MFACRuntimeState,
)
from .supply_flow_tracking import (
    SUPPLY_FLOW_TRACKING_SEMANTICS_VERSION,
    SupplyFlowTrackingConfig,
    SupplyFlowTrackingEvent,
    SupplyFlowTrackingMonitor,
    SupplyFlowTrackingUpdate,
)

__all__ = [
    "MFAC_SEMANTICS_VERSION",
    "CONTINUOUS_TARGET_SEMANTICS_VERSION",
    "SUPPLY_FLOW_TRACKING_SEMANTICS_VERSION",
    "COUNTERFACTUAL_SHADOW",
    "ONLINE_SHADOW",
    "ActionResponseEvent",
    "DelayProfile",
    "MFACBootstrapProfile",
    "MFACContextResolution",
    "MFACRuntimeState",
    "MFACContextResolver",
    "MFACEligibilityConfig",
    "MFACEligibilityDecision",
    "StrictMFACEligibilityGate",
    "Scheme1EpisodeToMFACAdapter",
    "adapt_episode_frame",
    "MFACBootstrapEvidence",
    "MFACReplayConfig",
    "build_bootstrap_evidence",
    "finalize_bootstrap_profile",
    "ContinuousTargetConfig",
    "ContinuousTargetDecision",
    "ContinuousTargetPublisher",
    "SupplyFlowTrackingConfig",
    "SupplyFlowTrackingEvent",
    "SupplyFlowTrackingMonitor",
    "SupplyFlowTrackingUpdate",
]
