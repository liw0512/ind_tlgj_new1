# -*- coding: utf-8 -*-
"""Canonical offline-training settings for Scheme-2 MFAC.

These settings own *offline evidence extraction and review-candidate training*.
They are not production-control parameters and cannot enable online learning,
Residual control, or DCS actuation.

The HistoricalEpisodeEngine timing values are migrated from the last reviewed
second-module extraction semantics so Process4MapControl can reuse one canonical
history segmentation path.  Historical marginal gains deliberately start with
a scalar model; more complex work-point surfaces require separate date-blocked
proof before they may be considered.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Tuple

from system.model.config.mfac_training_lifecycle import (
    INCREMENTAL_OFFLINE_TRAINING_DAYS,
    INITIAL_OFFLINE_TRAINING_DAYS,
    OFFLINE_TRAINING_ORDER,
    ONLINE_MFAC_UPDATE_TRIGGER,
)

from .historical_model_based_gain_adapter import (
    HistoricalModelBasedGainAdapterConfig,
)
from .historical_sensitivity_validation import (
    HistoricalSensitivityBlockedValidationConfig,
    HistoricalSensitivityModelSpec,
)
from .model_based_local_gain_trainer import ModelBasedLocalGainTrainerConfig


MFAC_OFFLINE_TRAINING_CONFIG_VERSION = (
    "SCHEME2_MFAC_OFFLINE_TRAINING_V3_7DAY_INITIAL_3DAY_INCREMENTAL_SCALAR_PRIOR_HANDOFF"
)


# HistoricalEpisodeEngine input/segmentation contract.  The first module has
# already labelled every row with condition_snapshot_version/grid/condition.
_HISTORICAL_EPISODE_CONFIG: Dict[str, Any] = {
    "io": {
        "csv_encoding": "utf-8-sig",
        "timestamp_format": None,
        "drop_duplicate_timestamp_keep": "last",
        "strict_required_columns": True,
    },
    "performance": {
        # Keep all Process4/FAST context columns.  The current model evidence
        # route needs more than the minimal physical-column whitelist.
        "read_only_required_columns": False,
        "skip_sort_when_already_ordered": True,
    },
    "preprocessing": {
        "supply_flow_rolling_median_points": 3,
        "max_continuous_gap_seconds": 180.0,
        "coerce_numeric": True,
    },
    "episode": {
        "baseline_minutes": 5.0,
        "action_detection_window_minutes": 2.0,
        "max_action_duration_minutes": 20.0,
        "action_end_stable_minutes": 1.5,
        "action_merge_gap_minutes": 1.0,
        "response_delay_minutes": 3.0,
        "response_window_minutes": 10.0,
        "invalidate_followup_action_in_response": True,
        "hold_action_guard_minutes": 3.0,
        "hold_episode_minutes": 15.0,
        "hold_stride_minutes": 15.0,
        "max_hold_episodes_per_segment": 48,
        "minimum_window_coverage_ratio": 0.70,
        "incremental_context_tail_minutes": 60.0,
        "short_reverse_action_minutes": 20.0,
    },
    "response": {
        "so2_direction_deadband": 0.50,
        "ph_direction_deadband": 0.02,
        "stable_so2_range_max": None,
        "oscillation_diff_deadband": 0.30,
    },
    # Direct LOCAL_GAIN stays fail-closed.  Large/ordinary historical actions
    # remain DYNAMIC evidence and reach the separate model-based marginal route.
    "mfac_historical_evidence": {},
}


def historical_episode_training_config() -> Dict[str, Any]:
    return deepcopy(_HISTORICAL_EPISODE_CONFIG)


def scalar_gain_trainer_config() -> ModelBasedLocalGainTrainerConfig:
    """Conservative scalar historical prior candidate, not runtime authority."""
    return ModelBasedLocalGainTrainerConfig(
        event_time_column="event_time",
        delta_q_column="delta_q",
        so2_response_column="so2_response",
        ph_response_column="ph_response",
        surface_feature_columns=(),
        nuisance_columns=(
            "duration_s",
            "inlet_pretrend",
            "so2_pretrend",
            "extra_volume_m3",
        ),
        minimum_event_count=20,
        minimum_independent_days=3,
        bootstrap_iterations=30,
        minimum_physical_sign_probability=0.80,
        minimum_relative_delta_q_scale=0.03,
        huber_epsilon=1.35,
        huber_alpha=0.2,
        random_seed=13,
        confidence_reference_event_count=30,
        confidence_reference_day_count=5,
    )


def blocked_validation_config() -> HistoricalSensitivityBlockedValidationConfig:
    """Cross-date evidence gate used before a prior may enter human review."""
    return HistoricalSensitivityBlockedValidationConfig(
        fold_count=5,
        minimum_train_event_count=20,
        minimum_holdout_event_count=5,
        minimum_evaluated_folds=5,
        minimum_so2_holdout_direction_rate=0.80,
        minimum_ph_holdout_direction_rate=0.80,
        minimum_so2_center_fold_rate=0.80,
        minimum_ph_center_fold_rate=0.80,
        minimum_median_so2_zero_effect_skill=0.0,
        minimum_median_ph_zero_effect_skill=0.0,
        maximum_mean_extrapolation_rate=0.70,
    )


def scalar_model_specs() -> Tuple[HistoricalSensitivityModelSpec, ...]:
    return (
        HistoricalSensitivityModelSpec(
            "GRID_SCALAR",
            0,
            scalar_gain_trainer_config(),
        ),
    )


def historical_adapter_config(tower_id: str) -> HistoricalModelBasedGainAdapterConfig:
    """Allow offline snapshot remap but never within-event condition drift."""
    return HistoricalModelBasedGainAdapterConfig(
        tower_id=str(tower_id),
        reject_condition_remap=False,
        reject_within_event_condition_change=True,
    )


# The only numeric offline cadence source is mfac_training_lifecycle.py.
# Process4 consumes the same constants; this contract is persisted so a version
# cannot claim a different 7d/3d lifecycle during activation.
OFFLINE_ONLINE_LIFECYCLE_CONTRACT: Dict[str, Any] = {
    "initial_training_days": int(INITIAL_OFFLINE_TRAINING_DAYS),
    "incremental_training_days": int(INCREMENTAL_OFFLINE_TRAINING_DAYS),
    "incremental_trigger_rule": (
        "AFTER_AT_LEAST_INCREMENTAL_TRAINING_DAYS_OF_NEW_DATA"
    ),
    "offline_order": list(OFFLINE_TRAINING_ORDER),
    "offline_prior_role": "COLD_START_OR_NEW_CONTEXT_BASELINE",
    "online_update_trigger": ONLINE_MFAC_UPDATE_TRIGGER,
    "online_update_is_periodic": False,
    "persisted_online_state_precedes_offline_prior_within_same_context": True,
    "runtime_state_namespace": ["condition_snapshot_version", "mfac_context_id"],
    # Version handoff is explicit rather than generic state reuse.
    "cross_snapshot_online_state_reuse": True,
    "cross_snapshot_online_state_reuse_policy": "SAME_MFAC_CONTEXT_AND_GRID_ONLY",
    "cross_snapshot_online_state_requires_runtime_grid_id": True,
    "cross_snapshot_residual_reuse": False,
    "cross_snapshot_pending_or_hold_reuse": False,
    "historical_prior_may_overwrite_online_evidence": False,
    "historical_episode_evidence_is_cumulative_across_snapshots": True,
    "snapshot_remap_key": "grid_id",
    "offline_training_publishes_runtime_authority": False,
}


__all__ = [
    "MFAC_OFFLINE_TRAINING_CONFIG_VERSION",
    "OFFLINE_ONLINE_LIFECYCLE_CONTRACT",
    "historical_episode_training_config",
    "historical_adapter_config",
    "scalar_gain_trainer_config",
    "blocked_validation_config",
    "scalar_model_specs",
]
