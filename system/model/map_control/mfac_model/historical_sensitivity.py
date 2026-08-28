# -*- coding: utf-8 -*-
"""Public facade for Scheme-2 historical sensitivity learning and mapping."""

from .historical_model_based_gain_adapter import (
    HISTORICAL_MODEL_BASED_GAIN_ADAPTER_VERSION,
    HistoricalModelBasedGainAdaptationSummary,
    HistoricalModelBasedGainAdapterConfig,
    adapt_historical_episodes_for_model_based_gain,
)
from .historical_sensitivity_map import (
    HISTORICAL_SENSITIVITY_MAP_VERSION,
    HISTORICAL_SENSITIVITY_SURFACE_VERSION,
    HistoricalSensitivityDecision,
    HistoricalSensitivityMap,
    HistoricalSensitivityMapConfig,
    HistoricalSensitivityQuery,
    HistoricalSensitivitySurface,
)
from .historical_sensitivity_training_pipeline import (
    HISTORICAL_SENSITIVITY_TRAINING_PIPELINE_VERSION,
    HistoricalSensitivityTrainingReport,
    build_historical_sensitivity_training_report,
)
from .historical_sensitivity_validation import (
    HISTORICAL_SENSITIVITY_BLOCKED_VALIDATION_VERSION,
    HISTORICAL_SENSITIVITY_MODEL_SELECTION_VERSION,
    HistoricalSensitivityBlockedValidationConfig,
    HistoricalSensitivityBlockedValidationResult,
    HistoricalSensitivityModelSelectionResult,
    HistoricalSensitivityModelSpec,
    HistoricalSensitivityValidationFold,
    select_blocked_validated_model,
    validate_model_based_local_gain_blocked,
)
from .historical_sensitivity_validation_pipeline import (
    HISTORICAL_SENSITIVITY_VALIDATION_PIPELINE_VERSION,
    HistoricalSensitivityValidationReport,
    build_historical_sensitivity_validation_report,
)
from .model_based_local_gain_trainer import (
    MODEL_BASED_LOCAL_GAIN_TRAINER_VERSION,
    ModelBasedLocalGainCandidate,
    ModelBasedLocalGainTrainerConfig,
    fit_model_based_local_gain,
)

__all__ = [
    "HISTORICAL_MODEL_BASED_GAIN_ADAPTER_VERSION",
    "HISTORICAL_SENSITIVITY_MAP_VERSION",
    "HISTORICAL_SENSITIVITY_SURFACE_VERSION",
    "HISTORICAL_SENSITIVITY_TRAINING_PIPELINE_VERSION",
    "HISTORICAL_SENSITIVITY_BLOCKED_VALIDATION_VERSION",
    "HISTORICAL_SENSITIVITY_MODEL_SELECTION_VERSION",
    "HISTORICAL_SENSITIVITY_VALIDATION_PIPELINE_VERSION",
    "MODEL_BASED_LOCAL_GAIN_TRAINER_VERSION",
    "HistoricalModelBasedGainAdaptationSummary",
    "HistoricalModelBasedGainAdapterConfig",
    "adapt_historical_episodes_for_model_based_gain",
    "HistoricalSensitivityDecision",
    "HistoricalSensitivityMap",
    "HistoricalSensitivityMapConfig",
    "HistoricalSensitivityQuery",
    "HistoricalSensitivitySurface",
    "HistoricalSensitivityTrainingReport",
    "build_historical_sensitivity_training_report",
    "HistoricalSensitivityBlockedValidationConfig",
    "HistoricalSensitivityBlockedValidationResult",
    "HistoricalSensitivityValidationFold",
    "HistoricalSensitivityModelSpec",
    "HistoricalSensitivityModelSelectionResult",
    "validate_model_based_local_gain_blocked",
    "select_blocked_validated_model",
    "HistoricalSensitivityValidationReport",
    "build_historical_sensitivity_validation_report",
    "ModelBasedLocalGainCandidate",
    "ModelBasedLocalGainTrainerConfig",
    "fit_model_based_local_gain",
]
