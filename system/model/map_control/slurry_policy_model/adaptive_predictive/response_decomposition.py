from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler

from .feature_builder import CausalFeatureConfig, build_causal_one_step_frame


GLOBAL_CONDITION_MODEL_TYPE = "GLOBAL_ARX_PLUS_CONDITION_FLOW_CORRECTION_V1"


@dataclass(frozen=True)
class GlobalConditionResponseConfig:
    """Identification settings for ``G_global + delta_G_condition``.

    The global backbone sees the complete strictly-causal feature set. The
    condition correction is intentionally narrower: it may only correct the
    actual slurry-flow pathway, not relearn arbitrary output/disturbance
    relationships independently inside every 100 mg grid/region.

    The current steel seeded-region contract is also encoded conservatively:
    EDGE_LOW/EDGE_HIGH are Global-only, while C4 is allowed a local Q-path
    correction only with stronger shrinkage than C1-C3.
    """

    global_ridge_alpha: float = 10.0
    condition_ridge_alpha: float = 100.0
    train_ratio: float = 0.70
    minimum_train_rows: int = 500
    minimum_validation_rows: int = 100
    minimum_condition_train_rows: int = 300
    shrinkage_reference_rows: float = 2000.0
    condition_column: str = "condition_label"
    global_only_conditions: tuple[str, ...] = ("10001", "10006")
    low_support_conditions: tuple[str, ...] = ("10005",)
    low_support_shrinkage_multiplier: float = 0.5

    def validate(self) -> None:
        if self.global_ridge_alpha < 0 or self.condition_ridge_alpha < 0:
            raise ValueError("ridge alphas must be non-negative")
        if not 0.5 <= self.train_ratio < 0.95:
            raise ValueError("train_ratio must be in [0.5, 0.95)")
        if self.minimum_train_rows <= 0 or self.minimum_validation_rows <= 0:
            raise ValueError("minimum row counts must be positive")
        if self.minimum_condition_train_rows <= 0:
            raise ValueError("minimum_condition_train_rows must be positive")
        if self.shrinkage_reference_rows < 0:
            raise ValueError("shrinkage_reference_rows must be non-negative")
        if not str(self.condition_column).strip():
            raise ValueError("condition_column cannot be empty")
        if not 0.0 < float(self.low_support_shrinkage_multiplier) <= 1.0:
            raise ValueError("low_support_shrinkage_multiplier must be in (0, 1]")


@dataclass(frozen=True)
class GlobalConditionFitResult:
    model_payload: dict[str, Any]
    validation: dict[str, Any]
    training_frame_rows: int
    condition_model_count: int


@dataclass(frozen=True)
class DualResponseFitResult:
    """SO2 + pH identification result for one tower actual-flow path."""

    tower_id: str
    so2: GlobalConditionFitResult
    ph: GlobalConditionFitResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "tower_id": self.tower_id,
            "so2": {
                "model_payload": self.so2.model_payload,
                "validation": self.so2.validation,
                "training_frame_rows": self.so2.training_frame_rows,
                "condition_model_count": self.so2.condition_model_count,
            },
            "ph": {
                "model_payload": self.ph.model_payload,
                "validation": self.ph.validation,
                "training_frame_rows": self.ph.training_frame_rows,
                "condition_model_count": self.ph.condition_model_count,
            },
        }


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    if actual.size == 0:
        return {
            "mae": 0.0,
            "rmse": 0.0,
            "r2": 0.0,
            "direction_accuracy": 0.0,
        }
    rmse = float(np.sqrt(mean_squared_error(actual, predicted)))
    mae = float(mean_absolute_error(actual, predicted))
    if len(actual) >= 2 and float(np.std(actual)) > 1e-12:
        r2 = float(r2_score(actual, predicted))
    else:
        r2 = 0.0
    direction_accuracy = float(np.mean(np.sign(actual) == np.sign(predicted)))
    return {
        "mae": mae,
        "rmse": rmse,
        "r2": r2,
        "direction_accuracy": direction_accuracy,
    }


def _normalize_condition(value: Any) -> str:
    if value is None:
        return "__UNKNOWN__"
    try:
        if pd.isna(value):
            return "__UNKNOWN__"
    except TypeError:
        pass
    text = str(value).strip()
    if not text:
        return "__UNKNOWN__"
    try:
        numeric = float(text)
        if np.isfinite(numeric) and numeric.is_integer():
            return str(int(numeric))
    except (TypeError, ValueError):
        pass
    return text


