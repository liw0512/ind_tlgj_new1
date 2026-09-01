from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict

from .config import InitialTrainingConfig
from .qbase_calibration import KBaseCalibrationResult


def build_initial_snapshot(
    *,
    snapshot_version: str,
    config: InitialTrainingConfig,
    kbase: KBaseCalibrationResult,
    response_knowledge: dict,
    source_rows: int,
    learnable_action_events: int,
) -> dict:
    """Build immutable long-term knowledge for Module-2 Initial V1."""

    return {
        "schema_version": "1.0",
        "snapshot_version": str(snapshot_version),
        "artifact_type": "BASELINE_ADAPTIVE_FEEDBACK_INITIAL",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "control_family": "NON_PREDICTIVE_BASELINE_ADAPTIVE_FEEDBACK",
        "runtime_contract": {
            "outlet_so2_target_source": "RUNTIME_REQUIRED",
            "historical_target_learned": False,
            "fixed_outlet_so2_target_in_snapshot": False,
            "no_local_response_data_requires_hold": False,
            "fallback_order": [
                "SHRUNK_LOCAL_RESPONSE",
                "GLOBAL_RESPONSE",
                "CONSERVATIVE_STEP",
                "HOLD_ONLY_FOR_SAFETY_DATA_INVALID_OR_PENDING",
            ],
        },
        "qbase": {
            "formula": "Kbase * engineering_raw_qbase",
            "omega_relation": {
                "formula": "omega_percent = k * rho + c",
                "k": float(config.omega_k),
                "c": float(config.omega_c),
                "output_unit": "percent",
                "mass_balance_conversion": "omega_fraction = omega_percent / 100",
            },
            "ca_s_reference": float(config.ca_s_reference),
            "limestone_purity": float(config.limestone_purity),
            "historical_calibration_outlet": "MEASURED_OUTLET_SO2",
            "online_outlet": "RUNTIME_TARGET_SO2",
            "kbase": kbase.to_dict(),
        },
        "response_knowledge": response_knowledge,
        "identification_policy": {
            "response_onset_semantics": "persistent favorable slope change; absolute SO2 reversal is not required",
            "response_effect_semantics": "later effect relative to pre-action local trend, not effect-at-onset",
            "event_grades": {
                "A": "strict disturbance-clean evidence",
                "B": "moderate disturbance evidence with reduced weight",
            },
            "sparse_condition_policy": "shrink local evidence to global; absence of local history never forces HOLD",
            "edge_policy": "EDGE_LOW/EDGE_HIGH use Global response only",
            "c4_policy": "C4 uses stronger shrinkage than C1-C3",
        },
        "training_summary": {
            "source_rows": int(source_rows),
            "learnable_action_events": int(learnable_action_events),
        },
        "safety": {
            "shadow_only": True,
            "dcs_write_enabled": False,
            "online_control_implemented": False,
        },
    }


def build_initial_report(
    *,
    snapshot: dict,
    action_grade_counts: Dict[str, int],
    response_status_counts: Dict[str, int],
    qbase_metrics: dict,
) -> dict:
    return {
        "schema_version": snapshot["schema_version"],
        "snapshot_version": snapshot["snapshot_version"],
        "artifact_type": "BASELINE_ADAPTIVE_FEEDBACK_INITIAL_REPORT",
        "qbase": {
            "kbase": snapshot["qbase"]["kbase"],
            "backtest": qbase_metrics,
            "runtime_target_required": True,
        },
        "events": {
            "action_grade_counts": action_grade_counts,
            "response_status_counts": response_status_counts,
        },
        "global_response": snapshot["response_knowledge"].get("responses", {}),
        "condition_response": snapshot["response_knowledge"].get("conditions", {}),
        "readiness": {
            "offline_initial_built": True,
            "incremental_not_built_yet": True,
            "online_not_built_yet": True,
            "no_local_data_requires_hold": False,
        },
    }
