from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Support both invocation styles:
#   python path/to/train_dual_response.py ...
#   python -m system.model.map_control.slurry_policy_model.adaptive_predictive.train_dual_response ...
# When a deeply nested file is executed directly, Python places only that file's
# directory on sys.path, so the repository-level ``system`` package is otherwise
# not importable.
if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[5]
    project_root_text = str(project_root)
    if project_root_text not in sys.path:
        sys.path.insert(0, project_root_text)

import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG
from system.model.map_control.slurry_policy_model.adaptive_predictive.config import (
    build_foundation_spec,
)
from system.model.map_control.slurry_policy_model.adaptive_predictive.feature_builder import (
    CausalFeatureConfig,
)
from system.model.map_control.slurry_policy_model.adaptive_predictive.identification_diagnostics import (
    evaluate_q_path_ablation,
)
from system.model.map_control.slurry_policy_model.adaptive_predictive.response_decomposition import (
    GlobalConditionResponseConfig,
    fit_tower_dual_response,
)


def _enabled_tower(plant: dict[str, Any], tower_id: str | None) -> dict[str, Any]:
    towers = [
        dict(item)
        for item in (plant.get("towers", []) or [])
        if item.get("enabled", True)
    ]
    if tower_id:
        for tower in towers:
            if str(tower.get("tower_id")) == str(tower_id):
                return tower
        raise KeyError("enabled tower not found: %s" % tower_id)
    if len(towers) != 1:
        raise ValueError("--tower-id is required when plant has multiple enabled towers")
    return towers[0]


def _prepare_time_segments(
    frame: pd.DataFrame,
    *,
    timestamp_column: str,
    sample_seconds: int,
    max_gap_multiple: float,
) -> pd.DataFrame:
    """Sort chronologically and split lags at long/invalid time gaps."""

    if timestamp_column not in frame.columns:
        if "continuous_segment_id" not in frame.columns:
            frame = frame.copy()
            frame["continuous_segment_id"] = 0
        return frame.reset_index(drop=True)

    result = frame.copy()
    timestamps = pd.to_datetime(result[timestamp_column], errors="coerce")
    if timestamps.isna().any():
        raise ValueError("invalid timestamps exist in %s" % timestamp_column)
    result["__predictive_timestamp"] = timestamps
    result = result.sort_values("__predictive_timestamp", kind="stable").reset_index(drop=True)
    delta_seconds = result["__predictive_timestamp"].diff().dt.total_seconds()
    max_gap = float(sample_seconds) * float(max_gap_multiple)
    new_segment = delta_seconds.isna() | (delta_seconds <= 0) | (delta_seconds > max_gap)
    result["continuous_segment_id"] = new_segment.cumsum().astype(int) - 1
    result = result.drop(columns=["__predictive_timestamp"])
    return result


