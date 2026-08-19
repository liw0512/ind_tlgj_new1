"""报警子系统运行参数。

厂级安全边界仍以 ``plant_config.py`` 为唯一事实源：
- 净烟气 SO2 硬安全上限读取 ``outlet_so2_safe_range``；
- 每座塔 pH 安全边界读取 ``ph_safe_range``；
- pH 恢复回差优先复用 ``ph_guard_band``。

本文件只保存报警事件自身的防抖、恢复和持久化周期，不复制厂级物理阈值。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping

from system.model.config.plant_config import PLANT_CONFIG, enabled_towers
from system.model.config.process4map_config import PROCESS4MAP_CONFIG


@dataclass(frozen=True)
class AlarmRuntimeConfig:
    # 报警管理器建议由外层每 1 秒调用一次 evaluate；这里不依赖 Qt/线程。
    evaluation_interval_seconds: float = 1.0

    # 通讯与实时数据。
    connection_trigger_seconds: float = 5.0
    connection_recovery_seconds: float = 5.0
    realtime_timeout_seconds: float = max(
        60.0,
        float(PROCESS4MAP_CONFIG.runtime.offline_grace_seconds) * 2.0,
    )
    realtime_timeout_trigger_seconds: float = 3.0
    realtime_timeout_recovery_seconds: float = 5.0

    # 控制链与关键输入。
    control_block_trigger_seconds: float = 5.0
    control_block_recovery_seconds: float = 5.0
    missing_field_trigger_seconds: float = 30.0
    missing_field_recovery_seconds: float = 10.0

    # 工艺安全报警。
    process_trigger_seconds: float = 30.0
    process_recovery_seconds: float = 60.0
    outlet_so2_recovery_margin: float = 2.0  # mg/Nm3，恢复阈值 = 安全上限 - margin。

    # 活动报警只低频刷新数据库；开始/恢复事件始终立即入队。
    persistence_refresh_seconds: float = 30.0


ALARM_RUNTIME_CONFIG = AlarmRuntimeConfig()


def outlet_so2_limits(plant_config: Mapping = PLANT_CONFIG) -> Dict[str, float]:
    values = list(plant_config.get("outlet_so2_safe_range", [0.0, 35.0]) or [0.0, 35.0])
    low = float(values[0]) if values else 0.0
    high = float(values[1]) if len(values) > 1 else 35.0
    margin = max(0.0, float(ALARM_RUNTIME_CONFIG.outlet_so2_recovery_margin))
    return {
        "low": low,
        "high": high,
        "recover_high": max(low, high - margin),
    }


def ph_alarm_specs(plant_config: Mapping = PLANT_CONFIG) -> List[Dict[str, object]]:
    result: List[Dict[str, object]] = []
    for tower in enabled_towers(plant_config):
        column = str(tower.get("ph_column", "")).strip()
        safe_range = list(tower.get("ph_safe_range", []) or [])
        if not column or len(safe_range) < 2:
            continue
        low = float(safe_range[0])
        high = float(safe_range[1])
        guard = max(0.0, float(tower.get("ph_guard_band", 0.0) or 0.0))
        recover_low = min(high, low + guard)
        recover_high = max(low, high - guard)
        result.append(
            {
                "tower_id": str(tower.get("tower_id", "")).strip(),
                "display_name": str(tower.get("display_name") or "吸收塔"),
                "column": column,
                "low": low,
                "high": high,
                "recover_low": recover_low,
                "recover_high": recover_high,
            }
        )
    return result


def _configured_display_names(plant_config: Mapping) -> Dict[str, str]:
    """收集 plant_config 已定义的中文测点名，报警页不直接暴露内部字段名。"""
    result: Dict[str, str] = {}
    monitor = plant_config.get("realtime_monitor", {}) or {}
    for group_name in ("inlet_signals", "outlet_signals", "auxiliary_signals"):
        for item in monitor.get(group_name, []) or []:
            column = str(item.get("column", "")).strip()
            if column:
                result[column] = str(item.get("display_name") or column)

    for tower in enabled_towers(plant_config):
        tower_name = str(tower.get("display_name") or "吸收塔")
        ph_column = str(tower.get("ph_column", "")).strip()
        if ph_column:
            result.setdefault(ph_column, f"{tower_name}浆液 pH")
        for group_name in (
            "monitor_fields",
            "valves",
            "supply_flows",
            "monitor_supply_pumps",
            "circulation_pumps",
        ):
            for item in tower.get(group_name, []) or []:
                column = str(
                    item.get("column")
                    or item.get("value_column")
                    or ""
                ).strip()
                if column:
                    result[column] = str(item.get("display_name") or column)
    return result


def required_alarm_fields(plant_config: Mapping = PLANT_CONFIG) -> List[Dict[str, str]]:
    """返回第一版报警需要关注的关键输入字段。

    仅收集真正影响工况判别、排放安全、pH 安全和当前阀门动作解析的字段，
    不把所有实时监控测点都视为关键输入，避免报警泛滥。
    """
    items: List[Dict[str, str]] = []
    display_names = _configured_display_names(plant_config)

    for axis in plant_config.get("condition_axes", []) or []:
        column = str(axis.get("column", "")).strip()
        if column:
            items.append(
                {
                    "column": column,
                    "display_name": display_names.get(column, column),
                }
            )

    # 净烟气 SO2 是核心安全与目标字段。
    items.append(
        {
            "column": "jyq_SO2",
            "display_name": display_names.get("jyq_SO2", "净烟气 SO₂"),
        }
    )

    for tower in enabled_towers(plant_config):
        tower_name = str(tower.get("display_name") or "吸收塔")
        ph_column = str(tower.get("ph_column", "")).strip()
        if ph_column:
            items.append(
                {
                    "column": ph_column,
                    "display_name": display_names.get(ph_column, f"{tower_name}浆液 pH"),
                }
            )
        for valve in tower.get("valves", []) or []:
            column = str(valve.get("column", "")).strip()
            if column:
                items.append(
                    {
                        "column": column,
                        "display_name": display_names.get(
                            column,
                            str(valve.get("display_name") or column),
                        ),
                    }
                )

    dedup: Dict[str, Dict[str, str]] = {}
    for item in items:
        dedup.setdefault(item["column"], item)
    return list(dedup.values())
