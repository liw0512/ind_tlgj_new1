"""操作员运行设置覆盖层。

原则：
- 算法/plant_config 中的值始终是默认值；
- 本文件只持久化操作员明确修改过的项目；
- 未设置的项目继续读取内部默认值；
- “恢复默认”通过删除对应覆盖项实现，而不是把默认值复制进覆盖文件。

目前只开放：
1. 净烟气 SO2 控制目标；
2. 各启用吸收塔的 pH 安全范围。

方案2已经完全替代旧 ``slurry_policy_model``。SO2 目标允许范围直接读取
``PLANT_CONFIG['scheme2']['so2_control']``，默认目标为 8.0 mg/Nm³；本模块不得再
依赖已经删除的旧第二模块配置。
"""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from system.model.config.plant_config import PLANT_CONFIG


PROJECT_ROOT = Path(__file__).resolve().parents[3]
OPERATOR_SETTINGS_FILE = (
    PROJECT_ROOT / "files" / "runtime" / "operator_settings.json"
)

# 当前方案2默认净烟气 SO2 控制目标。操作员未设置覆盖值时使用该值。
DEFAULT_OUTLET_SO2_TARGET = 8.0

_LOCK = threading.RLock()
_CACHE: Dict[str, Any] = {}
_CACHE_MTIME_NS: Optional[int] = None


def _load_file_unlocked() -> Dict[str, Any]:
    global _CACHE, _CACHE_MTIME_NS
    path = OPERATOR_SETTINGS_FILE
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        _CACHE = {}
        _CACHE_MTIME_NS = None
        return {}

    if _CACHE_MTIME_NS == mtime:
        return copy.deepcopy(_CACHE)

    try:
        with path.open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
        data = dict(raw) if isinstance(raw, Mapping) else {}
    except Exception:
        # 配置损坏时不让控制系统崩溃；退回内部默认配置。
        data = {}
    _CACHE = data
    _CACHE_MTIME_NS = mtime
    return copy.deepcopy(data)


def load_operator_overrides() -> Dict[str, Any]:
    with _LOCK:
        return _load_file_unlocked()


def _write_file_unlocked(data: Mapping[str, Any]) -> None:
    global _CACHE, _CACHE_MTIME_NS
    path = OPERATOR_SETTINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = dict(data)

    if not normalized:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        _CACHE = {}
        _CACHE_MTIME_NS = None
        return

    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(normalized, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temp), str(path))
    _CACHE = copy.deepcopy(normalized)
    try:
        _CACHE_MTIME_NS = path.stat().st_mtime_ns
    except OSError:
        _CACHE_MTIME_NS = None


def _scheme2_so2_control_config() -> Dict[str, Any]:
    scheme2 = PLANT_CONFIG.get("scheme2", {})
    if not isinstance(scheme2, Mapping):
        return {}
    values = scheme2.get("so2_control", {})
    return dict(values) if isinstance(values, Mapping) else {}


def _default_so2_target() -> float:
    values = _scheme2_so2_control_config()
    return float(values.get("default_target", DEFAULT_OUTLET_SO2_TARGET))


def so2_target_allowed_range() -> Tuple[float, float]:
    values = _scheme2_so2_control_config().get("allowed_target_range")
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        raise ValueError(
            "PLANT_CONFIG.scheme2.so2_control.allowed_target_range 必须配置为 [min, max]"
        )
    low = float(values[0])
    high = float(values[1])
    if not low < high:
        raise ValueError(
            "PLANT_CONFIG.scheme2.so2_control.allowed_target_range 必须满足 min < max"
        )
    return low, high


def operator_so2_target_override() -> Optional[float]:
    value = load_operator_overrides().get("outlet_so2_target")
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def effective_so2_target() -> float:
    override = operator_so2_target_override()
    return _default_so2_target() if override is None else float(override)


def so2_target_source() -> str:
    return "现场设置" if operator_so2_target_override() is not None else "默认配置"


def set_operator_so2_target(value: float) -> float:
    value = float(value)
    lo, hi = so2_target_allowed_range()
    if not lo <= value <= hi:
        raise ValueError(
            "目标净烟气 SO2 必须位于 %.1f～%.1f mg/Nm³" % (lo, hi)
        )
    with _LOCK:
        data = _load_file_unlocked()
        data["outlet_so2_target"] = value
        _write_file_unlocked(data)
    return value