def _condition_delta(validation: dict[str, Any]) -> dict[str, float]:
    global_metrics = validation["validation"]["global"]
    combined_metrics = validation["validation"]["global_plus_condition"]
    global_rmse = float(global_metrics["rmse"])
    global_mae = float(global_metrics["mae"])
    return {
        "rmse_improvement_ratio": (
            0.0 if global_rmse <= 1e-12 else 1.0 - float(combined_metrics["rmse"]) / global_rmse
        ),
        "mae_improvement_ratio": (
            0.0 if global_mae <= 1e-12 else 1.0 - float(combined_metrics["mae"]) / global_mae
        ),
        "r2_delta": float(combined_metrics["r2"]) - float(global_metrics["r2"]),
        "direction_accuracy_delta": (
            float(combined_metrics["direction_accuracy"])
            - float(global_metrics["direction_accuracy"])
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train shadow-only SO2+pH global/condition slurry response models."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--tower-id")
    parser.add_argument("--timestamp-column", default="date")
    parser.add_argument("--context-columns", nargs="*", default=[])
    parser.add_argument("--condition-column", default="condition_label")
    parser.add_argument("--sample-seconds", type=int, default=10)
    parser.add_argument("--max-gap-multiple", type=float, default=3.0)
    parser.add_argument("--flow-lags", type=int, default=60)
    parser.add_argument("--disturbance-lags", type=int, default=60)
    parser.add_argument("--output-lags", type=int, default=6)
    parser.add_argument("--context-lags", type=int, default=6)
    parser.add_argument("--global-ridge-alpha", type=float, default=10.0)
    parser.add_argument("--condition-ridge-alpha", type=float, default=100.0)
    parser.add_argument("--minimum-condition-train-rows", type=int, default=300)
    parser.add_argument("--shrinkage-reference-rows", type=float, default=2000.0)
    parser.add_argument("--max-rows", type=int)
    return parser


def main() -> None:
    args = _parser().parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    frame = pd.read_csv(input_path)
    if args.max_rows is not None:
        if args.max_rows <= 0:
            raise ValueError("--max-rows must be positive")
        frame = frame.iloc[: args.max_rows].copy()

    predictive_config = {
        "sample_seconds": args.sample_seconds,
        "context_columns": list(args.context_columns),
        "condition_label_column": args.condition_column,
        "shadow_only": True,
    }
    foundation = build_foundation_spec(PLANT_CONFIG, predictive_config)
    tower = _enabled_tower(PLANT_CONFIG, args.tower_id)
    prepared = _prepare_time_segments(
        frame,
        timestamp_column=args.timestamp_column,
        sample_seconds=foundation.sample_seconds,
        max_gap_multiple=args.max_gap_multiple,
    )

    feature_config = CausalFeatureConfig(
        output_delta_lags=args.output_lags,
        flow_delta_lags=args.flow_lags,
        disturbance_delta_lags=args.disturbance_lags,
        context_delta_lags=args.context_lags,
        causal_flow_filter_points=3,
        segment_column="continuous_segment_id",
    )
    response_config = GlobalConditionResponseConfig(
        global_ridge_alpha=args.global_ridge_alpha,
        condition_ridge_alpha=args.condition_ridge_alpha,
        minimum_condition_train_rows=args.minimum_condition_train_rows,
        shrinkage_reference_rows=args.shrinkage_reference_rows,
        condition_column=foundation.condition_label_column,
    )
    result = fit_tower_dual_response(
        prepared,
        tower=tower,
        outlet_so2_column=foundation.outlet_so2_column,
        disturbance_columns=foundation.disturbance_columns,
        context_columns=foundation.context_columns,
        feature_config=feature_config,
        response_config=response_config,
    )

    so2_q_ablation = evaluate_q_path_ablation(
        prepared,
        output_column=foundation.outlet_so2_column,
        tower=tower,
        disturbance_columns=foundation.disturbance_columns,
        context_columns=foundation.context_columns,
        feature_config=feature_config,
        response_config=response_config,
    )
    ph_column = str(tower.get("ph_column", ""))
    ph_q_ablation = evaluate_q_path_ablation(
        prepared,
        output_column=ph_column,
        tower=tower,
        disturbance_columns=foundation.disturbance_columns,
        context_columns=foundation.context_columns,
        feature_config=feature_config,
        response_config=response_config,
    )

    artifact = {
        "schema_version": "1.1",
        "artifact_type": "DUAL_RESPONSE_GLOBAL_CONDITION_IDENTIFICATION",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_input": str(input_path),
        "source_rows": int(len(frame)),
        "prepared_rows": int(len(prepared)),
        "continuous_segment_count": int(prepared["continuous_segment_id"].nunique()),
        "foundation": foundation.to_dict(),
        "safety": {
            "shadow_only": True,
            "dcs_write_enabled": False,
            "target_supply_flow_activation": False,
        },
        "dual_response": result.to_dict(),
        "identification_diagnostics": {
            "so2_q_path_ablation": so2_q_ablation.to_dict(),
            "ph_q_path_ablation": ph_q_ablation.to_dict(),
            "so2_condition_correction_delta": _condition_delta(result.so2.validation),
            "ph_condition_correction_delta": _condition_delta(result.ph.validation),
            "acceptance_note": (
                "Good one-step prediction alone does not establish causal slurry response. "
                "Q-path ablation must show repeatable validation value before delay/gain "
                "interpretation, and later closed-loop deconfounding is still required."
            ),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as stream:
        json.dump(artifact, stream, ensure_ascii=False, indent=2)

    summary = {
        "output": str(output_path),
        "segments": artifact["continuous_segment_count"],
        "so2_training_rows": result.so2.training_frame_rows,
        "ph_training_rows": result.ph.training_frame_rows,
        "so2_condition_models": result.so2.condition_model_count,
        "ph_condition_models": result.ph.condition_model_count,
        "so2_validation": result.so2.validation["validation"],
        "ph_validation": result.ph.validation["validation"],
        "so2_condition_correction_delta": _condition_delta(result.so2.validation),
        "ph_condition_correction_delta": _condition_delta(result.ph.validation),
        "so2_q_path_ablation": so2_q_ablation.validation["validation"],
        "ph_q_path_ablation": ph_q_ablation.validation["validation"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
