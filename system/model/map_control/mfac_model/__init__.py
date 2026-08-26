# -*- coding: utf-8 -*-
"""Scheme 2 condition-aware MFAC package.

V1 starts as a sidecar to ``condition_model``.  Nothing in this package is
wired into the existing online control path until the shadow/runtime phases.
"""

from .bootstrap_trainer import (
    MFACBootstrapEvidence,
    MFACReplayConfig,
    build_bootstrap_evidence,
    finalize_bootstrap_profile,
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

__all__ = [
    "MFAC_SEMANTICS_VERSION",
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
]
