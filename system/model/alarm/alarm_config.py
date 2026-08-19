"""报警子系统运行参数。

厂级安全边界仍以内部配置为默认事实源；操作员运行设置只覆盖被明确修改的项目：
- 净烟气 SO2 硬安全上限仍读取 ``plant_config.outlet_so2_safe_range``；
- 每座塔 pH 安全边界读取“操作员覆盖后的有效 plant_config”；
- pH 恢复回差继续复用 ``ph_guard_band``。

本文件只保存报警事件自身的防抖、恢复和持久化周期，不复制厂级物理阈值。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

from system.model.config.operator_settings import effective_plant_config
from system.model.config.plant_config import PLANT_CONFIG, enabled_towers
from system.model.config.process4map_config import PROCESS4MAP_CONFIG


@dataclass(frozen=True)
class AlarmRuntimeConfig:
    evaluation_interval_seconds: float = 1.0

    connection_trigger_seconds: float = 5.0
    connection_recovery_seconds: float = 5.0
    realtime_timeout_seconds: float = max(
        60.0,
        float(PROCESS4MAP_CONFIG.runtime.offline_grace_seconds) * 2.0,
    )
    realtime_timeout_trigger_seconds: float = 3.0
    realtime_timeout_recovery_seconds: float = 5.0

    control_block_trigger_seconds: float = 5.0
    control_block_recovery_seconds: float = 5.0
    missing_field_trigger_seconds: float = 30.0
    missing_field_recovery_seconds: float = 10.0

    process_trigger_seconds: float = 30.0
    process_recovery_seconds: float = 60.0
    outlet_so2_recovery_margin: float = 2.0

    persistence_refresh_seconds: float = 30.0


ALARM_RUNTIME_CONFIG = AlarmRuntimeConfig()


def outlet_so2_limits(plant_config: Optional[Mapping] = None) -> Dict[str, float]:
    plant = PLANT_CONFIG if plant_config is None else plant_config
    values = list(plant.get("outlet_so2_safe_range", [0.0, 35.0]) or [0.0, 35.0])
    low = float(values[0]) if values else 0.0
    high = float(values[1]) if len(values) > 1 else 35.0
    margin = max(0.0, float(ALARM_RUNTIME_CONFIG.outlet_so2_recovery_margin))
    return {
        "low": low,
        "high": high,
        "recover_high": max(low, high - margin),
    }


def ph_alarm_specs(plant_config: Optional[Mapping] = None) -> List[Dict[str, object]]:
    plant = effective_plant_config() if plant_config is None else plant_config
    result: List[Dict[str, object]] = []
    for tower in enabled_towers(plant):
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


def required_alarm_fields(plant_config: Optional[Mapping] = None) -> List[Dict[str, str]]:
    plant = PLANT_CONFIG if plant_config is None else plant_config
    items: List[Dict[str, str]] = []
    display_names = _configured_display_names(plant)

    for axis in plant.get("condition_axes", []) or []:
        column = str(axis.get("column", "")).strip()
        if column:
            items.append(
                {
                    "column": column,
                    "display_name": display_names.get(column, column),
                }
            )

    items.append(
        {
            "column": "jyq_SO2",
            "display_name": display_names.get("jyq_SO2", "净烟气 SO₂"),
        }
    )

    for tower in enabled_towers(plant):
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
