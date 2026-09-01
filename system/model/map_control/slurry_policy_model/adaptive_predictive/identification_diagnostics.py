from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler

from .feature_builder import CausalFeatureConfig, build_causal_one_step_frame
from .response_decomposition import GlobalConditionResponseConfig


@dataclass(frozen=True)
class QPathAblationResult:
    output_column: str
    training_frame_rows: int
    full_feature_count: int
    no_q_feature_count: int
    validation: dict[str, Any]
    q_path_coefficients: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_column": self.output_column,
            "training_frame_rows": self.training_frame_rows,
            "full_feature_count": self.full_feature_count,
            "no_q_feature_count": self.no_q_feature_count,
            "validation": self.validation,
            "q_path_coefficients": self.q_path_coefficients,
            "coefficient_note": (
                "Q coefficients are diagnostic only; correlated lags mean the largest "
                "coefficient must not be interpreted directly as physical dead time."
            ),
        }


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
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


def _is_q_feature(name: str) -> bool:
    return name == "flow_level_t" or name.startswith("flow_delta_lag_")


def _fit_predict(
    train: pd.DataFrame,
    valid: pd.DataFrame,
    feature_names: list[str],
    target_column: str,
    ridge_alpha: float,
) -> tuple[np.ndarray, np.ndarray, StandardScaler, Ridge]:
    x_train = train.loc[:, feature_names].to_numpy(dtype=float)
    x_valid = valid.loc[:, feature_names].to_numpy(dtype=float)
    y_train = train[target_column].to_numpy(dtype=float)

    scaler = StandardScaler()
    x_train_scaled = scaler.fit_transform(x_train)
    x_valid_scaled = scaler.transform(x_valid)
    model = Ridge(alpha=float(ridge_alpha), fit_intercept=True)
    model.fit(x_train_scaled, y_train)
    return (
        model.predict(x_train_scaled),
        model.predict(x_valid_scaled),
        scaler,
        model,
    )


def evaluate_q_path_ablation(
    frame: pd.DataFrame,
    *,
    output_column: str,
    tower: dict[str, Any],
    disturbance_columns: Iterable[str],
    context_columns: Iterable[str] = (),
    feature_config: CausalFeatureConfig | None = None,
    response_config: GlobalConditionResponseConfig | None = None,
) -> QPathAblationResult:
    """Measure the incremental one-step predictive value of actual slurry flow.

    Two models are fit on the exact same chronological train/validation split:
    ``FULL_GLOBAL`` contains all strictly causal features; ``NO_Q_GLOBAL``
    removes ``flow_level_t`` and every ``flow_delta_lag_*`` feature.  The
    difference is a diagnostic for whether Q history contributes repeatable
    out-of-sample information beyond output history, measured disturbances and
    operating context.

    This is still a predictive ablation, not proof of causal identification.
    Closed-loop deconfounding/orthogonalization remains a later acceptance gate.
    """

    cfg = response_config or GlobalConditionResponseConfig()
    cfg.validate()
    feat_cfg = feature_config or CausalFeatureConfig()

    source = frame.reset_index(drop=True).copy()
    training_frame, feature_names_tuple, target_column = build_causal_one_step_frame(
        source,
        output_column=output_column,
        tower=tower,
        disturbance_columns=disturbance_columns,
        context_columns=context_columns,
        config=feat_cfg,
    )
    feature_names = list(feature_names_tuple)
    no_q_features = [name for name in feature_names if not _is_q_feature(name)]
    q_features = [name for name in feature_names if _is_q_feature(name)]
    if not q_features:
        raise RuntimeError("no Q-path features found for ablation")
    if not no_q_features:
        raise RuntimeError("Q-path ablation requires non-Q nuisance features")

    row_count = len(training_frame)
    minimum = cfg.minimum_train_rows + cfg.minimum_validation_rows
    if row_count < minimum:
        raise ValueError(
            "insufficient causal identification rows: %s < %s" % (row_count, minimum)
        )

    split = int(row_count * cfg.train_ratio)
    split = max(cfg.minimum_train_rows, split)
    split = min(row_count - cfg.minimum_validation_rows, split)
    if split <= 0 or split >= row_count:
        raise ValueError("unable to construct chronological train/validation split")

    train = training_frame.iloc[:split].reset_index(drop=True)
    valid = training_frame.iloc[split:].reset_index(drop=True)
    y_train = train[target_column].to_numpy(dtype=float)
    y_valid = valid[target_column].to_numpy(dtype=float)

    full_train, full_valid, full_scaler, full_model = _fit_predict(
        train,
        valid,
        feature_names,
        target_column,
        cfg.global_ridge_alpha,
    )
    no_q_train, no_q_valid, _, _ = _fit_predict(
        train,
        valid,
        no_q_features,
        target_column,
        cfg.global_ridge_alpha,
    )

    full_valid_metrics = _metrics(y_valid, full_valid)
    no_q_valid_metrics = _metrics(y_valid, no_q_valid)
    full_train_metrics = _metrics(y_train, full_train)
    no_q_train_metrics = _metrics(y_train, no_q_train)

    no_q_rmse = float(no_q_valid_metrics["rmse"])
    no_q_mae = float(no_q_valid_metrics["mae"])
    q_rmse_improvement = (
        0.0 if no_q_rmse <= 1e-12 else 1.0 - full_valid_metrics["rmse"] / no_q_rmse
    )
    q_mae_improvement = (
        0.0 if no_q_mae <= 1e-12 else 1.0 - full_valid_metrics["mae"] / no_q_mae
    )

    raw_coefficients = np.asarray(full_model.coef_, dtype=float) / np.where(
        np.abs(full_scaler.scale_) < 1e-12,
        1.0,
        np.asarray(full_scaler.scale_, dtype=float),
    )
    q_path_coefficients = {
        name: float(raw_coefficients[index])
        for index, name in enumerate(feature_names)
        if _is_q_feature(name)
    }

    return QPathAblationResult(
        output_column=str(output_column),
        training_frame_rows=row_count,
        full_feature_count=len(feature_names),
        no_q_feature_count=len(no_q_features),
        validation={
            "split_mode": "CHRONOLOGICAL_TIME_BLOCK",
            "train": {
                "full_global": full_train_metrics,
                "no_q_global": no_q_train_metrics,
            },
            "validation": {
                "full_global": full_valid_metrics,
                "no_q_global": no_q_valid_metrics,
                "q_incremental_rmse_improvement_ratio": float(q_rmse_improvement),
                "q_incremental_mae_improvement_ratio": float(q_mae_improvement),
            },
            "interpretation": (
                "Positive improvement means Q history adds out-of-sample predictive "
                "information beyond output history, measured disturbances and context. "
                "It does not by itself prove causal Q->output dynamics."
            ),
        },
        q_path_coefficients=q_path_coefficients,
    )
