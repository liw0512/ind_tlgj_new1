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
    "ModelBasedLocalGainCandidate",
    "ModelBasedLocalGainTrainerConfig",
    "fit_model_based_local_gain",
]