def _condition_values(
    source: pd.DataFrame,
    training_frame: pd.DataFrame,
    condition_column: str,
) -> np.ndarray:
    if condition_column not in source.columns:
        raise KeyError(
            "missing condition label column for response correction: %s"
            % condition_column
        )
    indices = pd.to_numeric(training_frame["source_index"], errors="raise").astype(int)
    values = source.loc[indices, condition_column]
    return np.asarray([_normalize_condition(value) for value in values], dtype=object)


def _flow_correction_features(feature_names: Iterable[str]) -> tuple[str, ...]:
    """Only the manipulated Q pathway is allowed to vary by condition."""

    return tuple(
        name
        for name in feature_names
        if name == "flow_level_t" or name.startswith("flow_delta_lag_")
    )


def _fit_condition_models(
    *,
    x_train: np.ndarray,
    residual_train: np.ndarray,
    conditions_train: np.ndarray,
    feature_names: tuple[str, ...],
    config: GlobalConditionResponseConfig,
) -> dict[str, dict[str, Any]]:
    correction_features = _flow_correction_features(feature_names)
    if not correction_features:
        raise RuntimeError("causal feature frame contains no slurry-flow correction features")
    name_to_index = {name: index for index, name in enumerate(feature_names)}
    indices = [name_to_index[name] for name in correction_features]
    result: dict[str, dict[str, Any]] = {}
    global_only = {_normalize_condition(value) for value in config.global_only_conditions}
    low_support = {_normalize_condition(value) for value in config.low_support_conditions}

    for condition in dict.fromkeys(conditions_train.tolist()):
        condition = _normalize_condition(condition)
        if condition in global_only:
            continue
        mask = conditions_train == condition
        row_count = int(np.sum(mask))
        if row_count < config.minimum_condition_train_rows:
            continue

        x_condition = x_train[mask][:, indices]
        y_condition = residual_train[mask]
        scaler = StandardScaler()
        scaled = scaler.fit_transform(x_condition)
        model = Ridge(alpha=float(config.condition_ridge_alpha), fit_intercept=True)
        model.fit(scaled, y_condition)

        reference = float(config.shrinkage_reference_rows)
        shrinkage = 1.0 if reference <= 0 else row_count / (row_count + reference)
        support_policy = "CORE_LOCAL_CORRECTION"
        if condition in low_support:
            shrinkage *= float(config.low_support_shrinkage_multiplier)
            support_policy = "LOW_SUPPORT_STRONG_SHRINKAGE"
        result[condition] = {
            "model_type": "CONDITION_FLOW_RESIDUAL_RIDGE_V1",
            "condition": condition,
            "training_row_count": row_count,
            "feature_names": list(correction_features),
            "scaler_mean": scaler.mean_.astype(float).tolist(),
            "scaler_scale": scaler.scale_.astype(float).tolist(),
            "coefficients": np.asarray(model.coef_, dtype=float).tolist(),
            "intercept": float(model.intercept_),
            "shrinkage_factor": float(shrinkage),
            "ridge_alpha": float(config.condition_ridge_alpha),
            "support_policy": support_policy,
            "semantics": "DELTA_G_CONDITION_ON_Q_PATH_ONLY",
        }
    return result


def _predict_condition_correction(
    *,
    condition_models: Mapping[str, Mapping[str, Any]],
    feature_frame: pd.DataFrame,
    conditions: np.ndarray,
) -> np.ndarray:
    correction = np.zeros(len(feature_frame), dtype=float)
    normalized = np.asarray([_normalize_condition(value) for value in conditions], dtype=object)
    for condition, payload in condition_models.items():
        mask = normalized == _normalize_condition(condition)
        if not np.any(mask):
            continue
        names = [str(v) for v in payload.get("feature_names", [])]
        x = feature_frame.loc[mask, names].to_numpy(dtype=float)
        mean = np.asarray(payload["scaler_mean"], dtype=float)
        scale = np.asarray(payload["scaler_scale"], dtype=float)
        scale = np.where(np.abs(scale) < 1e-12, 1.0, scale)
        coefficients = np.asarray(payload["coefficients"], dtype=float)
        local = ((x - mean) / scale) @ coefficients + float(payload["intercept"])
        correction[mask] = local * float(payload.get("shrinkage_factor", 1.0))
    return correction