def reset_operator_so2_target() -> None:
    with _LOCK:
        data = _load_file_unlocked()
        data.pop("outlet_so2_target", None)
        _write_file_unlocked(data)


def _tower_by_id(tower_id: str, plant_config: Mapping[str, Any] = PLANT_CONFIG) -> Dict[str, Any]:
    wanted = str(tower_id)
    for tower in plant_config.get("towers", []) or []:
        if str(tower.get("tower_id")) == wanted:
            return dict(tower)
    raise KeyError("未知吸收塔: %s" % wanted)


def default_ph_safe_range(tower_id: str) -> Tuple[float, float]:
    tower = _tower_by_id(tower_id)
    values = tower.get("ph_safe_range", [5.6, 6.8])
    return float(values[0]), float(values[1])


def operator_ph_safe_range_override(tower_id: str) -> Optional[Tuple[float, float]]:
    ranges = load_operator_overrides().get("ph_safe_ranges")
    if not isinstance(ranges, Mapping):
        return None
    values = ranges.get(str(tower_id))
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        return None
    try:
        return float(values[0]), float(values[1])
    except (TypeError, ValueError, OverflowError):
        return None


def effective_ph_safe_range(
    tower_id: str,
    default_range: Optional[Any] = None,
) -> Tuple[float, float]:
    override = operator_ph_safe_range_override(tower_id)
    if override is not None:
        return override
    if isinstance(default_range, (list, tuple)) and len(default_range) == 2:
        return float(default_range[0]), float(default_range[1])
    return default_ph_safe_range(tower_id)


def ph_safe_range_source(tower_id: str) -> str:
    return "现场设置" if operator_ph_safe_range_override(tower_id) is not None else "默认配置"


def set_operator_ph_safe_range(tower_id: str, low: float, high: float) -> Tuple[float, float]:
    # 先确认 tower_id 确实属于当前 plant_config。
    _tower_by_id(tower_id)
    low = float(low)
    high = float(high)
    if not (0.0 < low < high < 14.0):
        raise ValueError("pH 安全范围必须满足 0 < 下限 < 上限 < 14")
    with _LOCK:
        data = _load_file_unlocked()
        ranges = data.get("ph_safe_ranges")
        ranges = dict(ranges) if isinstance(ranges, Mapping) else {}
        ranges[str(tower_id)] = [low, high]
        data["ph_safe_ranges"] = ranges
        _write_file_unlocked(data)
    return low, high


def reset_operator_ph_safe_range(tower_id: str) -> None:
    with _LOCK:
        data = _load_file_unlocked()
        ranges = data.get("ph_safe_ranges")
        if isinstance(ranges, Mapping):
            ranges = dict(ranges)
            ranges.pop(str(tower_id), None)
            if ranges:
                data["ph_safe_ranges"] = ranges
            else:
                data.pop("ph_safe_ranges", None)
        _write_file_unlocked(data)


def effective_plant_config(
    plant_config: Mapping[str, Any] = PLANT_CONFIG,
) -> Dict[str, Any]:
    """返回应用操作员 pH 覆盖后的 plant_config 深拷贝。"""
    result = copy.deepcopy(dict(plant_config))
    for tower in result.get("towers", []) or []:
        tower_id = str(tower.get("tower_id", ""))
        if not tower_id:
            continue
        low, high = effective_ph_safe_range(
            tower_id,
            tower.get("ph_safe_range"),
        )
        tower["ph_safe_range"] = [low, high]
    return result


def settings_snapshot() -> Dict[str, Any]:
    """GUI 使用的只读摘要，不暴露内部测点字段。"""
    towers = []
    for tower in PLANT_CONFIG.get("towers", []) or []:
        if not tower.get("enabled", True):
            continue
        tower_id = str(tower.get("tower_id", ""))
        low, high = effective_ph_safe_range(tower_id, tower.get("ph_safe_range"))
        towers.append(
            {
                "tower_id": tower_id,
                "display_name": str(tower.get("display_name") or "吸收塔"),
                "ph_low": low,
                "ph_high": high,
                "ph_source": ph_safe_range_source(tower_id),
            }
        )
    lo, hi = so2_target_allowed_range()
    return {
        "outlet_so2_target": effective_so2_target(),
        "outlet_so2_target_source": so2_target_source(),
        "outlet_so2_allowed_range": [lo, hi],
        "towers": towers,
        "settings_file": str(OPERATOR_SETTINGS_FILE),
    }
