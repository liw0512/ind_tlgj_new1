"""Adaptive predictive slurry-control foundation.

This package is intentionally side-by-side with the legacy prototype advisor.
Nothing in this package writes DCS or replaces the active TARGET_SUPPLY_FLOW
path until the shadow/replay acceptance gates are completed.
"""

from .config import (
    PredictiveFoundationSpec,
    build_foundation_spec,
    operating_context_columns,
)
from .identifiability import (
    IdentifiabilityAssessment,
    IdentifiabilityLevel,
    assess_episode_identifiability,
)
from .model_types import ResponseChannelSpec, ResponseModelArtifact
from .response_decomposition import (
    GLOBAL_CONDITION_MODEL_TYPE,
    DualResponseFitResult,
    GlobalConditionFitResult,
    GlobalConditionResponseConfig,
    fit_global_condition_response_channel,
    fit_tower_dual_response,
    predict_global_condition_delta,
)

__all__ = [
    "PredictiveFoundationSpec",
    "build_foundation_spec",
    "operating_context_columns",
    "IdentifiabilityAssessment",
    "IdentifiabilityLevel",
    "assess_episode_identifiability",
    "ResponseChannelSpec",
    "ResponseModelArtifact",
    "GLOBAL_CONDITION_MODEL_TYPE",
    "GlobalConditionResponseConfig",
    "GlobalConditionFitResult",
    "DualResponseFitResult",
    "fit_global_condition_response_channel",
    "fit_tower_dual_response",
    "predict_global_condition_delta",
]
