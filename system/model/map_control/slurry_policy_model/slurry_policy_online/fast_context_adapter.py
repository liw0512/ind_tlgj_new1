from __future__ import annotations

import json
from typing import Any, Dict


class FastContextError(ValueError):
    pass


def _mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except Exception:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def extract_fast_context(process: Dict[str, Any]) -> Dict[str, Any]:
    required = (
        "fast_change_mode",
        "fast_change_direction",
        "fast_change_exact_trend_mode",
        "fast_change_effect_risk_level",
        "fast_change_overall_risk_level",
        "fast_change_outlet_so2_rate",
    )
    missing = [name for name in required if name not in process]
    if missing:
        raise FastContextError(
            "实时输入缺少上游 fast_change_mode 结果: %s" % missing
        )
    context = {key: value for key, value in process.items() if str(key).startswith("fast_change_")}
    context["fast_change_axis_rates"] = _mapping(process.get("fast_change_axis_rates"))
    return context
