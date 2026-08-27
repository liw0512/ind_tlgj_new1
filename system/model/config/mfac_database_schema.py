"""Canonical MFAC extension for the model-result monthly table.

The base database schema is kept for non-breaking migration and still contains
legacy ``slurry_policy_*`` compatibility columns.  This module adds the formal
``mfac_*`` fields emitted by ``MFACUnifiedRuntimePolicy`` and is the schema used
by ``Process4MapControlMFAC``.
"""
from __future__ import annotations

import datetime
import json
import math
import uuid
from collections import OrderedDict
from typing import Any, Iterable, Mapping

import pandas as pd

from system.model.config.database_schema import (
    MODEL_RESULT_FIELD_TYPES as LEGACY_MODEL_RESULT_FIELD_TYPES,
    ensure_monthly_table,
    monthly_table_name,
)


_MFAC_RESULT_FIELDS = OrderedDict([
    ("mfac_loaded_version", "varchar(32)"),
    ("second_module_type", "varchar(32)"),
    ("second_module_algorithm_target_supply_flow", "float8"),
    ("second_module_dcs_write_enabled", "boolean"),
    ("mfac_decision_id", "varchar(128)"),
    ("mfac_timestamp", "timestamp(6)"),
    ("mfac_model_type", "varchar(32)"),
    ("mfac_runtime_version", "varchar(96)"),
    ("mfac_model_version", "varchar(32)"),
    ("mfac_condition_snapshot_version", "varchar(32)"),
    ("mfac_condition_label", "varchar(128)"),
    ("mfac_base_condition_id", "varchar(64)"),
    ("mfac_grid_id", "varchar(64)"),
    ("mfac_policy_region_id", "varchar(128)"),
    ("mfac_control_mode", "varchar(64)"),
    ("mfac_runtime_mode", "varchar(64)"),
    ("mfac_disturbance_mode", "varchar(64)"),
    ("mfac_current_so2", "float8"),
    ("mfac_commanded_target", "float8"),
    ("mfac_effective_target", "float8"),
    ("mfac_experience_source", "varchar(64)"),
    ("mfac_action_id", "varchar(128)"),
    ("mfac_action_family", "varchar(128)"),
    ("mfac_action_direction", "varchar(64)"),
    ("mfac_action_magnitude", "varchar(64)"),
    ("mfac_decision_status", "varchar(64)"),
    ("mfac_reason_codes", "jsonb"),
    ("mfac_qbase", "jsonb"),
    ("mfac_qbase_source", "varchar(64)"),
    ("mfac_qbase_valid", "boolean"),
    ("mfac_qbase_raw", "float8"),
    ("mfac_qbase_effective", "float8"),
    ("mfac_residual_mfac_hold", "float8"),
    ("mfac_algorithm_target_supply_flow", "float8"),
    ("mfac_algorithm_target_valid", "boolean"),
    ("mfac_algorithm_target_status", "varchar(64)"),
    ("mfac_algorithm_target", "jsonb"),
    ("mfac_runtime_cycle", "jsonb"),
    ("mfac_learn_enabled", "boolean"),
    ("mfac_residual_enabled", "boolean"),
    ("mfac_dcs_write_enabled", "boolean"),
    ("mfac_target_supply_flow", "jsonb"),
    ("mfac_control_recommendation", "jsonb"),
    ("mfac_target_flow_execution_preview", "jsonb"),
    ("mfac_debug", "jsonb"),
    ("mfac_integration_valid", "boolean"),
    ("mfac_integration_error", "text"),
    ("mfac_output_json", "jsonb"),
    ("mfac_runtime_config_status", "varchar(64)"),
    ("mfac_runtime_config_error", "text"),
    ("mfac_runtime_configured", "boolean"),
])

MFAC_MODEL_RESULT_FIELD_TYPES = OrderedDict(LEGACY_MODEL_RESULT_FIELD_TYPES)
MFAC_MODEL_RESULT_FIELD_TYPES.update(_MFAC_RESULT_FIELDS)

_JSONB_FIELDS = {
    name for name, type_name in MFAC_MODEL_RESULT_FIELD_TYPES.items()
    if type_name.lower() == "jsonb"
}
_BOOLEAN_FIELDS = {
    name for name, type_name in MFAC_MODEL_RESULT_FIELD_TYPES.items()
    if type_name.lower() == "boolean"
}
_TIMESTAMP_FIELDS = {
    name for name, type_name in MFAC_MODEL_RESULT_FIELD_TYPES.items()
    if type_name.lower().startswith("timestamp")
}
_JSONB_OBJECT_FIELDS = {
    "fast_change_axis_rates",
    "slurry_policy_target_supply_flow",
    "slurry_policy_control_recommendation",
    "slurry_policy_target_flow_execution_preview",
    "mfac_qbase",
    "mfac_algorithm_target",
    "mfac_runtime_cycle",
    "mfac_target_supply_flow",
    "mfac_control_recommendation",
    "mfac_target_flow_execution_preview",
    "mfac_debug",
    "mfac_output_json",
}


def _quote(name: str) -> str:
    return '"%s"' % str(name).replace('"', '""')


def _normalize_scalar(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def _as_bool(value: Any):
    value = _normalize_scalar(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _json_value(value: Any, *, default_object: bool = False) -> str:
    value = _normalize_scalar(value)
    if value is None:
        value = {} if default_object else []
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except Exception:
            pass
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _timestamp_value(value: Any):
    value = _normalize_scalar(value)
    if value is None:
        return None
    try:
        return pd.to_datetime(value).to_pydatetime()
    except Exception:
        return None


def ensure_mfac_model_result_table(engine: Any, prefix: str) -> str:
    name = monthly_table_name(prefix)
    return ensure_monthly_table(
        engine,
        name,
        MFAC_MODEL_RESULT_FIELD_TYPES,
        alias=(
            f"供浆MFAC模型结果表_{datetime.datetime.now().year}_"
            f"{datetime.datetime.now().month}"
        ),
    )


def insert_mfac_model_result_row(
    engine: Any,
    table_name: str,
    data: Mapping[str, Any],
) -> None:
    names = list(MFAC_MODEL_RESULT_FIELD_TYPES.keys())
    values = []
    placeholders = []
    for name in names:
        value = uuid.uuid4() if name == "id" else data.get(name)
        if name == "date" and value is None:
            value = pd.Timestamp.now()
        if name in _JSONB_FIELDS:
            values.append(
                _json_value(
                    value,
                    default_object=name in _JSONB_OBJECT_FIELDS,
                )
            )
            placeholders.append("%s::jsonb")
        elif name in _BOOLEAN_FIELDS:
            values.append(_as_bool(value))
            placeholders.append("%s")
        elif name in _TIMESTAMP_FIELDS:
            values.append(_timestamp_value(value))
            placeholders.append("%s")
        else:
            values.append(_normalize_scalar(value))
            placeholders.append("%s")

    sql = (
        f"INSERT INTO {_quote(table_name)} "
        f"({', '.join(_quote(name) for name in names)}) VALUES "
        f"({', '.join(placeholders)})"
    )
    engine.execute(sql, tuple(values))


def mfac_model_result_columns() -> Iterable[str]:
    return tuple(MFAC_MODEL_RESULT_FIELD_TYPES.keys())


__all__ = [
    "MFAC_MODEL_RESULT_FIELD_TYPES",
    "ensure_mfac_model_result_table",
    "insert_mfac_model_result_row",
    "mfac_model_result_columns",
]
