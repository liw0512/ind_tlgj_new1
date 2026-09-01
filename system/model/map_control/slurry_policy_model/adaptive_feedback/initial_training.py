from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Dict, Tuple

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[5]
    project_root_text = str(project_root)
    if project_root_text not in sys.path:
        sys.path.insert(0, project_root_text)

import numpy as np
import pandas as pd

from system.model.map_control.slurry_policy_model.adaptive_feedback.config import InitialTrainingConfig
from system.model.map_control.slurry_policy_model.adaptive_feedback.event_bootstrap import extract_action_events
from system.model.map_control.slurry_policy_model.adaptive_feedback.qbase_calibration import calibrate_kbase, evaluate_kbase
from system.model.map_control.slurry_policy_model.adaptive_feedback.response_estimator import (
    build_hierarchical_response_knowledge,
    estimate_event_responses,
)
from system.model.map_control.slurry_policy_model.adaptive_feedback.snapshot import (
    build_initial_report,
    build_initial_snapshot,
)


def _window_metrics(backtest: pd.DataFrame) -> dict:
    metrics: Dict[str, dict] = {}
    if backtest.empty:
        return metrics
    for hours, rows in backtest.groupby("window_hours"):
        usable = rows.loc[
            rows["accepted"].astype(bool)
            & np.isfinite(pd.to_numeric(rows["qbase_effective_mean_m3h"], errors="coerce"))
            & np.isfinite(pd.to_numeric(rows["qactual_mean_m3h"], errors="coerce"))
        ]
        if usable.empty:
            continue
        pred = usable["qbase_effective_mean_m3h"].to_numpy(dtype=float)
        actual = usable["qactual_mean_m3h"].to_numpy(dtype=float)
        bias = pred - actual
        correlation = (
            float(np.corrcoef(pred, actual)[0, 1])
            if len(usable) >= 2 and np.std(pred) > 0 and np.std(actual) > 0
            else None
        )
        metrics["%dh" % int(hours)] = {
            "window_count": int(len(usable)),
            "qbase_mean_m3h": float(np.mean(pred)),
            "qactual_mean_m3h": float(np.mean(actual)),
            "bias_m3h": float(np.mean(bias)),
            "mae_m3h": float(np.mean(np.abs(bias))),
            "rmse_m3h": float(np.sqrt(np.mean(bias ** 2))),
            "correlation": correlation,
            "cumulative_volume_error_m3": float(usable["effective_volume_error_m3"].sum()),
        }
    return metrics


def _temporal_holdout(frame: pd.DataFrame, config: InitialTrainingConfig) -> Tuple[dict, pd.DataFrame]:
    fraction = float(config.kbase_holdout_fraction)
    if fraction <= 0.0 or len(frame) < 100:
        return {"available": False, "reason": "HOLDOUT_DISABLED_OR_TOO_SMALL"}, pd.DataFrame()

    ordered = frame.copy()
    timestamps = pd.to_datetime(ordered[config.timestamp_column], errors="coerce")
    ordered = ordered.assign(__ts=timestamps).dropna(subset=["__ts"])
    ordered = ordered.sort_values("__ts", kind="stable").drop(columns=["__ts"]).reset_index(drop=True)
    split = int(round(len(ordered) * (1.0 - fraction)))
    split = max(1, min(split, len(ordered) - 1))
    train = ordered.iloc[:split].copy()
    holdout = ordered.iloc[split:].copy()

    try:
        train_calibration, _, _ = calibrate_kbase(train, config)
    except ValueError as exc:
        return {
            "available": False,
            "reason": "INSUFFICIENT_CALIBRATION_HISTORY",
            "detail": str(exc),
        }, pd.DataFrame()

    holdout_backtest = evaluate_kbase(holdout, config, kbase=train_calibration.kbase, hours_list=(1, 6, 24))
    holdout_backtest["evaluation_scope"] = "TEMPORAL_HOLDOUT"
    return {
        "available": True,
        "calibration_fraction": 1.0 - fraction,
        "holdout_fraction": fraction,
        "train_kbase": train_calibration.to_dict(),
        "metrics": _window_metrics(holdout_backtest),
    }, holdout_backtest


