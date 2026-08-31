"""Adaptive predictive slurry-control foundation.

This package is intentionally side-by-side with the legacy prototype advisor.
Nothing in this package writes DCS or replaces the active TARGET_SUPPLY_FLOW
path until the shadow/replay acceptance gates are completed.
"""

from .config import PredictiveFoundationSpec, build_foundation_spec
from .identifiability import (
    IdentifiabilityAssessment,
    IdentifiabilityLevel,
    assess_episode_identifiability,
)
from .model_types import ResponseChannelSpec, ResponseModelArtifact

__all__ = [
    "PredictiveFoundationSpec",
    "build_foundation_spec",
    "IdentifiabilityAssessment",
    "IdentifiabilityLevel",
    "assess_episode_identifiability",
    "ResponseChannelSpec",
    "ResponseModelArtifact",
]
