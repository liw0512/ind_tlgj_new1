# -*- coding: utf-8 -*-
"""Real offline-training lifecycle for one integrated Scheme-2 MFAC version.

Process4MapControl already runs the first module before the second module.  This
module fills the missing second-module work:

first-module labelled CSV + exact ConditionSnapshot
    -> strict snapshot/grid/condition alignment check
    -> canonical HistoricalEpisodeEngine
    -> cumulative historical episode store (old episodes remapped by grid_id)
    -> scalar dual-response historical sensitivity candidates
    -> date-blocked validation
    -> auditable version artifacts

Nothing here publishes runtime authority.  The generated sensitivities are
review candidates only.  Online MFAC state is a separate event-driven lifecycle
and always remains namespaced by (condition_snapshot_version, mfac_context_id).
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import numpy as np
import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG as SITE_PLANT_CONFIG
from system.model.config.standard_fields import OUTLET_SO2_COLUMN

from .historical_episode_engine.condition_snapshot_bridge import (
    load_condition_snapshot_index,
    remap_episode_conditions,
    validate_input_frame_alignment,
)
from .historical_episode_engine.pipeline import prepare_raw_data, run_episode_pipeline
from .historical_episode_engine.schema import condition_axis_columns, time_column
from .historical_sensitivity_training_pipeline import (
    build_historical_sensitivity_training_report,
)
from .historical_sensitivity_validation_pipeline import (
    build_historical_sensitivity_validation_report,
)
from .offline_training_config import (
    MFAC_OFFLINE_TRAINING_CONFIG_VERSION,
    OFFLINE_ONLINE_LIFECYCLE_CONTRACT,
    blocked_validation_config,
    historical_adapter_config,
    historical_episode_training_config,
    scalar_gain_trainer_config,
    scalar_model_specs,
)


MFAC_OFFLINE_VERSION_TRAINING_VERSION = (
    "SCHEME2_MFAC_OFFLINE_VERSION_TRAINING_V1_CUMULATIVE_EPISODES"
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if value is pd.NA:
        return None
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            _json_safe(dict(value)),
            handle,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return dict(value) if isinstance(value, dict) else None


def _linear_rate_per_minute(
    frame: pd.DataFrame,
    timestamp_column: str,
    value_column: str,
) -> Optional[float]:
    if frame.empty or value_column not in frame.columns:
        return None
    work = pd.DataFrame(
        {
            "time": pd.to_datetime(frame[timestamp_column], errors="coerce"),
            "value": pd.to_numeric(frame[value_column], errors="coerce"),
        }
    ).dropna()
    if len(work) < 3:
        return None
    t0 = work["time"].iloc[0]
    x = (work["time"] - t0).dt.total_seconds().to_numpy(dtype=float) / 60.0
    y = work["value"].to_numpy(dtype=float)
    x_center = x - float(np.mean(x))
    denominator = float(np.dot(x_center, x_center))
    if denominator <= 1e-12:
        return 0.0
    y_center = y - float(np.mean(y))
    return float(np.dot(x_center, y_center) / denominator)


def _median_numeric(frame: pd.DataFrame, column: str) -> Optional[float]:
    if frame.empty or column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return None
    value = float(values.median())
    return value if math.isfinite(value) else None


def enrich_episode_model_features(
    episodes: pd.DataFrame,
    raw_history: pd.DataFrame,
    plant: Mapping[str, Any],
    training: Mapping[str, Any],
) -> pd.DataFrame:
    """Attach pre-action work-point/trend features required by the gain trainer.

    Features use data strictly before action_start_time.  This keeps the model
    nuisance adjustment reproducible and prevents post-action leakage.
    """
    result = episodes.copy()
    if result.empty:
        return result

    timestamp_column = time_column(dict(plant))
    axes = condition_axis_columns(dict(training))
    baseline_minutes = float(training["episode"]["baseline_minutes"])
    history = raw_history.copy()
    history[timestamp_column] = pd.to_datetime(
        history[timestamp_column], errors="coerce"
    )
    history = history.dropna(subset=[timestamp_column]).sort_values(
        timestamp_column, kind="stable"
    )

    before_axis_1 = []
    before_axis_1_rate = []
    before_axis_2 = []
    before_axis_2_rate = []
    before_out_rate = []

    for start_value in result["action_start_time"]:
        start = pd.Timestamp(start_value)
        baseline_start = start - pd.Timedelta(minutes=baseline_minutes)
        window = history.loc[
            (history[timestamp_column] >= baseline_start)
            & (history[timestamp_column] < start)
        ]
        axis1 = axes[0] if axes else ""
        axis2 = axes[1] if len(axes) > 1 else ""
        before_axis_1.append(_median_numeric(window, axis1) if axis1 else None)
        before_axis_1_rate.append(
            _linear_rate_per_minute(window, timestamp_column, axis1)
            if axis1 else None
        )
        before_axis_2.append(_median_numeric(window, axis2) if axis2 else None)
        before_axis_2_rate.append(
            _linear_rate_per_minute(window, timestamp_column, axis2)
            if axis2 else None
        )
        before_out_rate.append(
            _linear_rate_per_minute(window, timestamp_column, OUTLET_SO2_COLUMN)
        )

    result["before_condition_axis_1"] = before_axis_1
    result["before_condition_axis_1_rate"] = before_axis_1_rate
    result["before_condition_axis_2"] = before_axis_2
    result["before_condition_axis_2_rate"] = before_axis_2_rate
    result["before_outlet_so2_rate"] = before_out_rate
    return result


def _enabled_tower_ids(plant: Mapping[str, Any]) -> tuple[str, ...]:
    values = tuple(
        str(item.get("tower_id") or "").strip()
        for item in plant.get("towers", []) or []
        if item.get("enabled", True) and str(item.get("tower_id") or "").strip()
    )
    if not values:
        raise ValueError("MFAC offline training requires at least one enabled tower")
    return values


def _previous_version_from_snapshot(previous_snapshot: Optional[str]) -> str:
    if not previous_snapshot:
        return ""
    try:
        return load_condition_snapshot_index(previous_snapshot).snapshot_version
    except Exception:
        return ""


def _load_previous_cumulative_episodes(
    output_root: Path,
    previous_version: str,
    current_index,
) -> tuple[pd.DataFrame, Dict[str, Any]]:
    if not previous_version:
        return pd.DataFrame(), {
            "status": "NO_PREVIOUS_VERSION",
            "previous_episode_count": 0,
        }
    previous_path = (
        output_root / "snapshots" / previous_version / "historical_valid_episodes.csv"
    )
    if not previous_path.is_file():
        return pd.DataFrame(), {
            "status": "PREVIOUS_EPISODE_STORE_MISSING",
            "previous_version": previous_version,
            "previous_episode_count": 0,
        }
    previous = pd.read_csv(previous_path, low_memory=False)
    remapped, summary, unresolved = remap_episode_conditions(
        previous,
        current_index,
        strict=True,
        dataset_name="MFAC_CUMULATIVE_HISTORICAL_EPISODES",
    )
    if not unresolved.empty:
        raise ValueError("strict episode remap returned unresolved rows")
    return remapped, {
        "status": "PREVIOUS_EPISODES_REMAPPED",
        "previous_version": previous_version,
        "previous_episode_count": int(len(previous)),
        "remap_summary": summary,
    }


def _load_previous_effective_config(
    output_root: Path,
    previous_version: str,
) -> Optional[Dict[str, Any]]:
    if not previous_version:
        return None
    return _read_json(
        output_root / "snapshots" / previous_version / "offline_effective_config.json"
    )


def _merge_cumulative_episodes(
    previous: pd.DataFrame,
    current: pd.DataFrame,
) -> pd.DataFrame:
    if previous.empty:
        result = current.copy()
    elif current.empty:
        result = previous.copy()
    else:
        result = pd.concat([previous, current], ignore_index=True, sort=False)
    if result.empty:
        return result
    if "episode_id" not in result.columns:
        raise ValueError("historical episode store is missing episode_id")
    result = result.drop_duplicates(subset=["episode_id"], keep="last")
    if "action_start_time" in result.columns:
        result["action_start_time"] = pd.to_datetime(
            result["action_start_time"], errors="coerce"
        )
        result = result.sort_values("action_start_time", kind="stable")
    return result.reset_index(drop=True)


def train_mfac_offline_version(
    *,
    input_csv: str,
    output_root: str,
    condition_snapshot: str,
    mode: str,
    previous_snapshot: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute the actual MFAC offline training step for one integrated version."""
    mode_text = str(mode or "").upper()
    if mode_text not in {"INITIAL", "INCREMENTAL"}:
        raise ValueError("mode must be INITIAL or INCREMENTAL")

    input_path = Path(input_csv).resolve()
    if not input_path.is_file():
        raise FileNotFoundError("MFAC offline input not found: %s" % input_path)
    current_index = load_condition_snapshot_index(condition_snapshot)
    version = current_index.snapshot_version
    root = Path(output_root).resolve()
    snapshot_dir = root / "snapshots" / version
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Hard handshake: the first-module labelled CSV must match exactly the
    # ConditionSnapshot that Process4 passed into this second-module builder.
    alignment_frame = pd.read_csv(input_path, encoding="utf-8-sig", low_memory=False)
    alignment = validate_input_frame_alignment(
        alignment_frame,
        current_index,
        context="MFAC_OFFLINE_%s" % mode_text,
    )

    plant = deepcopy(SITE_PLANT_CONFIG)
    episode_config = historical_episode_training_config()
    raw_history, input_warnings = prepare_raw_data(
        str(input_path),
        plant,
        episode_config,
    )

    previous_version = _previous_version_from_snapshot(previous_snapshot)
    previous_effective = _load_previous_effective_config(root, previous_version)
    current_valid, current_invalid, effective, _ = run_episode_pipeline(
        raw_history,
        plant,
        episode_config,
        previous_effective_config=(
            previous_effective if mode_text == "INCREMENTAL" else None
        ),
        recalibrate=False,
        aggregate_results=False,
    )
    current_valid = enrich_episode_model_features(
        current_valid,
        raw_history,
        plant,
        effective.get("training", episode_config),
    )

    previous_episodes, carry_forward = _load_previous_cumulative_episodes(
        root,
        previous_version if mode_text == "INCREMENTAL" else "",
        current_index,
    )
    cumulative = _merge_cumulative_episodes(previous_episodes, current_valid)

    valid_path = snapshot_dir / "historical_valid_episodes.csv"
    invalid_path = snapshot_dir / "historical_current_invalid_episodes.csv"
    cumulative.to_csv(valid_path, index=False, encoding="utf-8-sig")
    current_invalid.to_csv(invalid_path, index=False, encoding="utf-8-sig")

    effective_path = snapshot_dir / "offline_effective_config.json"
    _write_json(effective_path, effective)

    tower_reports: Dict[str, Any] = {}
    any_accepted = 0
    any_selected = 0
    for tower_id in _enabled_tower_ids(plant):
        adapter = historical_adapter_config(tower_id)
        training_report = build_historical_sensitivity_training_report(
            cumulative,
            adapter_config=adapter,
            trainer_config=scalar_gain_trainer_config(),
            include_pooled_fallback=True,
        )
        validation_report = build_historical_sensitivity_validation_report(
            cumulative,
            adapter_config=adapter,
            model_specs=scalar_model_specs(),
            validation_config=blocked_validation_config(),
            include_pooled_fallback=True,
        )
        training_payload = training_report.to_dict()
        validation_payload = validation_report.to_dict()
        training_path = snapshot_dir / (
            "historical_sensitivity_training_%s.json" % tower_id
        )
        validation_path = snapshot_dir / (
            "historical_sensitivity_validation_%s.json" % tower_id
        )
        _write_json(training_path, training_payload)
        _write_json(validation_path, validation_payload)
        any_accepted += int(training_report.accepted_training_event_count)
        any_selected += int(validation_report.selected_grid_model_count)
        any_selected += int(validation_report.selected_pooled_model_count)
        tower_reports[tower_id] = {
            "training_report_path": str(training_path),
            "validation_report_path": str(validation_path),
            "accepted_training_event_count": int(
                training_report.accepted_training_event_count
            ),
            "full_sample_review_candidate_count": int(
                training_report.review_candidate_count
            ),
            "blocked_validated_grid_candidate_count": int(
                validation_report.selected_grid_model_count
            ),
            "blocked_validated_pooled_candidate_count": int(
                validation_report.selected_pooled_model_count
            ),
        }

    status = (
        "BLOCKED_VALIDATION_REVIEW_CANDIDATES_AVAILABLE"
        if any_selected > 0
        else (
            "OFFLINE_PRIOR_EVIDENCE_AVAILABLE_REVIEW_REQUIRED"
            if any_accepted > 0
            else "OFFLINE_PRIOR_EVIDENCE_INSUFFICIENT"
        )
    )
    summary = {
        "semantics_version": MFAC_OFFLINE_VERSION_TRAINING_VERSION,
        "offline_config_version": MFAC_OFFLINE_TRAINING_CONFIG_VERSION,
        "version": version,
        "mode": mode_text,
        "status": status,
        "condition_alignment": alignment,
        "condition_snapshot": current_index.to_metadata(),
        "input_csv": str(input_path),
        "input_warnings": list(input_warnings),
        "current_valid_episode_count": int(len(current_valid)),
        "current_invalid_episode_count": int(len(current_invalid)),
        "cumulative_valid_episode_count": int(len(cumulative)),
        "carry_forward": carry_forward,
        "tower_reports": tower_reports,
        "lifecycle_contract": deepcopy(OFFLINE_ONLINE_LIFECYCLE_CONTRACT),
        "historical_valid_episodes_path": str(valid_path),
        "historical_current_invalid_episodes_path": str(invalid_path),
        "offline_effective_config_path": str(effective_path),
        "historical_prior_role": "REVIEW_CANDIDATE_ONLY",
        "runtime_prior_reviewed": False,
        "runtime_prior_allowed": False,
        "online_runtime_state_overwrite": False,
        "activation_status": "NOT_ACTIVATABLE",
        "learning_permission": False,
        "residual_control_permission": False,
        "dcs_write_permission": False,
    }
    summary_path = snapshot_dir / "offline_training_report.json"
    _write_json(summary_path, summary)
    summary["offline_training_report_path"] = str(summary_path)
    return summary


__all__ = [
    "MFAC_OFFLINE_VERSION_TRAINING_VERSION",
    "enrich_episode_model_features",
    "train_mfac_offline_version",
]
