"""当前供浆系统数据库表结构与写入的唯一实现。

数据库只保留两类月表：
- t_data1_filter_rt_YYYY_M：data_preprocessor1 处理后的基础历史数据；
- t_model_result_YYYY_M：同一基础数据 + condition_model + slurry_policy_model 在线结果。

旧 t_data1_rt_*、cluster/Q-learning/PH_predict 结果字段不再属于当前架构。
"""
from __future__ import annotations

import datetime
import json
import math
import uuid
from collections import OrderedDict
from typing import Any, Dict, Iterable, Mapping, Optional

import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG
from system.model.config.standard_fields import TARGET_SO2_COLUMN


# 用户当前 data_preprocessor1 输出的基础字段。顺序同时作为 INSERT 顺序。
_BASE_FIELDS = OrderedDict([
    ("id", "uuid NOT NULL"),
    ("date", "timestamp(6) NOT NULL"),
    ("xstshsjy_MD", "float8"),
    ("xstgjb_ADL", "float8"),
    ("xstgjb_BDL", "float8"),
    ("xst_FMKD1", "float8"),
    ("xst_FMKD2", "float8"),
    ("yyq_SO2", "float8"),
    ("jyq_SO2", "float8"),
    ("yyq_O2", "float8"),
    ("yyq_LL", "float8"),
    ("jyq_LL", "float8"),
    ("xst_YW", "float8"),
    ("xstjyxhb_ADL", "float8"),
    ("xstjyxhb_BDL", "float8"),
    ("xstjyxhb_CDL", "float8"),
    ("xstjyxhb_DDL", "float8"),
    ("xstjyxhb_EDL", "float8"),
    ("xstyhfj_ADL", "float8"),
    ("xstjy_PH", "float8"),
    ("xst_ADL_status", "int"),
    ("xst_BDL_status", "int"),
    ("xst_CDL_status", "int"),
    ("xst_DDL_status", "int"),
    ("xst_EDL_status", "int"),
    ("xst_pump_status", "varchar(40)"),
    ("combined_pump_status", "varchar(80)"),
    ("liquid_gas_ratio", "float8"),
    ("desulfurization_efficiency", "float8"),
    # 在线目标是运行事实，存在时一起留档；没有时保持 NULL。
    (TARGET_SO2_COLUMN, "float8"),
])


def _append_plant_dynamic_fields(fields: OrderedDict) -> OrderedDict:
    """保证工况轴、启用塔 pH/阀门/供浆泵字段也能进入基础历史表。"""
    result = OrderedDict(fields)
    for axis in PLANT_CONFIG.get("condition_axes", []):
        column = str(axis.get("column", "")).strip()
        if column:
            result.setdefault(column, "float8")
    for tower in PLANT_CONFIG.get("towers", []):
        if not tower.get("enabled", True):
            continue
        ph_column = str(tower.get("ph_column", "")).strip()
        if ph_column:
            result.setdefault(ph_column, "float8")
        for valve in tower.get("valves", []):
            column = str(valve.get("column", "")).strip()
            if column:
                result.setdefault(column, "float8")
        for pump in tower.get("supply_pumps", []):
            column = str(pump.get("current_column", "")).strip()
            if column:
                result.setdefault(column, "float8")
    return result


FILTER_FIELD_TYPES = _append_plant_dynamic_fields(_BASE_FIELDS)

