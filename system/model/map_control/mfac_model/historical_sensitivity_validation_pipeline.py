# -*- coding: utf-8 -*-
"""Canonical blocked-validation pipeline for historical MFAC sensitivities.

HistoricalEpisodeEngine output is first adapted through the same model-based
LOCAL_GAIN evidence gate used by training.  Each snapshot/context/grid is then
validated across calendar-date blocks using an explicit model-complexity ladder.
The simplest model that passes is selected as a REVIEW_CANDIDATE only.

This module does not publish HistoricalSensitivityMap authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

import pandas as pd

from .historical_model_based_gain_adapter import (
    HISTORICAL_MODEL_BASED_GAIN_ADAPTER_VERSION,
    HistoricalModelBasedGainAdaptationSummary,
    HistoricalModelBasedGainAdapterConfig,
    adapt_historical_episodes_for_model_based_gain,
)
from .historical_sensitivity_validation import (
    HISTORICAL_SENSITIVITY_BLOCKED_VALIDATION_VERSION,
    HISTORICAL_SENSITIVITY_MODEL_SELECTION_VERSION,
    HistoricalSensitivityBlockedValidationConfig,
    HistoricalSensitivityModelSelectionResult,
    HistoricalSensitivityModelSpec,
    select_blocked_validated_model,
)


HISTORICAL_SENSITIVITY_VALIDATION_PIPELINE_VERSION = (
    "SCHEME2_HISTORICAL_SENSITIVITY_VALIDATION_PIPELINE_V1_CANONICAL_BLOCKED"
)


@dataclass(frozen=True)
class HistoricalSensitivityValidationReport:
    adaptation_summary: HistoricalModelBasedGainAdaptationSummary
    grid_selections: Tuple[HistoricalSensitivityModelSelectionResult, ...]
    pooled_selections: Tuple[HistoricalSensitivityModelSelectionResult, ...]
    snapshot_versions: Tuple[str, ...]
    accepted_training_event_count: int
    selected_grid_model_count: int
    selected_pooled_model_count: int
    no_validated_grid_model_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = HISTORICAL_SENSITIVITY_VALIDATION_PIPELINE_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "semantics_version": self.semantics_version,
            "adaptation_summary": self.adaptation_summary.to_dict(),
            "grid_selections": [item.to_dict() for item in self.grid_selections],
            "pooled_selections": [item.to_dict() for item in self.pooled_selections],
            "snapshot_versions": list(self.snapshot_versions),
            "accepted_training_event_count": self.accepted_training_event_count,
            "selected_grid_model_count": self.selected_grid_model_count,
            "selected_pooled_model_count": self.selected_pooled_model_count,
            "no_validated_grid_model_count": self.no_validated_grid_model_count,
            "metadata": dict(self.metadata),
            "activation_status": "NOT_ACTIVATABLE",
            "learning_permission": False,
            "residual_control_permission": False,
            "dcs_write_permission": False,
        }


def build_historical_sensitivity_validation_report(
    episodes: pd.DataFrame,
    *,
    adapter_config: HistoricalModelBasedGainAdapterConfig,
    model_specs: Sequence[HistoricalSensitivityModelSpec],
    validation_config: HistoricalSensitivityBlockedValidationConfig,
    include_pooled_fallback: bool = True,
) -> HistoricalSensitivityValidationReport:
    """Run canonical date-blocked model selection without publishing runtime state."""
    frame, adaptation = adapt_historical_episodes_for_model_based_gain(
        episodes,
        adapter_config,
    )
    if frame.empty:
        return HistoricalSensitivityValidationReport(
            adaptation_summary=adaptation,
            grid_selections=(),
            pooled_selections=(),
            snapshot_versions=(),
            accepted_training_event_count=0,
            selected_grid_model_count=0,
            selected_pooled_model_count=0,
            no_validated_grid_model_count=0,
            metadata={
                "adapter_semantics_version": HISTORICAL_MODEL_BASED_GAIN_ADAPTER_VERSION,
                "blocked_validation_semantics_version": HISTORICAL_SENSITIVITY_BLOCKED_VALIDATION_VERSION,
                "model_selection_semantics_version": HISTORICAL_SENSITIVITY_MODEL_SELECTION_VERSION,
                "publish_runtime_map": False,
            },
        )

    grid_selections: List[HistoricalSensitivityModelSelectionResult] = []
    group_columns = [
        "condition_snapshot_version",
        "mfac_context_id",
        "grid_id",
    ]
    for key, group in frame.groupby(group_columns, sort=True, dropna=False):
        snapshot, context_id, grid_id = (str(item) for item in key)
        grid_selections.append(
            select_blocked_validated_model(
                group,
                model_specs,
                validation_config,
                condition_snapshot_version=snapshot,
                mfac_context_id=context_id,
                grid_id=grid_id,
            )
        )

    pooled_selections: List[HistoricalSensitivityModelSelectionResult] = []
    if include_pooled_fallback:
        for snapshot, group in frame.groupby("condition_snapshot_version", sort=True):
            pooled_selections.append(
                select_blocked_validated_model(
                    group,
                    model_specs,
                    validation_config,
                    condition_snapshot_version=str(snapshot),
                    mfac_context_id="",
                    grid_id="",
                )
            )

    selected_grid = sum(
        item.status == "BLOCKED_VALIDATED_MODEL_REVIEW_CANDIDATE"
        for item in grid_selections
    )
    selected_pooled = sum(
        item.status == "BLOCKED_VALIDATED_MODEL_REVIEW_CANDIDATE"
        for item in pooled_selections
    )
    no_grid = sum(
        item.status == "NO_BLOCKED_VALIDATED_MODEL"
        for item in grid_selections
    )
    snapshots = tuple(
        sorted({str(item) for item in frame["condition_snapshot_version"]})
    )
    return HistoricalSensitivityValidationReport(
        adaptation_summary=adaptation,
        grid_selections=tuple(grid_selections),
        pooled_selections=tuple(pooled_selections),
        snapshot_versions=snapshots,
        accepted_training_event_count=int(len(frame)),
        selected_grid_model_count=int(selected_grid),
        selected_pooled_model_count=int(selected_pooled),
        no_validated_grid_model_count=int(no_grid),
        metadata={
            "adapter_semantics_version": HISTORICAL_MODEL_BASED_GAIN_ADAPTER_VERSION,
            "blocked_validation_semantics_version": HISTORICAL_SENSITIVITY_BLOCKED_VALIDATION_VERSION,
            "model_selection_semantics_version": HISTORICAL_SENSITIVITY_MODEL_SELECTION_VERSION,
            "historical_route": "MODEL_BASED_LOCAL_GAIN_DATE_BLOCKED",
            "selection_policy": "SIMPLEST_PASSING",
            "publish_runtime_map": False,
            "human_review_required_before_surface_publish": True,
            "qbase_availability_independent": True,
        },
    )


__all__ = [
    "HISTORICAL_SENSITIVITY_VALIDATION_PIPELINE_VERSION",
    "HistoricalSensitivityValidationReport",
    "build_historical_sensitivity_validation_report",
]
