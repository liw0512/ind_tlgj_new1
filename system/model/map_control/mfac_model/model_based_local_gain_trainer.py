# -*- coding: utf-8 -*-
"""Robust model-based LOCAL_GAIN estimation from historical dynamic events.

Large historical pulses are not converted to ``delta_y / delta_q`` directly.
Instead, an event-level response model controls for baseline/work-point and
nuisance features and estimates the marginal derivative with respect to actual
flow change.  Surface interactions allow the resulting ``phi`` to vary
continuously inside one operating grid/context.

The output is always a REVIEW_CANDIDATE.  Physical direction and bootstrap sign
stability must pass before a candidate may even be considered publishable to a
reviewed ``HistoricalSensitivitySurface``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor


MODEL_BASED_LOCAL_GAIN_TRAINER_VERSION = (
    "SCHEME2_MODEL_BASED_LOCAL_GAIN_TRAINER_V1_ROBUST_INTERACTION"
)


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _robust_center_scale(series: pd.Series) -> Tuple[float, float]:
    values = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype=float)
    if values.size == 0:
        raise ValueError("cannot scale empty feature")
    center = float(np.median(values))
    q25, q75 = np.quantile(values, [0.25, 0.75])
    scale = float((q75 - q25) / 1.349)
    if not math.isfinite(scale) or scale <= 1e-12:
        scale = float(np.std(values, ddof=0))
    if not math.isfinite(scale) or scale <= 1e-12:
        raise ValueError("feature has no usable variation")
    return center, scale


@dataclass(frozen=True)
class ModelBasedLocalGainTrainerConfig:
    event_time_column: str
    delta_q_column: str
    so2_response_column: str
    ph_response_column: str
    surface_feature_columns: Tuple[Tuple[str, str], ...]
    nuisance_columns: Tuple[str, ...]
    minimum_event_count: int
    minimum_independent_days: int
    bootstrap_iterations: int
    minimum_physical_sign_probability: float
    minimum_relative_delta_q_scale: float
    huber_epsilon: float
    huber_alpha: float
    random_seed: int
    confidence_reference_event_count: int
    confidence_reference_day_count: int

    def __post_init__(self) -> None:
        if int(self.minimum_event_count) < 3:
            raise ValueError("minimum_event_count must be >= 3")
        if int(self.minimum_independent_days) < 1:
            raise ValueError("minimum_independent_days must be >= 1")
        if int(self.bootstrap_iterations) < 20:
            raise ValueError("bootstrap_iterations must be >= 20")
        probability = _finite(self.minimum_physical_sign_probability)
        if probability is None or not 0.5 < probability <= 1.0:
            raise ValueError("minimum_physical_sign_probability must be within (0.5, 1]")
        spread = _finite(self.minimum_relative_delta_q_scale)
        if spread is None or spread <= 0.0:
            raise ValueError("minimum_relative_delta_q_scale must be > 0")
        epsilon = _finite(self.huber_epsilon)
        alpha = _finite(self.huber_alpha)
        if epsilon is None or epsilon <= 1.0:
            raise ValueError("huber_epsilon must be > 1")
        if alpha is None or alpha < 0.0:
            raise ValueError("huber_alpha must be >= 0")
        if int(self.confidence_reference_event_count) < int(self.minimum_event_count):
            raise ValueError("confidence_reference_event_count must cover minimum_event_count")
        if int(self.confidence_reference_day_count) < int(self.minimum_independent_days):
            raise ValueError("confidence_reference_day_count must cover minimum_independent_days")
        names = [str(item[0]) for item in self.surface_feature_columns]
        if len(names) != len(set(names)):
            raise ValueError("surface feature names must be unique")


@dataclass(frozen=True)
class ModelBasedLocalGainCandidate:
    status: str
    condition_snapshot_version: str
    mfac_context_id: str
    grid_id: str
    event_count: int
    independent_days: int
    phi_so2_center: Optional[float]
    phi_ph_center: Optional[float]
    phi_so2_surface_coefficients: Dict[str, float]
    phi_ph_surface_coefficients: Dict[str, float]
    feature_center: Dict[str, float]
    feature_scale: Dict[str, float]
    support_min: Dict[str, float]
    support_max: Dict[str, float]
    bootstrap_so2: Dict[str, float]
    bootstrap_ph: Dict[str, float]
    so2_physical_sign_probability: float
    ph_physical_sign_probability: float
    confidence_so2_candidate: float
    confidence_ph_candidate: float
    delta_q_center: Optional[float]
    delta_q_scale: Optional[float]
    delta_q_relative_scale: Optional[float]
    reason_codes: Tuple[str, ...] = ()
    metadata: Dict[str, Any] = field(default_factory=dict)
    semantics_version: str = MODEL_BASED_LOCAL_GAIN_TRAINER_VERSION

    @property
    def publishable_for_review(self) -> bool:
        return self.status == "MODEL_BASED_LOCAL_GAIN_REVIEW_CANDIDATE"

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["reason_codes"] = list(self.reason_codes)
        value["publishable_for_review"] = self.publishable_for_review
        value["activation_status"] = "NOT_ACTIVATABLE"
        value["learning_permission"] = False
        value["residual_control_permission"] = False
        value["dcs_write_permission"] = False
        return value


def _required_columns(config: ModelBasedLocalGainTrainerConfig) -> Tuple[str, ...]:
    columns = [
        config.event_time_column,
        config.delta_q_column,
        config.so2_response_column,
        config.ph_response_column,
    ]
    columns.extend(column for _, column in config.surface_feature_columns)
    columns.extend(config.nuisance_columns)
    return tuple(dict.fromkeys(columns))


def _design_matrix(
    frame: pd.DataFrame,
    config: ModelBasedLocalGainTrainerConfig,
    *,
    learned_scaling: Optional[Mapping[str, Tuple[float, float]]] = None,
) -> Tuple[np.ndarray, Dict[str, Tuple[float, float]], Tuple[str, ...]]:
    scaling: Dict[str, Tuple[float, float]] = dict(learned_scaling or {})
    delta_name = config.delta_q_column
    if delta_name not in scaling:
        scaling[delta_name] = _robust_center_scale(frame[delta_name])
    dq_center, dq_scale = scaling[delta_name]
    dq = pd.to_numeric(frame[delta_name], errors="coerce").to_numpy(dtype=float)
    dq_z = (dq - dq_center) / dq_scale

    columns = [dq_z]
    names = ["DELTA_Q_Z"]
    surface_z: Dict[str, np.ndarray] = {}
    for feature_name, column_name in config.surface_feature_columns:
        if column_name not in scaling:
            scaling[column_name] = _robust_center_scale(frame[column_name])
        center, scale = scaling[column_name]
        values = pd.to_numeric(frame[column_name], errors="coerce").to_numpy(dtype=float)
        z = (values - center) / scale
        surface_z[str(feature_name)] = z
        columns.append(z)
        names.append("SURFACE:%s" % feature_name)

    for column_name in config.nuisance_columns:
        if column_name not in scaling:
            scaling[column_name] = _robust_center_scale(frame[column_name])
        center, scale = scaling[column_name]
        values = pd.to_numeric(frame[column_name], errors="coerce").to_numpy(dtype=float)
        columns.append((values - center) / scale)
        names.append("NUISANCE:%s" % column_name)

    for feature_name, _ in config.surface_feature_columns:
        columns.append(dq_z * surface_z[str(feature_name)])
        names.append("INTERACTION:%s" % feature_name)

    return np.column_stack(columns), scaling, tuple(names)


def _fit_channel(
    frame: pd.DataFrame,
    response_column: str,
    config: ModelBasedLocalGainTrainerConfig,
    *,
    scaling: Optional[Mapping[str, Tuple[float, float]]] = None,
) -> Tuple[HuberRegressor, Dict[str, Tuple[float, float]], Tuple[str, ...]]:
    matrix, learned, names = _design_matrix(frame, config, learned_scaling=scaling)
    response = pd.to_numeric(frame[response_column], errors="coerce").to_numpy(dtype=float)
    model = HuberRegressor(
        epsilon=float(config.huber_epsilon),
        alpha=float(config.huber_alpha),
        max_iter=1000,
    ).fit(matrix, response)
    return model, learned, names


def _marginal_parameters(
    model: HuberRegressor,
    names: Sequence[str],
    delta_q_scale: float,
) -> Tuple[float, Dict[str, float]]:
    index = {name: idx for idx, name in enumerate(names)}
    center_phi = float(model.coef_[index["DELTA_Q_Z"]]) / float(delta_q_scale)
    coefficients = {}
    for name, idx in index.items():
        if name.startswith("INTERACTION:"):
            feature = name.split(":", 1)[1]
            coefficients[feature] = float(model.coef_[idx]) / float(delta_q_scale)
    return center_phi, coefficients


def _bootstrap_summary(values: Sequence[float]) -> Dict[str, float]:
    array = np.asarray(list(values), dtype=float)
    if array.size == 0:
        return {}
    return {
        "p10": float(np.quantile(array, 0.10)),
        "p25": float(np.quantile(array, 0.25)),
        "median": float(np.quantile(array, 0.50)),
        "p75": float(np.quantile(array, 0.75)),
        "p90": float(np.quantile(array, 0.90)),
        "min": float(np.min(array)),
        "max": float(np.max(array)),
    }


def fit_model_based_local_gain(
    frame: pd.DataFrame,
    config: ModelBasedLocalGainTrainerConfig,
    *,
    condition_snapshot_version: str,
    mfac_context_id: str,
    grid_id: str,
) -> ModelBasedLocalGainCandidate:
    """Fit one context/grid historical marginal-gain candidate."""
    required = _required_columns(config)
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise ValueError("missing model-based gain columns: %s" % ", ".join(missing))

    work = frame.loc[:, required].copy()
    work[config.event_time_column] = pd.to_datetime(
        work[config.event_time_column], errors="coerce"
    )
    numeric_columns = [name for name in required if name != config.event_time_column]
    for name in numeric_columns:
        work[name] = pd.to_numeric(work[name], errors="coerce")
    work = work.dropna().reset_index(drop=True)
    event_count = int(len(work))
    days = int(work[config.event_time_column].dt.date.nunique()) if event_count else 0
    reasons = []
    if event_count < int(config.minimum_event_count):
        reasons.append("INSUFFICIENT_EVENT_COUNT")
    if days < int(config.minimum_independent_days):
        reasons.append("INSUFFICIENT_INDEPENDENT_DAYS")
    if reasons:
        return ModelBasedLocalGainCandidate(
            status="INSUFFICIENT_EVIDENCE",
            condition_snapshot_version=str(condition_snapshot_version),
            mfac_context_id=str(mfac_context_id),
            grid_id=str(grid_id),
            event_count=event_count,
            independent_days=days,
            phi_so2_center=None,
            phi_ph_center=None,
            phi_so2_surface_coefficients={},
            phi_ph_surface_coefficients={},
            feature_center={},
            feature_scale={},
            support_min={},
            support_max={},
            bootstrap_so2={},
            bootstrap_ph={},
            so2_physical_sign_probability=0.0,
            ph_physical_sign_probability=0.0,
            confidence_so2_candidate=0.0,
            confidence_ph_candidate=0.0,
            delta_q_center=None,
            delta_q_scale=None,
            delta_q_relative_scale=None,
            reason_codes=tuple(reasons),
            metadata={"operator_action_imitation": False},
        )

    so2_model, scaling, names = _fit_channel(
        work, config.so2_response_column, config
    )
    ph_model, _, _ = _fit_channel(
        work, config.ph_response_column, config, scaling=scaling
    )
    dq_center, dq_scale = scaling[config.delta_q_column]
    relative_scale = abs(float(dq_scale)) / max(abs(float(dq_center)), 1e-12)
    if relative_scale < float(config.minimum_relative_delta_q_scale):
        reasons.append("INSUFFICIENT_DELTA_Q_VARIATION")

    phi_so2, so2_coefficients = _marginal_parameters(so2_model, names, dq_scale)
    phi_ph, ph_coefficients = _marginal_parameters(ph_model, names, dq_scale)

    rng = np.random.default_rng(int(config.random_seed))
    bootstrap_so2 = []
    bootstrap_ph = []
    for _ in range(int(config.bootstrap_iterations)):
        indices = rng.integers(0, event_count, event_count)
        sample = work.iloc[indices].reset_index(drop=True)
        try:
            so2_boot, boot_scaling, boot_names = _fit_channel(
                sample, config.so2_response_column, config
            )
            ph_boot, _, _ = _fit_channel(
                sample, config.ph_response_column, config, scaling=boot_scaling
            )
            boot_dq_scale = boot_scaling[config.delta_q_column][1]
            so2_value, _ = _marginal_parameters(so2_boot, boot_names, boot_dq_scale)
            ph_value, _ = _marginal_parameters(ph_boot, boot_names, boot_dq_scale)
        except Exception:
            continue
        if math.isfinite(so2_value):
            bootstrap_so2.append(float(so2_value))
        if math.isfinite(ph_value):
            bootstrap_ph.append(float(ph_value))

    minimum_bootstrap = max(20, int(config.bootstrap_iterations) // 2)
    if len(bootstrap_so2) < minimum_bootstrap or len(bootstrap_ph) < minimum_bootstrap:
        reasons.append("INSUFFICIENT_BOOTSTRAP_FITS")
    so2_sign_probability = (
        float(np.mean(np.asarray(bootstrap_so2) < 0.0)) if bootstrap_so2 else 0.0
    )
    ph_sign_probability = (
        float(np.mean(np.asarray(bootstrap_ph) > 0.0)) if bootstrap_ph else 0.0
    )
    if phi_so2 >= 0.0 or so2_sign_probability < float(config.minimum_physical_sign_probability):
        reasons.append("SO2_MARGINAL_DIRECTION_NOT_STABLE")
    if phi_ph <= 0.0 or ph_sign_probability < float(config.minimum_physical_sign_probability):
        reasons.append("PH_MARGINAL_DIRECTION_NOT_STABLE")

    support_factor = min(
        1.0,
        event_count / float(config.confidence_reference_event_count),
    ) * min(
        1.0,
        days / float(config.confidence_reference_day_count),
    )
    confidence_so2 = max(0.0, min(1.0, support_factor * so2_sign_probability))
    confidence_ph = max(0.0, min(1.0, support_factor * ph_sign_probability))

    feature_center: Dict[str, float] = {}
    feature_scale: Dict[str, float] = {}
    support_min: Dict[str, float] = {}
    support_max: Dict[str, float] = {}
    for feature_name, column_name in config.surface_feature_columns:
        center, scale = scaling[column_name]
        values = pd.to_numeric(work[column_name], errors="coerce").to_numpy(dtype=float)
        feature_center[str(feature_name)] = float(center)
        feature_scale[str(feature_name)] = float(scale)
        support_min[str(feature_name)] = float(np.quantile(values, 0.05))
        support_max[str(feature_name)] = float(np.quantile(values, 0.95))

    status = (
        "MODEL_BASED_LOCAL_GAIN_REVIEW_CANDIDATE"
        if not reasons
        else "MODEL_BASED_LOCAL_GAIN_REJECTED"
    )
    return ModelBasedLocalGainCandidate(
        status=status,
        condition_snapshot_version=str(condition_snapshot_version),
        mfac_context_id=str(mfac_context_id),
        grid_id=str(grid_id),
        event_count=event_count,
        independent_days=days,
        phi_so2_center=float(phi_so2),
        phi_ph_center=float(phi_ph),
        phi_so2_surface_coefficients=so2_coefficients,
        phi_ph_surface_coefficients=ph_coefficients,
        feature_center=feature_center,
        feature_scale=feature_scale,
        support_min=support_min,
        support_max=support_max,
        bootstrap_so2=_bootstrap_summary(bootstrap_so2),
        bootstrap_ph=_bootstrap_summary(bootstrap_ph),
        so2_physical_sign_probability=so2_sign_probability,
        ph_physical_sign_probability=ph_sign_probability,
        confidence_so2_candidate=confidence_so2,
        confidence_ph_candidate=confidence_ph,
        delta_q_center=float(dq_center),
        delta_q_scale=float(dq_scale),
        delta_q_relative_scale=float(relative_scale),
        reason_codes=tuple(dict.fromkeys(reasons)),
        metadata={
            "operator_action_imitation": False,
            "estimation_target": "MARGINAL_RESPONSE_DERIVATIVE",
            "model_family": "HUBER_LOCAL_LINEAR_WITH_DELTA_Q_INTERACTIONS",
            "surface_support_quantiles": [0.05, 0.95],
            "confidence_candidate_is_not_probability_of_causality": True,
            "review_required": True,
            "activation_status": "NOT_ACTIVATABLE",
            "learning_permission": False,
            "residual_control_permission": False,
            "dcs_write_permission": False,
        },
    )


__all__ = [
    "MODEL_BASED_LOCAL_GAIN_TRAINER_VERSION",
    "ModelBasedLocalGainTrainerConfig",
    "ModelBasedLocalGainCandidate",
    "fit_model_based_local_gain",
]