def build_initial_artifacts(
    frame: pd.DataFrame,
    *,
    config: InitialTrainingConfig | None = None,
    snapshot_version: str = "v001",
) -> Tuple[dict, dict, pd.DataFrame, pd.DataFrame]:
    """Build Module-2 offline Initial V1 artifacts from historical 10-second data."""

    cfg = config or InitialTrainingConfig()
    cfg.validate()

    final_kbase, full_backtest, _ = calibrate_kbase(frame, cfg)
    full_backtest["evaluation_scope"] = "FULL_HISTORY_FINAL_KBASE"
    holdout_report, holdout_backtest = _temporal_holdout(frame, cfg)
    qbase_backtest = pd.concat([full_backtest, holdout_backtest], ignore_index=True, sort=False)

    action_events, response_work = extract_action_events(frame, cfg)
    response_rows = estimate_event_responses(action_events, response_work, cfg)
    response_knowledge = build_hierarchical_response_knowledge(response_rows, cfg)

    learnable_count = int(action_events["learnable"].sum()) if not action_events.empty else 0
    snapshot = build_initial_snapshot(
        snapshot_version=snapshot_version,
        config=cfg,
        kbase=final_kbase,
        response_knowledge=response_knowledge,
        source_rows=len(frame),
        learnable_action_events=learnable_count,
    )

    grade_counts = (
        {str(key): int(value) for key, value in action_events["quality_grade"].value_counts().items()}
        if not action_events.empty
        else {}
    )
    status_counts = (
        {
            "%s:%s" % (response, status): int(value)
            for (response, status), value in response_rows.groupby(["response", "response_status"]).size().items()
        }
        if not response_rows.empty
        else {}
    )
    report = build_initial_report(
        snapshot=snapshot,
        action_grade_counts=grade_counts,
        response_status_counts=status_counts,
        qbase_metrics={
            "full_history_final_kbase": _window_metrics(full_backtest),
            "temporal_holdout": holdout_report,
        },
    )

    if response_rows.empty:
        event_export = action_events.copy()
        if not event_export.empty:
            event_export["response"] = "ACTION_ONLY"
    else:
        action_metadata = action_events.drop(
            columns=[
                column
                for column in (
                    "action_time",
                    "direction",
                    "condition_label",
                    "quality_grade",
                    "event_weight",
                    "delta_q_actual_m3h",
                )
                if column in action_events.columns
            ],
            errors="ignore",
        )
        event_export = response_rows.merge(action_metadata, on="event_id", how="left")
        rejected = action_events.loc[~action_events["learnable"].astype(bool)].copy()
        if not rejected.empty:
            rejected["response"] = "ACTION_ONLY_REJECTED"
            event_export = pd.concat([event_export, rejected], ignore_index=True, sort=False)

    return snapshot, report, qbase_backtest, event_export


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Module-2 non-predictive offline Initial V1 snapshot.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--snapshot-version", default="v001")
    parser.add_argument("--condition-column", default="condition_label")
    parser.add_argument("--timestamp-column", default="date")
    parser.add_argument("--max-rows", type=int)
    return parser


def main() -> None:
    args = _parser().parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    frame = pd.read_csv(input_path)
    if args.max_rows is not None:
        if args.max_rows <= 0:
            raise ValueError("--max-rows must be positive")
        frame = frame.iloc[: args.max_rows].copy()

    config = replace(
        InitialTrainingConfig(),
        condition_column=str(args.condition_column),
        timestamp_column=str(args.timestamp_column),
    )
    snapshot, report, backtest, events = build_initial_artifacts(
        frame,
        config=config,
        snapshot_version=args.snapshot_version,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_dir / "module2_initial_snapshot.json"
    report_path = output_dir / "module2_initial_report.json"
    backtest_path = output_dir / "qbase_backtest.csv"
    events_path = output_dir / "response_events.csv"

    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    backtest.to_csv(backtest_path, index=False, encoding="utf-8-sig")
    events.to_csv(events_path, index=False, encoding="utf-8-sig")

    print(
        json.dumps(
            {
                "snapshot": str(snapshot_path),
                "report": str(report_path),
                "qbase_backtest": str(backtest_path),
                "response_events": str(events_path),
                "source_rows": int(len(frame)),
                "kbase": snapshot["qbase"]["kbase"],
                "learnable_action_events": snapshot["training_summary"]["learnable_action_events"],
                "global_response": snapshot["response_knowledge"].get("responses", {}),
                "runtime_so2_target": "REQUIRED_ONLINE_NOT_LEARNED_OFFLINE",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
