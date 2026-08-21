from __future__ import annotations

import math
from typing import Any, Dict, Mapping


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _reasons(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, (list, tuple, set)):
            continue
        for item in value:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
    return result


def normalize_target_flow_status(
    flow_value: Any,
    control_value: Any,
    preview_value: Any,
) -> Dict[str, Any]:
    """Build one read-only view model for both realtime and history pages."""
    flow = _mapping(flow_value)
    control = _mapping(control_value)
    preview = _mapping(preview_value)
    primary = _mapping(control.get("primary"))
    tracking = _mapping(flow.get("tracking"))
    validation = _mapping(tracking.get("validation"))
    global_validation = _mapping(validation.get("global"))
    by_prototype = _mapping(validation.get("by_prototype"))

    prototype_id = str(
        flow.get("prototype_id") or primary.get("prototype_id") or ""
    )
    prototype_validation = _mapping(by_prototype.get(prototype_id))
    phases = []
    for item in preview.get("phases") or []:
        phase = _mapping(item)
        if not phase:
            continue
        phases.append({
            "phase": str(phase.get("phase") or ""),
            "target_flow": _number(phase.get("target_flow")),
            "completion_tolerance": _number(
                phase.get("completion_tolerance")
            ),
        })

    def value(key: str) -> Any:
        candidate = flow.get(key)
        return candidate if candidate is not None else primary.get(key)

    reasons = _reasons(
        flow.get("reason_codes"),
        primary.get("reason_codes"),
        preview.get("reason_codes"),
        global_validation.get("reason_codes"),
        prototype_validation.get("reason_codes"),
    )
    return {
        "available": bool(
            flow.get("available")
            or primary.get("recommendation_type") == "TARGET_SUPPLY_FLOW"
        ),
        "requested_mode": str(control.get("requested_mode") or ""),
        "effective_mode": str(control.get("effective_mode") or ""),
        "primary_type": str(primary.get("recommendation_type") or ""),
        "primary_actionable": bool(primary.get("actionable")),
        "prototype_id": prototype_id,
        "tower_id": str(value("tower_id") or ""),
        "action_direction": str(value("action_direction") or ""),
        "flow_shape": str(value("flow_shape") or ""),
        "flow_execution_profile": str(
            value("flow_execution_profile") or ""
        ),
        "current_flow": _number(value("current_flow")),
        "target_peak_flow": _number(value("target_peak_flow")),
        "target_final_flow": _number(value("target_final_flow")),
        "tracking_state": str(tracking.get("state") or "IDLE"),
        "global_validation_status": str(
            global_validation.get("status") or "WARMUP"
        ),
        "prototype_validation_status": str(
            prototype_validation.get("status") or "WARMUP"
        ),
        "validation_metrics": _mapping(global_validation.get("metrics")),
        "adapter_mode": str(preview.get("adapter_mode") or ""),
        "preview_status": str(preview.get("status") or ""),
        "preview_phases": phases,
        "command_issued": bool(preview.get("command_issued")),
        "dcs_write_attempted": bool(preview.get("dcs_write_attempted")),
        "reason_codes": reasons,
    }