# 第一模块 + 同版本管理字段。condition_label 正式替代旧 cluster_label。
_CONDITION_RESULT_FIELDS = OrderedDict([
    # 独立 FAST_CHANGE 上游上下文；复杂轴速率/原因使用 jsonb。
    ("fast_change_mode", "varchar(32)"),
    ("fast_change_active", "boolean"),
    ("fast_change_recovery_active", "boolean"),
    ("fast_change_raw_trigger", "boolean"),
    ("fast_change_direction", "varchar(32)"),
    ("fast_change_severity", "varchar(32)"),
    ("fast_change_exact_trend_mode", "varchar(96)"),
    ("fast_change_trend_risk_level", "varchar(32)"),
    ("fast_change_effect_risk_level", "varchar(32)"),
    ("fast_change_effect_state", "varchar(48)"),
    ("fast_change_overall_risk_level", "varchar(32)"),
    ("fast_change_axis_rates", "jsonb"),
    ("fast_change_trigger_axes", "jsonb"),
    ("fast_change_outlet_so2_rate", "float8"),
    ("fast_change_outlet_so2_trend", "varchar(32)"),
    ("fast_change_reason_codes", "jsonb"),
    ("condition_snapshot_version", "varchar(32)"),
    ("raw_grid_id", "varchar(64)"),
    ("raw_base_condition_id", "varchar(64)"),
    ("raw_condition_label", "varchar(128)"),
    ("stable_grid_id", "varchar(64)"),
    ("stable_base_condition_id", "varchar(64)"),
    ("stable_condition_label", "varchar(128)"),
    ("grid_id", "varchar(64)"),
    ("base_condition_id", "varchar(64)"),
    ("condition_label", "varchar(128)"),
    ("policy_region_id", "varchar(128)"),
    ("region_status", "varchar(64)"),
    ("region_member_count", "int"),
    ("coverage_status", "varchar(40)"),
    ("state_key", "varchar(256)"),
    ("condition_experience_source", "varchar(64)"),
    ("condition_valid", "boolean"),
    ("condition_stable", "boolean"),
    ("out_of_range_clipped", "boolean"),
    ("clip_axis", "varchar(128)"),
    ("condition_switch_state", "varchar(40)"),
    ("stability_sample_count", "int"),
    ("majority_count", "int"),
    ("majority_tied", "boolean"),
    ("condition_reason", "text"),
    ("integrated_active_version", "varchar(32)"),
    ("condition_loaded_version", "varchar(32)"),
    ("slurry_policy_loaded_version", "varchar(32)"),
    ("version_consistent", "boolean"),
    ("version_switch_state", "varchar(64)"),
    ("version_switch_time", "varchar(64)"),
    ("version_switch_error", "text"),
])

# 第二模块正式在线输出。复杂阀门结构使用 jsonb，不把 xst_v1/v2/v3 写死进数据库列。
_POLICY_RESULT_FIELDS = OrderedDict([
    ("slurry_policy_decision_id", "varchar(64)"),
    ("slurry_policy_timestamp", "timestamp(6)"),
    ("slurry_policy_model_version", "varchar(32)"),
    ("slurry_policy_control_mode", "varchar(40)"),
    ("slurry_policy_disturbance_mode", "varchar(40)"),
    ("slurry_policy_commanded_target", "float8"),
    ("slurry_policy_effective_target", "float8"),
    ("slurry_policy_desired_so2_response", "varchar(40)"),
    ("slurry_policy_experience_source", "varchar(64)"),
    ("slurry_policy_action_id", "varchar(256)"),
    ("slurry_policy_action_family", "varchar(128)"),
    ("slurry_policy_action_direction", "varchar(32)"),
    ("slurry_policy_action_magnitude", "varchar(32)"),
    ("slurry_policy_recommended_valve_deltas", "jsonb"),
    ("slurry_policy_projected_valve_openings", "jsonb"),
    ("slurry_policy_historical_reliability", "float8"),
    ("slurry_policy_historical_safety_score", "float8"),
    ("slurry_policy_historical_direction_consistency", "float8"),
    ("slurry_policy_decision_status", "varchar(40)"),
    ("slurry_policy_reason_codes", "jsonb"),
    ("slurry_policy_integration_valid", "boolean"),
    ("slurry_policy_integration_error", "text"),
    ("model_seq", "bigint"),
])

MODEL_RESULT_FIELD_TYPES = OrderedDict(FILTER_FIELD_TYPES)
MODEL_RESULT_FIELD_TYPES.update(_CONDITION_RESULT_FIELDS)
MODEL_RESULT_FIELD_TYPES.update(_POLICY_RESULT_FIELDS)

_JSONB_FIELDS = {
    name for name, type_name in MODEL_RESULT_FIELD_TYPES.items()
    if type_name.lower() == "jsonb"
}
_BOOLEAN_FIELDS = {
    name for name, type_name in MODEL_RESULT_FIELD_TYPES.items()
    if type_name.lower() == "boolean"
}
_TIMESTAMP_FIELDS = {
    name for name, type_name in MODEL_RESULT_FIELD_TYPES.items()
    if type_name.lower().startswith("timestamp")
}


def monthly_table_name(prefix: str, when: Optional[datetime.datetime] = None) -> str:
    when = when or datetime.datetime.now()
    return f"{prefix}{when.year}_{when.month}"


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


def _as_bool(value: Any) -> Optional[bool]:
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


def _json_value(value: Any) -> str:
    value = _normalize_scalar(value)
    if value is None:
        value = []
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except Exception:
            pass
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _timestamp_value(value: Any) -> Any:
    value = _normalize_scalar(value)
    if value is None:
        return None
    try:
        return pd.to_datetime(value).to_pydatetime()
    except Exception:
        return None