def fit_global_condition_response_channel(
    frame: pd.DataFrame,
    *,
    output_column: str,
    tower: dict[str, Any],
    disturbance_columns: Iterable[str],
    context_columns: Iterable[str] = (),
    feature_config: CausalFeatureConfig | None = None,
    response_config: GlobalConditionResponseConfig | None = None,
    expected_q_effect: str | None = None,
) -> GlobalConditionFitResult:
    """Fit one causal output as ``G_global + delta_G_condition``.

    ``G_global`` is a chronological ARX/Ridge model over output history,
    Q_actual history, measured disturbances and configured operating context.
    ``delta_G_condition`` is fit only on the Q features and on training residuals
    from the global model, with support-dependent shrinkage.

    This avoids one independent model per 100 mg cell or region. Seeded edge
    regions remain Global-only; supported core regions may only modify the
    manipulated Q pathway, and C4 receives stronger shrinkage.
    """

    cfg = response_config or GlobalConditionResponseConfig()
    cfg.validate()
    feat_cfg = feature_config or CausalFeatureConfig()

    source = frame.reset_index(drop=True).copy()
    training_frame, feature_names, target_column = build_causal_one_step_frame(
        source,
        output_column=output_column,
        tower=tower,
        disturbance_columns=disturbance_columns,
        context_columns=context_columns,
        config=feat_cfg,
    )
    row_count = len(training_frame)
    minimum = cfg.minimum_train_rows + cfg.minimum_validation_rows
    if row_count < minimum:
        raise ValueError(
            "insufficient causal identification rows: %s < %s" % (row_count, minimum)
        )

    conditions = _condition_values(source, training_frame, cfg.condition_column)
    split = int(row_count * cfg.train_ratio)
    split = max(cfg.minimum_train_rows, split)
    split = min(row_count - cfg.minimum_validation_rows, split)
    if split <= 0 or split >= row_count:
        raise ValueError("unable to construct chronological train/validation split")

    train = training_frame.iloc[:split].reset_index(drop=True)
    valid = training_frame.iloc[split:].reset_index(drop=True)
    condition_train = conditions[:split]
    condition_valid = conditions[split:]

    x_train = train.loc[:, feature_names].to_numpy(dtype=float)
    y_train = train[target_column].to_numpy(dtype=float)
    x_valid = valid.loc[:, feature_names].to_numpy(dtype=float)
    y_valid = valid[target_column].to_numpy(dtype=float)

    global_scaler = StandardScaler()
    x_train_scaled = global_scaler.fit_transform(x_train)
    x_valid_scaled = global_scaler.transform(x_valid)
    global_model = Ridge(alpha=float(cfg.global_ridge_alpha), fit_intercept=True)
    global_model.fit(x_train_scaled, y_train)
    global_train = global_model.predict(x_train_scaled)
    global_valid = global_model.predict(x_valid_scaled)

    residual_train = y_train - global_train
    condition_models = _fit_condition_models(
        x_train=x_train,
        residual_train=residual_train,
        conditions_train=condition_train,
        feature_names=feature_names,
        config=cfg,
    )
    correction_train = _predict_condition_correction(
        condition_models=condition_models,
        feature_frame=train,
        conditions=condition_train,
    )
    correction_valid = _predict_condition_correction(
        condition_models=condition_models,
        feature_frame=valid,
        conditions=condition_valid,
    )
    combined_train = global_train + correction_train
    combined_valid = global_valid + correction_valid

    covered_valid = np.asarray(
        [_normalize_condition(condition) in condition_models for condition in condition_valid],
        dtype=bool,
    )
    coverage_ratio = float(np.mean(covered_valid)) if len(covered_valid) else 0.0

    payload = {
        "model_type": GLOBAL_CONDITION_MODEL_TYPE,
        "target_semantics": "DELTA_OUTPUT_T_PLUS_1",
        "output_column": str(output_column),
        "tower_id": str(tower.get("tower_id", "")),
        "expected_q_effect": expected_q_effect,
        "disturbance_columns": [str(v) for v in disturbance_columns],
        "context_columns": [str(v) for v in context_columns],
        "condition_column": cfg.condition_column,
        "feature_names": list(feature_names),
        "feature_config": asdict(feat_cfg),
        "response_config": asdict(cfg),
        "global_backbone": {
            "model_type": "ARX_RIDGE_GLOBAL_V1",
            "scaler_mean": global_scaler.mean_.astype(float).tolist(),
            "scaler_scale": global_scaler.scale_.astype(float).tolist(),
            "coefficients": np.asarray(global_model.coef_, dtype=float).tolist(),
            "intercept": float(global_model.intercept_),
            "training_row_count": int(len(train)),
        },
        "condition_flow_corrections": condition_models,
        "decomposition": "G_CURRENT = G_GLOBAL + DELTA_G_CONDITION",
        "condition_correction_scope": "Q_PATH_ONLY",
        "edge_policy": "GLOBAL_ONLY",
        "low_support_policy": "STRONGER_SHRINKAGE",
    }
    validation = {
        "split_mode": "CHRONOLOGICAL_TIME_BLOCK",
        "train": {
            "global": _metrics(y_train, global_train),
            "global_plus_condition": _metrics(y_train, combined_train),
        },
        "validation": {
            "global": _metrics(y_valid, global_valid),
            "global_plus_condition": _metrics(y_valid, combined_valid),
        },
        "condition_correction_validation_coverage_ratio": coverage_ratio,
        "condition_model_count": len(condition_models),
        "condition_train_rows": {
            _normalize_condition(condition): int(np.sum(condition_train == condition))
            for condition in dict.fromkeys(condition_train.tolist())
        },
        "global_only_conditions": [
            _normalize_condition(value) for value in cfg.global_only_conditions
        ],
        "low_support_conditions": [
            _normalize_condition(value) for value in cfg.low_support_conditions
        ],
    }
    return GlobalConditionFitResult(
        model_payload=payload,
        validation=validation,
        training_frame_rows=row_count,
        condition_model_count=len(condition_models),
    )


