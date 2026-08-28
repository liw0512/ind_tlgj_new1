# -*- coding: utf-8 -*-
"""Date-blocked validation for historical MFAC marginal-gain candidates.

Full-sample bootstrap stability is necessary but not sufficient.  Historical
operator interventions can be non-stationary across days, so a sensitivity
candidate must also survive date-blocked holdout validation before it may be
promoted for review.

The validator evaluates the *local-gain object itself*, not a full process
forecast.  For each holdout event it reconstructs the candidate marginal phi at
the holdout work point and checks:

* SO2 marginal remains negative;
* pH marginal remains positive;
* the linearized effect ``phi(work_point) * delta_q`` improves on a zero-effect
  baseline in holdout data;
* work-point extrapolation remains auditable.

No validation result grants runtime authority.  Outputs remain REVIEW_ONLY and
cannot enable LEARN, Residual, or DCS write.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .model_based_local_gain_trainer import (
    MODEL_BASED_LOCAL_GAIN_TRAINER_VERSION,
    ModelBasedLocalGainCandidate,
    ModelBasedLocalGainTrainerConfig,
    fit_model_based_local_gain,
)


HISTORICAL_SENSITIVITY_BLOCKED_VALIDATION_VERSION = (
    "SCHEME2_HISTORICAL_SENSITIVITY_BLOCKED_VALIDATION_V1_DATE_BLOCKED"
)
HISTORICAL_SENSITIVITY_MODEL_SELECTION_VERSION = (
    "SCHEME2_HISTORICAL_SENSITIVITY_MODEL_SELECTION_V1_SIMPLEST_PASSING"
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class HistoricalSensitivityBlockedValidationConfig:
    fold_count: int
    minimum_train_event_count: int
    minimum_holdout_event_count: int
    minimum_evaluated_folds: int
    minimum_so2_holdout_direction_rate: float
    minimum_ph_holdout_direction_rate: float
    minimum_so2_center_fold_rate: float
    minimum_ph_center_fold_rate: float
    minimum_median_so2_zero_effect_skill: float
    minimum_median_ph_zero_effect_skill: float
    maximum_mean_extrapolation_rate: Optional[float] = None

    def __post_init__(self) -> None:
        if int(self.fold_count) < 3:
            raise ValueError("fold_count must be >= 3")
        if int(self.minimum_train_event_count) < 3:
            raise ValueError("minimum_train_event_count must be >= 3")
        if int(self.minimum_holdout_event_count) < 1:
            raise ValueError("minimum_holdout_event_count must be >= 1")
        if not 1 <= int(self.minimum_evaluated_folds) <= int(self.fold_count):
            raise ValueError("minimum_evaluated_folds must be within [1, fold_count]")
        for name in (
            "minimum_so2_holdout_direction_rate",
            "minimum_ph_holdout_direction_rate",
            "minimum_so2_center_fold_rate",
            "minimum_ph_center_fold_rate",
        ):
            value = _finite(getattr(self, name))
            if value is None or not 0.0 <= value <= 1.0:
                raise ValueError("%s must be finite within [0, 1]" % name)
        for name in (
            "minimum_median_so2_zero_effect_skill",
            "minimum_median_ph_zero_effect_skill",
        ):
            value = _finite(getattr(self, name))
            if value is None:
                raise ValueError("%s must be finite" % name)
        if self.maximum_mean_extrapolation_rate is not None:
            value = _finite(self.maximum_mean_extrapolation_rate)
            if value is None or not 0.0 <= value <= 1.0:
                raise ValueError(
                    "maximum_mean_extrapolation_rate must be within [0, 1]"
                )


@dataclass(frozen=True)
class HistoricalSensitivityValidationFold:
    fold_index: int
    train_start_date: str
    train_end_date: str
    holdout_start_date: str
    holdout_end_date: str
    train_event_count: int
    holdout_event_count: int
    train_candidate_status: str
    phi_so2_center: Optional[float]
    phi_ph_center: Optional[float]
    so2_holdout_direction_rate: Optional[float]
    ph_holdout_direction_rate: Optional[float]
    so2_zero_effect_skill: Optional[float]
    ph_zero_effect_skill: Optional[float]
    extrapolation_rate: Optional[float]
    status: str
    reason_codes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["reason_codes"] = list(self.reason_codes)
        return value


@dataclass(frozen=True)
class HistoricalSensitivityBlockedValidationResult:
    condition_snapshot_version: str
    mfac_context_id: str
    grid_id: str
    model_label: str
    folds: Tuple[HistoricalSensitivityValidationFold, ...]
    evaluated_fold_count: int
    evaluated_holdout_event_count: int
    so2_holdout_direction_rate: float
    ph_holdout_direction_rate: float
    so2_center_fold_rate: float
    ph_center_fold_rate: float
    median_so2_zero_effect_skill: float
    median_ph_zero_effect_skill: float
    mean_extrapolation_rate: float
    status: str
    reason_codes: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = HISTORICAL_SENSITIVITY_BLOCKED_VALIDATION_VERSION

    @property
    def publishable_for_review(self) -> bool:
        return self.status == "BLOCKED_VALIDATION_REVIEW_CANDIDATE"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "semantics_version": self.semantics_version,
            "condition_snapshot_version": self.condition_snapshot_version,
            "mfac_context_id": self.mfac_context_id,
            "grid_id": self.grid_id,
            "model_label": self.model_label,
            "folds": [item.to_dict() for item in self.folds],
            "evaluated_fold_count": self.evaluated_fold_count,
            "evaluated_holdout_event_count": self.evaluated_holdout_event_count,
            "so2_holdout_direction_rate": self.so2_holdout_direction_rate,
            "ph_holdout_direction_rate": self.ph_holdout_direction_rate,
            "so2_center_fold_rate": self.so2_center_fold_rate,
            "ph_center_fold_rate": self.ph_center_fold_rate,
            "median_so2_zero_effect_skill": self.median_so2_zero_effect_skill,
            "median_ph_zero_effect_skill": self.median_ph_zero_effect_skill,
            "mean_extrapolation_rate": self.mean_extrapolation_rate,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "publishable_for_review": self.publishable_for_review,
            "metadata": dict(self.metadata),
            "activation_status": "NOT_ACTIVATABLE",
            "learning_permission": False,
            "residual_control_permission": False,
            "dcs_write_permission": False,
        }


@dataclass(frozen=True)
class HistoricalSensitivityModelSpec:
    label: str
    complexity_rank: int
    trainer_config: ModelBasedLocalGainTrainerConfig

    def __post_init__(self) -> None:
        if not str(self.label or "").strip():
            raise ValueError("model spec label is required")
        if int(self.complexity_rank) < 0:
            raise ValueError("complexity_rank must be >= 0")


@dataclass(frozen=True)
class HistoricalSensitivityModelSelectionResult:
    condition_snapshot_version: str
    mfac_context_id: str
    grid_id: str
    selected_model_label: str
    selected_complexity_rank: Optional[int]
    status: str
    validations: Tuple[HistoricalSensitivityBlockedValidationResult, ...]
    reason_codes: Tuple[str, ...] = ()
    semantics_version: str = HISTORICAL_SENSITIVITY_MODEL_SELECTION_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "semantics_version": self.semantics_version,
            "condition_snapshot_version": self.condition_snapshot_version,
            "mfac_context_id": self.mfac_context_id,
            "grid_id": self.grid_id,
            "selected_model_label": self.selected_model_label,
            "selected_complexity_rank": self.selected_complexity_rank,
            "status": self.status,
            "validations": [item.to_dict() for item in self.validations],
            "reason_codes": list(self.reason_codes),
            "selection_policy": "SIMPLEST_PASSING",
            "activation_status": "NOT_ACTIVATABLE",
            "learning_permission": False,
            "residual_control_permission": False,
            "dcs_write_permission": False,
        }


def _date_blocks(frame: pd.DataFrame, event_time_column: str, fold_count: int) -> list[list[Any]]:
    timestamps = pd.to_datetime(frame[event_time_column], errors="coerce")
    dates = sorted({item for item in timestamps.dt.date if pd.notna(item)})
    if not dates:
        return []
    return [list(block) for block in np.array_split(np.asarray(dates, dtype=object), fold_count) if len(block)]


def _candidate_phi(
    candidate: ModelBasedLocalGainCandidate,
    frame: pd.DataFrame,
    trainer_config: ModelBasedLocalGainTrainerConfig,
    *,
    channel: str,
) -> np.ndarray:
    center = candidate.phi_so2_center if channel == "so2" else candidate.phi_ph_center
    if center is None:
        return np.full(len(frame), np.nan, dtype=float)
    coefficients = (
        candidate.phi_so2_surface_coefficients
        if channel == "so2"
        else candidate.phi_ph_surface_coefficients
    )
    values = np.full(len(frame), float(center), dtype=float)
    for feature_name, column_name in trainer_config.surface_feature_columns:
        name = str(feature_name)
        if name not in candidate.feature_center or name not in candidate.feature_scale:
            continue
        scale = float(candidate.feature_scale[name])
        z = (
            pd.to_numeric(frame[column_name], errors="coerce").to_numpy(dtype=float)
            - float(candidate.feature_center[name])
        ) / scale
        values += float(coefficients.get(name, 0.0)) * z
    return values


def _extrapolation_rate(
    candidate: ModelBasedLocalGainCandidate,
    frame: pd.DataFrame,
    trainer_config: ModelBasedLocalGainTrainerConfig,
) -> float:
    if len(frame) == 0 or not trainer_config.surface_feature_columns:
        return 0.0
    outside = np.zeros(len(frame), dtype=bool)
    for feature_name, column_name in trainer_config.surface_feature_columns:
        name = str(feature_name)
        if name not in candidate.support_min or name not in candidate.support_max:
            continue
        values = pd.to_numeric(frame[column_name], errors="coerce").to_numpy(dtype=float)
        outside |= (
            (values < float(candidate.support_min[name]))
            | (values > float(candidate.support_max[name]))
        )
    return float(np.mean(outside))


def _zero_effect_skill(observed: np.ndarray, predicted: np.ndarray) -> Optional[float]:
    valid = np.isfinite(observed) & np.isfinite(predicted)
    if not np.any(valid):
        return None
    observed = observed[valid]
    predicted = predicted[valid]
    baseline_mae = float(np.mean(np.abs(observed)))
    if baseline_mae <= 1e-12:
        return None
    model_mae = float(np.mean(np.abs(observed - predicted)))
    return 1.0 - model_mae / baseline_mae


def validate_model_based_local_gain_blocked(
    frame: pd.DataFrame,
    trainer_config: ModelBasedLocalGainTrainerConfig,
    validation_config: HistoricalSensitivityBlockedValidationConfig,
    *,
    condition_snapshot_version: str,
    mfac_context_id: str,
    grid_id: str,
    model_label: str,
) -> HistoricalSensitivityBlockedValidationResult:
    """Validate one historical sensitivity specification with date-blocked CV."""
    required = {
        trainer_config.event_time_column,
        trainer_config.delta_q_column,
        trainer_config.so2_response_column,
        trainer_config.ph_response_column,
    }
    required.update(column for _, column in trainer_config.surface_feature_columns)
    required.update(trainer_config.nuisance_columns)
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("missing blocked-validation columns: %s" % ", ".join(missing))

    work = frame.copy()
    work[trainer_config.event_time_column] = pd.to_datetime(
        work[trainer_config.event_time_column], errors="coerce"
    )
    work = work.dropna(subset=[trainer_config.event_time_column]).sort_values(
        trainer_config.event_time_column, kind="stable"
    )
    blocks = _date_blocks(work, trainer_config.event_time_column, int(validation_config.fold_count))
    folds = []
    weighted_so2_direction = 0.0
    weighted_ph_direction = 0.0
    weighted_holdout_count = 0
    center_so2_flags = []
    center_ph_flags = []
    so2_skills = []
    ph_skills = []
    extrapolation_rates = []

    for index, holdout_dates in enumerate(blocks, 1):
        date_series = work[trainer_config.event_time_column].dt.date
        holdout_mask = date_series.isin(set(holdout_dates))
        train = work.loc[~holdout_mask].copy()
        holdout = work.loc[holdout_mask].copy()
        reasons = []
        if len(train) < int(validation_config.minimum_train_event_count):
            reasons.append("INSUFFICIENT_TRAIN_EVENTS")
        if len(holdout) < int(validation_config.minimum_holdout_event_count):
            reasons.append("INSUFFICIENT_HOLDOUT_EVENTS")
        if reasons:
            folds.append(
                HistoricalSensitivityValidationFold(
                    fold_index=index,
                    train_start_date=(
                        train[trainer_config.event_time_column].min().date().isoformat()
                        if not train.empty else ""
                    ),
                    train_end_date=(
                        train[trainer_config.event_time_column].max().date().isoformat()
                        if not train.empty else ""
                    ),
                    holdout_start_date=min(holdout_dates).isoformat(),
                    holdout_end_date=max(holdout_dates).isoformat(),
                    train_event_count=int(len(train)),
                    holdout_event_count=int(len(holdout)),
                    train_candidate_status="NOT_FIT",
                    phi_so2_center=None,
                    phi_ph_center=None,
                    so2_holdout_direction_rate=None,
                    ph_holdout_direction_rate=None,
                    so2_zero_effect_skill=None,
                    ph_zero_effect_skill=None,
                    extrapolation_rate=None,
                    status="NOT_EVALUATED",
                    reason_codes=tuple(reasons),
                )
            )
            continue

        candidate = fit_model_based_local_gain(
            train,
            trainer_config,
            condition_snapshot_version=condition_snapshot_version,
            mfac_context_id=mfac_context_id,
            grid_id=grid_id,
        )
        phi_so2 = _candidate_phi(candidate, holdout, trainer_config, channel="so2")
        phi_ph = _candidate_phi(candidate, holdout, trainer_config, channel="ph")
        valid_so2 = np.isfinite(phi_so2)
        valid_ph = np.isfinite(phi_ph)
        so2_rate = float(np.mean(phi_so2[valid_so2] < 0.0)) if np.any(valid_so2) else None
        ph_rate = float(np.mean(phi_ph[valid_ph] > 0.0)) if np.any(valid_ph) else None
        delta_q = pd.to_numeric(
            holdout[trainer_config.delta_q_column], errors="coerce"
        ).to_numpy(dtype=float)
        observed_so2 = pd.to_numeric(
            holdout[trainer_config.so2_response_column], errors="coerce"
        ).to_numpy(dtype=float)
        observed_ph = pd.to_numeric(
            holdout[trainer_config.ph_response_column], errors="coerce"
        ).to_numpy(dtype=float)
        so2_skill = _zero_effect_skill(observed_so2, phi_so2 * delta_q)
        ph_skill = _zero_effect_skill(observed_ph, phi_ph * delta_q)
        extrapolation = _extrapolation_rate(candidate, holdout, trainer_config)

        holdout_count = int(len(holdout))
        if so2_rate is not None and ph_rate is not None:
            weighted_so2_direction += so2_rate * holdout_count
            weighted_ph_direction += ph_rate * holdout_count
            weighted_holdout_count += holdout_count
        if candidate.phi_so2_center is not None:
            center_so2_flags.append(float(candidate.phi_so2_center) < 0.0)
        if candidate.phi_ph_center is not None:
            center_ph_flags.append(float(candidate.phi_ph_center) > 0.0)
        if so2_skill is not None:
            so2_skills.append(float(so2_skill))
        if ph_skill is not None:
            ph_skills.append(float(ph_skill))
        extrapolation_rates.append(float(extrapolation))

        fold_reasons = list(candidate.reason_codes)
        folds.append(
            HistoricalSensitivityValidationFold(
                fold_index=index,
                train_start_date=train[trainer_config.event_time_column].min().date().isoformat(),
                train_end_date=train[trainer_config.event_time_column].max().date().isoformat(),
                holdout_start_date=min(holdout_dates).isoformat(),
                holdout_end_date=max(holdout_dates).isoformat(),
                train_event_count=int(len(train)),
                holdout_event_count=holdout_count,
                train_candidate_status=candidate.status,
                phi_so2_center=candidate.phi_so2_center,
                phi_ph_center=candidate.phi_ph_center,
                so2_holdout_direction_rate=so2_rate,
                ph_holdout_direction_rate=ph_rate,
                so2_zero_effect_skill=so2_skill,
                ph_zero_effect_skill=ph_skill,
                extrapolation_rate=extrapolation,
                status="EVALUATED",
                reason_codes=tuple(fold_reasons),
            )
        )

    evaluated = [item for item in folds if item.status == "EVALUATED"]
    evaluated_count = int(len(evaluated))
    reasons = []
    if evaluated_count < int(validation_config.minimum_evaluated_folds):
        reasons.append("INSUFFICIENT_EVALUATED_FOLDS")

    so2_direction = (
        weighted_so2_direction / weighted_holdout_count
        if weighted_holdout_count else 0.0
    )
    ph_direction = (
        weighted_ph_direction / weighted_holdout_count
        if weighted_holdout_count else 0.0
    )
    so2_center_rate = float(np.mean(center_so2_flags)) if center_so2_flags else 0.0
    ph_center_rate = float(np.mean(center_ph_flags)) if center_ph_flags else 0.0
    median_so2_skill = float(np.median(so2_skills)) if so2_skills else float("-inf")
    median_ph_skill = float(np.median(ph_skills)) if ph_skills else float("-inf")
    mean_extrapolation = (
        float(np.mean(extrapolation_rates)) if extrapolation_rates else 0.0
    )

    if so2_direction < float(validation_config.minimum_so2_holdout_direction_rate):
        reasons.append("SO2_HOLDOUT_DIRECTION_UNSTABLE")
    if ph_direction < float(validation_config.minimum_ph_holdout_direction_rate):
        reasons.append("PH_HOLDOUT_DIRECTION_UNSTABLE")
    if so2_center_rate < float(validation_config.minimum_so2_center_fold_rate):
        reasons.append("SO2_CENTER_DIRECTION_UNSTABLE_ACROSS_FOLDS")
    if ph_center_rate < float(validation_config.minimum_ph_center_fold_rate):
        reasons.append("PH_CENTER_DIRECTION_UNSTABLE_ACROSS_FOLDS")
    if median_so2_skill < float(validation_config.minimum_median_so2_zero_effect_skill):
        reasons.append("SO2_HOLDOUT_LINEARIZED_SKILL_TOO_LOW")
    if median_ph_skill < float(validation_config.minimum_median_ph_zero_effect_skill):
        reasons.append("PH_HOLDOUT_LINEARIZED_SKILL_TOO_LOW")
    if (
        validation_config.maximum_mean_extrapolation_rate is not None
        and mean_extrapolation > float(validation_config.maximum_mean_extrapolation_rate)
    ):
        reasons.append("HOLDOUT_EXTRAPOLATION_RATE_TOO_HIGH")

    if "INSUFFICIENT_EVALUATED_FOLDS" in reasons:
        status = "INSUFFICIENT_BLOCKED_VALIDATION"
    elif reasons:
        status = "BLOCKED_VALIDATION_REJECTED"
    else:
        status = "BLOCKED_VALIDATION_REVIEW_CANDIDATE"

    return HistoricalSensitivityBlockedValidationResult(
        condition_snapshot_version=str(condition_snapshot_version),
        mfac_context_id=str(mfac_context_id),
        grid_id=str(grid_id),
        model_label=str(model_label),
        folds=tuple(folds),
        evaluated_fold_count=evaluated_count,
        evaluated_holdout_event_count=int(weighted_holdout_count),
        so2_holdout_direction_rate=float(so2_direction),
        ph_holdout_direction_rate=float(ph_direction),
        so2_center_fold_rate=float(so2_center_rate),
        ph_center_fold_rate=float(ph_center_rate),
        median_so2_zero_effect_skill=float(median_so2_skill),
        median_ph_zero_effect_skill=float(median_ph_skill),
        mean_extrapolation_rate=float(mean_extrapolation),
        status=status,
        reason_codes=tuple(dict.fromkeys(reasons)),
        metadata={
            "trainer_semantics_version": MODEL_BASED_LOCAL_GAIN_TRAINER_VERSION,
            "validation_split_unit": "CALENDAR_DATE",
            "holdout_prediction_semantics": "LOCAL_GAIN_ONLY_PHI_TIMES_DELTA_Q",
            "zero_effect_baseline": True,
            "publish_runtime_map": False,
        },
    )


def select_blocked_validated_model(
    frame: pd.DataFrame,
    specs: Sequence[HistoricalSensitivityModelSpec],
    validation_config: HistoricalSensitivityBlockedValidationConfig,
    *,
    condition_snapshot_version: str,
    mfac_context_id: str,
    grid_id: str,
) -> HistoricalSensitivityModelSelectionResult:
    """Choose the simplest model that passes blocked validation."""
    ordered = sorted(specs, key=lambda item: (int(item.complexity_rank), item.label))
    if not ordered:
        raise ValueError("at least one model spec is required")
    validations = []
    selected = None
    for spec in ordered:
        result = validate_model_based_local_gain_blocked(
            frame,
            spec.trainer_config,
            validation_config,
            condition_snapshot_version=condition_snapshot_version,
            mfac_context_id=mfac_context_id,
            grid_id=grid_id,
            model_label=spec.label,
        )
        validations.append(result)
        if selected is None and result.publishable_for_review:
            selected = spec

    if selected is None:
        return HistoricalSensitivityModelSelectionResult(
            condition_snapshot_version=str(condition_snapshot_version),
            mfac_context_id=str(mfac_context_id),
            grid_id=str(grid_id),
            selected_model_label="",
            selected_complexity_rank=None,
            status="NO_BLOCKED_VALIDATED_MODEL",
            validations=tuple(validations),
            reason_codes=("NO_MODEL_SPEC_PASSED_BLOCKED_VALIDATION",),
        )
    return HistoricalSensitivityModelSelectionResult(
        condition_snapshot_version=str(condition_snapshot_version),
        mfac_context_id=str(mfac_context_id),
        grid_id=str(grid_id),
        selected_model_label=selected.label,
        selected_complexity_rank=int(selected.complexity_rank),
        status="BLOCKED_VALIDATED_MODEL_REVIEW_CANDIDATE",
        validations=tuple(validations),
        reason_codes=(),
    )


__all__ = [
    "HISTORICAL_SENSITIVITY_BLOCKED_VALIDATION_VERSION",
    "HISTORICAL_SENSITIVITY_MODEL_SELECTION_VERSION",
    "HistoricalSensitivityBlockedValidationConfig",
    "HistoricalSensitivityValidationFold",
    "HistoricalSensitivityBlockedValidationResult",
    "HistoricalSensitivityModelSpec",
    "HistoricalSensitivityModelSelectionResult",
    "validate_model_based_local_gain_blocked",
    "select_blocked_validated_model",
]