def table_exists(engine: Any, table_name: str) -> bool:
    row = engine.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema='public' AND table_name=%s
        )
        """,
        (table_name,),
    ).fetchone()
    return bool(row[0]) if row else False


def ensure_monthly_table(
    engine: Any,
    table_name: str,
    field_types: Mapping[str, str],
    *,
    alias: str,
) -> str:
    """非破坏式建表/补列：永远不 DROP 已有月表。"""
    columns_sql = ", ".join(
        f"{_quote(name)} {type_name}" for name, type_name in field_types.items()
    )
    engine.execute(
        f"CREATE TABLE IF NOT EXISTS {_quote('public')}.{_quote(table_name)} ({columns_sql})"
    )

    # 已有月表可能来自旧版本，逐列补齐新 schema，不删除旧列和旧数据。
    for name, type_name in field_types.items():
        # NOT NULL 不能直接用于 ADD COLUMN，先按基础类型补列。
        add_type = type_name.replace(" NOT NULL", "")
        engine.execute(
            f"ALTER TABLE {_quote('public')}.{_quote(table_name)} "
            f"ADD COLUMN IF NOT EXISTS {_quote(name)} {add_type}"
        )

    # id 主键与 date 索引。旧表已有约束时异常应由 PostgreSQL 避免重复检查。
    pk_name = f"pk_{table_name}"
    pk_exists = engine.execute(
        "SELECT 1 FROM pg_constraint WHERE conname=%s LIMIT 1",
        (pk_name,),
    ).fetchone()
    if not pk_exists:
        try:
            engine.execute(
                f"ALTER TABLE {_quote('public')}.{_quote(table_name)} "
                f"ADD CONSTRAINT {_quote(pk_name)} PRIMARY KEY ({_quote('id')})"
            )
        except Exception:
            # 兼容旧表已经存在其他名字的主键。
            pass

    index_name = f"idx_{table_name}_date"
    engine.execute(
        f"CREATE INDEX IF NOT EXISTS {_quote(index_name)} "
        f"ON {_quote('public')}.{_quote(table_name)} ({_quote('date')})"
    )

    # t_table_name 仅做目录注册；若目录表不存在则不阻断核心写库。
    try:
        engine.execute(
            """
            INSERT INTO t_table_name(id, table_name, table_alias)
            SELECT %s, %s, %s
            WHERE NOT EXISTS (
                SELECT 1 FROM t_table_name WHERE table_name=%s
            )
            """,
            (uuid.uuid4(), table_name, alias, table_name),
        )
    except Exception:
        pass
    return table_name


def latest_monthly_table(engine: Any, prefix: str) -> Optional[str]:
    rows = engine.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
    ).fetchall()
    candidates = []
    for row in rows:
        name = str(row[0])
        if not name.startswith(prefix):
            continue
        tail = name[len(prefix):]
        parts = tail.split("_")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            continue
        candidates.append((int(parts[0]), int(parts[1]), name))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def _insert_row(
    engine: Any,
    table_name: str,
    field_types: Mapping[str, str],
    data: Mapping[str, Any],
) -> None:
    names = list(field_types.keys())
    values = []
    placeholders = []
    for name in names:
        value = uuid.uuid4() if name == "id" else data.get(name)
        if name == "date" and value is None:
            value = pd.Timestamp.now()
        if name in _JSONB_FIELDS:
            values.append(_json_value(value))
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


def ensure_filter_table(engine: Any, prefix: str) -> str:
    name = monthly_table_name(prefix)
    return ensure_monthly_table(
        engine,
        name,
        FILTER_FIELD_TYPES,
        alias=f"数据过滤表_{datetime.datetime.now().year}_{datetime.datetime.now().month}",
    )


def ensure_model_result_table(engine: Any, prefix: str) -> str:
    name = monthly_table_name(prefix)
    return ensure_monthly_table(
        engine,
        name,
        MODEL_RESULT_FIELD_TYPES,
        alias=f"供浆模型结果表_{datetime.datetime.now().year}_{datetime.datetime.now().month}",
    )


def insert_filter_row(engine: Any, table_name: str, data: Mapping[str, Any]) -> None:
    _insert_row(engine, table_name, FILTER_FIELD_TYPES, data)


def insert_model_result_row(engine: Any, table_name: str, data: Mapping[str, Any]) -> None:
    _insert_row(engine, table_name, MODEL_RESULT_FIELD_TYPES, data)


def model_result_columns() -> Iterable[str]:
    return tuple(MODEL_RESULT_FIELD_TYPES.keys())