def fit_tower_dual_response(
    frame: pd.DataFrame,
    *,
    tower: dict[str, Any],
    outlet_so2_column: str,
    disturbance_columns: Iterable[str],
    context_columns: Iterable[str] = (),
    feature_config: CausalFeatureConfig | None = None,
    response_config: GlobalConditionResponseConfig | None = None,
) -> DualResponseFitResult:
    """Fit the two response channels required by predictive slurry control."""

    tower_id = str(tower.get("tower_id", ""))
    ph_column = str(tower.get("ph_column", "")).strip()
    if not tower_id:
        raise ValueError("tower_id cannot be empty")
    if not ph_column:
        raise ValueError("tower ph_column cannot be empty")

    so2 = fit_global_condition_response_channel(
        frame,
        output_column=outlet_so2_column,
        tower=tower,
        disturbance_columns=disturbance_columns,
        context_columns=context_columns,
        feature_config=feature_config,
        response_config=response_config,
        expected_q_effect="NEGATIVE_Q_TO_SO2",
    )
    ph = fit_global_condition_response_channel(
        frame,
        output_column=ph_column,
        tower=tower,
        disturbance_columns=disturbance_columns,
        context_columns=context_columns,
        feature_config=feature_config,
        response_config=response_config,
        expected_q_effect="POSITIVE_Q_TO_PH",
    )
    return DualResponseFitResult(tower_id=tower_id, so2=so2, ph=ph)


def predict_global_condition_delta(
    model_payload: Mapping[str, Any],
    feature_frame: pd.DataFrame,
    condition_labels: Iterable[Any],
) -> np.ndarray:
    """Evaluate a persisted global+condition payload on aligned causal features."""

    if str(model_payload.get("model_type")) != GLOBAL_CONDITION_MODEL_TYPE:
        raise ValueError("unsupported global-condition response payload type")
    names = [str(v) for v in model_payload.get("feature_names", [])]
    missing = [name for name in names if name not in feature_frame.columns]
    if missing:
        raise KeyError("missing response features: " + ", ".join(missing))

    backbone = dict(model_payload.get("global_backbone") or {})
    x = feature_frame.loc[:, names].to_numpy(dtype=float)
    mean = np.asarray(backbone["scaler_mean"], dtype=float)
    scale = np.asarray(backbone["scaler_scale"], dtype=float)
    scale = np.where(np.abs(scale) < 1e-12, 1.0, scale)
    coefficients = np.asarray(backbone["coefficients"], dtype=float)
    global_prediction = ((x - mean) / scale) @ coefficients + float(backbone["intercept"])

    conditions = np.asarray(
        [_normalize_condition(value) for value in condition_labels],
        dtype=object,
    )
    if len(conditions) != len(feature_frame):
        raise ValueError("condition_labels must align one-to-one with feature_frame")
    correction = _predict_condition_correction(
        condition_models=model_payload.get("condition_flow_corrections") or {},
        feature_frame=feature_frame,
        conditions=conditions,
    )
    return global_prediction + correction
