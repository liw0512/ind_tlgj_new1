# -*- coding: utf-8 -*-
"""Offline pipeline for context/grid model-based historical MFAC sensitivities.

This is the corrected Scheme-2 historical bootstrap route:

canonical HistoricalEpisodeEngine output
    -> DYNAMIC/non-safety model-based event frame
    -> one robust marginal-gain model per snapshot/context/grid
    -> one pooled fallback candidate per snapshot
    -> review report

The pipeline deliberately does not publish ``HistoricalSensitivityMap`` objects.
Publishing a runtime-consumable map is a separate reviewed action so rejected or
unreviewed historical fits cannot silently become online authority.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import pandas as pd

from .historical_model_based_gain_adapter import (
    HISTORICAL_MODEL_BASED_GAIN_ADAPTER_VERSION,
    HistoricalModelBasedGainAdapterConfig,
    HistoricalModelBasedGainAdaptationSummary,
    adapt_historical_episodes_for_model_based_gain,
)
from .model_based_local_gain_trainer import (
    MODEL_BASED_LOCAL_GAIN_TRAINER_VERSION,
    ModelBasedLocalGainCandidate,
    ModelBasedLocalGainTrainerConfig,
    fit_model_based_local_gain,
)


HISTORICAL_SENSITIVITY_TRAINING_PIPELINE_VERSION = (
    "SCHEME2_HISTORICAL_SENSITIVITY_TRAINING_PIPELINE_V1_REVIEW_ONLY"
)


@dataclass(frozen=True)
class HistoricalSensitivityTrainingReport:
    adaptation_summary: HistoricalModelBasedGainAdaptationSummary
    grid_candidates: Tuple[ModelBasedLocalGainCandidate, ...]
    pooled_candidates: Tuple[ModelBasedLocalGainCandidate, ...]
    snapshot_versions: Tuple[str, ...]
    accepted_training_event_count: int
    review_candidate_count: int
    rejected_model_count: int
    insufficient_model_count: int
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = HISTORICAL_SENSITIVITY_TRAINING_PIPELINE_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "semantics_version": self.semantics_version,
            "adaptation_summary": self.adaptation_summary.to_dict(),
            "grid_candidates": [item.to_dict() for item in self.grid_candidates],
            "pooled_candidates": [item.to_dict() for item in self.pooled_candidates],
            "snapshot_versions": list(self.snapshot_versions),
            "accepted_training_event_count": self.accepted_training_event_count,
            "review_candidate_count": self.review_candidate_count,
            "rejected_model_count": self.rejected_model_count,
            "insufficient_model_count": self.insufficient_model_count,
            "metadata": dict(self.metadata),
            "activation_status": "NOT_ACTIVATABLE",
            "learning_permission": False,
            "residual_control_permission": False,
            "dcs_write_permission": False,
        }


def _fit_group(
    frame: pd.DataFrame,
    trainer_config: ModelBasedLocalGainTrainerConfig,
    *,
    snapshot: str,
    context_id: str,
    grid_id: str,
) -> ModelBasedLocalGainCandidate:
    return fit_model_based_local_gain(
        frame,
        trainer_config,
        condition_snapshot_version=snapshot,
        mfac_context_id=context_id,
        grid_id=grid_id,
    )


def build_historical_sensitivity_training_report(
    episodes: pd.DataFrame,
    *,
    adapter_config: HistoricalModelBasedGainAdapterConfig,
    trainer_config: ModelBasedLocalGainTrainerConfig,
    include_pooled_fallback: bool = True,
) -> HistoricalSensitivityTrainingReport:
    """Train all available context/grid candidates without publishing authority."""
    frame, adaptation = adapt_historical_episodes_for_model_based_gain(
        episodes,
        adapter_config,
    )
    if frame.empty:
        return HistoricalSensitivityTrainingReport(
            adaptation_summary=adaptation,
            grid_candidates=(),
            pooled_candidates=(),
            snapshot_versions=(),
            accepted_training_event_count=0,
            review_candidate_count=0,
            rejected_model_count=0,
            insufficient_model_count=0,
            metadata={
                "adapter_semantics_version": HISTORICAL_MODEL_BASED_GAIN_ADAPTER_VERSION,
                "trainer_semantics_version": MODEL_BASED_LOCAL_GAIN_TRAINER_VERSION,
                "publish_runtime_map": False,
            },
        )

    grid_candidates: List[ModelBasedLocalGainCandidate] = []
    group_columns = [
        "condition_snapshot_version",
        "mfac_context_id",
        "grid_id",
    ]
    for key, group in frame.groupby(group_columns, sort=True, dropna=False):
        snapshot, context_id, grid_id = (str(item) for item in key)
        grid_candidates.append(
            _fit_group(
                group,
                trainer_config,
                snapshot=snapshot,
                context_id=context_id,
                grid_id=grid_id,
            )
        )

    pooled_candidates: List[ModelBasedLocalGainCandidate] = []
    if include_pooled_fallback:
        for snapshot, group in frame.groupby("condition_snapshot_version", sort=True):
            pooled_candidates.append(
                _fit_group(
                    group,
                    trainer_config,
                    snapshot=str(snapshot),
                    context_id="",
                    grid_id="",
                )
            )

    all_candidates = grid_candidates + pooled_candidates
    review_count = sum(item.publishable_for_review for item in all_candidates)
    rejected_count = sum(
        item.status == "MODEL_BASED_LOCAL_GAIN_REJECTED"
        for item in all_candidates
    )
    insufficient_count = sum(
        item.status == "INSUFFICIENT_EVIDENCE"
        for item in all_candidates
    )
    snapshots = tuple(sorted({str(item) for item in frame["condition_snapshot_version"]}))
    return HistoricalSensitivityTrainingReport(
        adaptation_summary=adaptation,
        grid_candidates=tuple(grid_candidates),
        pooled_candidates=tuple(pooled_candidates),
        snapshot_versions=snapshots,
        accepted_training_event_count=int(len(frame)),
        review_candidate_count=int(review_count),
        rejected_model_count=int(rejected_count),
        insufficient_model_count=int(insufficient_count),
        metadata={
            "adapter_semantics_version": HISTORICAL_MODEL_BASED_GAIN_ADAPTER_VERSION,
            "trainer_semantics_version": MODEL_BASED_LOCAL_GAIN_TRAINER_VERSION,
            "historical_route": "MODEL_BASED_LOCAL_GAIN",
            "direct_large_pulse_delta_y_over_delta_q": False,
            "operator_action_imitation": False,
            "publish_runtime_map": False,
            "human_review_required_before_surface_publish": True,
            "qbase_availability_independent": True,
        },
    )


__all__ = [
    "HISTORICAL_SENSITIVITY_TRAINING_PIPELINE_VERSION",
    "HistoricalSensitivityTrainingReport",
    "build_historical_sensitivity_training_report",
]
