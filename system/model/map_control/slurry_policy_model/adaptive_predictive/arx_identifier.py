from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler

from .feature_builder import (
    CausalFeatureConfig,
    build_causal_one_step_frame,
)


@dataclass(frozen=True)
class ArxIdentifierConfig:
    ridge_alpha: float = 10.0
    train_ratio: float = 0.70
    minimum_train_rows: int = 500
    minimum_validation_rows: int = 100

    def validate(self) -> None:
        if self.ridge_alpha < 0:
            raise ValueError("ridge_alpha must be non-negative")
        if not 0.5 <= self.train_ratio < 0.95:
            raise ValueError("train_ratio must be in [0.5, 0.95)")
        if self.minimum_train_rows <= 0 or self.minimum_validation_rows <= 0:
            raise ValueError("minimum row counts must be positive")


@dataclass(frozen=True)
class ArxFitResult:
    model_payload: dict[str, Any]
    validation: dict[str, Any]
    training_frame_rows: int


def _direction_accuracy(actual: np.ndarray, predicted: np.ndarray) -> float:
    if len(actual) == 0:
        return 0.0
    actual_sign = np.sign(actual)
    predicted_sign = np.sign(predicted)
    return float(np.mean(actual_sign == predicted_sign))


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    rmse = float(np.sqrt(mean_squared_error(actual, predicted)))
    mae = float(mean_absolute_error(actual, predicted))
    r2 = float(r2_score(actual, predicted)) if len(actual) >= 2 else 0.0
    direction_accuracy = _direction_accuracy(actual, predicted)
    baseline = np.zeros_like(actual)
    baseline_rmse = float(np.sqrt(mean_squared_error(actual, baseline)))
    baseline_mae = float(mean_absolute_error(actual, baseline))
    return {
        "mae": mae,
        "rmse": rmse,
        "r2": r2,
        "direction_accuracy": direction_accuracy,
        "zero_delta_baseline_mae": baseline_mae,
        "zero_delta_baseline_rmse": baseline_rmse,
        "rmse_improvement_ratio": (
            0.0 if baseline_rmse <= 1e-12 else 1.0 - rmse / baseline_rmse
        ),
    }


def fit_arx_ridge_channel(
    frame: pd.DataFrame,
    *,
    output_column: str,
    tower: dict[str, Any],
    disturbance_columns: Iterable[str],
    context_columns: Iterable[str] = (),
    feature_config: CausalFeatureConfig | None = None,
    identifier_config: ArxIdentifierConfig | None = None,
) -> ArxFitResult:
    """Fit a chronological, strictly-causal one-step ARX/Ridge baseline.

    This is intentionally a transparent baseline for P1, not the final causal
    estimator.  It simultaneously conditions on actual slurry-flow history and
    measured plant disturbances, which is already safer than learning
    ``future_output ~ Q`` alone under closed-loop operator control.
    """

    id_cfg = identifier_config or ArxIdentifierConfig()
    id_cfg.validate()
    feat_cfg = feature_config or CausalFeatureConfig()
    training_frame, feature_names, target_column = build_causal_one_step_frame(
        frame,
        output_column=output_column,
        tower=tower,
        disturbance_columns=disturbance_columns,
        context_columns=context_columns,
        config=feat_cfg,
    )
    row_count = len(training_frame)
    if row_count < id_cfg.minimum_train_rows + id_cfg.minimum_validation_rows:
        raise ValueError(
            "insufficient causal identification rows: %s < %s"
            % (
                row_count,
                id_cfg.minimum_train_rows + id_cfg.minimum_validation_rows,
            )
        )

    split = int(row_count * id_cfg.train_ratio)
    split = max(id_cfg.minimum_train_rows, split)
    split = min(row_count - id_cfg.minimum_validation_rows, split)
    if split <= 0 or split >= row_count:
        raise ValueError("unable to construct chronological train/validation split")

    train = training_frame.iloc[:split]
    valid = training_frame.iloc[split:]
    x_train = train.loc[:, feature_names].to_numpy(dtype=float)
    y_train = train[target_column].to_numpy(dtype=float)
    x_valid = valid.loc[:, feature_names].to_numpy(dtype=float)
    y_valid = valid[target_column].to_numpy(dtype=float)

    scaler = StandardScaler()
    x_train_scaled = scaler.fit_transform(x_train)
    x_valid_scaled = scaler.transform(x_valid)

    model = Ridge(alpha=float(id_cfg.ridge_alpha), fit_intercept=True)
    model.fit(x_train_scaled, y_train)
    pred_train = model.predict(x_train_scaled)
    pred_valid = model.predict(x_valid_scaled)

    payload = {
        "model_type": "ARX_RIDGE_V1",
        "target_semantics": "DELTA_OUTPUT_T_PLUS_1",
        "output_column": str(output_column),
        "tower_id": str(tower.get("tower_id", "")),
        "disturbance_columns": [str(v) for v in disturbance_columns],
        "context_columns": [str(v) for v in context_columns],
        "feature_names": list(feature_names),
        "feature_config": asdict(feat_cfg),
        "identifier_config": asdict(id_cfg),
        "scaler_mean": scaler.mean_.astype(float).tolist(),
        "scaler_scale": scaler.scale_.astype(float).tolist(),
        "coefficients": np.asarray(model.coef_, dtype=float).tolist(),
        "intercept": float(model.intercept_),
        "training_row_count": int(len(train)),
        "validation_row_count": int(len(valid)),
    }
    validation = {
        "split_mode": "CHRONOLOGICAL",
        "train": _metrics(y_train, pred_train),
        "validation": _metrics(y_valid, pred_valid),
    }
    return ArxFitResult(
        model_payload=payload,
        validation=validation,
        training_frame_rows=row_count,
    )


def predict_arx_delta(
    model_payload: dict[str, Any],
    feature_frame: pd.DataFrame,
) -> np.ndarray:
    """Evaluate a persisted ARX_RIDGE_V1 payload on an aligned feature frame."""

    if str(model_payload.get("model_type")) != "ARX_RIDGE_V1":
        raise ValueError("unsupported ARX payload type")
    names = [str(v) for v in model_payload.get("feature_names", [])]
    missing = [name for name in names if name not in feature_frame.columns]
    if missing:
        raise KeyError("missing ARX features: " + ", ".join(missing))
    x = feature_frame.loc[:, names].to_numpy(dtype=float)
    mean = np.asarray(model_payload["scaler_mean"], dtype=float)
    scale = np.asarray(model_payload["scaler_scale"], dtype=float)
    scale = np.where(np.abs(scale) < 1e-12, 1.0, scale)
    x_scaled = (x - mean) / scale
    coefficients = np.asarray(model_payload["coefficients"], dtype=float)
    return x_scaled @ coefficients + float(model_payload["intercept"])
