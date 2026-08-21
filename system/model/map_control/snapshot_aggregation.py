"""10 秒模型快照的聚合规则。

P4PC 的实时预处理链仍按 1 秒推进；本模块只负责把最近若干个已预处理的
1 秒帧聚合成模型判断/历史写库使用的低频快照。

默认语义：
- 连续、可转成有限浮点数的过程量：对窗口内有效值取均值；
- 状态、标识、控制目标、内部字段和布尔量：严格保留窗口末帧值；
- 字符串等非数值字段：自然保留窗口末帧值。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence


def is_latest_value_field(field_name: Any, value: Any, config: Any) -> bool:
    """返回字段是否必须使用窗口末帧，而不能参与数值均值。"""
    name = str(field_name)
    if isinstance(value, bool):
        return True
    if name in config.latest_value_fields:
        return True
    if any(name.startswith(prefix) for prefix in config.latest_value_prefixes):
        return True
    if any(name.endswith(suffix) for suffix in config.latest_value_suffixes):
        return True
    return False


def average_snapshot_window(
    snapshots: Sequence[Mapping[str, Any]],
    config: Any,
) -> Optional[Dict[str, Any]]:
    """按配置聚合窗口；连续量取均值，离散语义字段取末帧。"""
    if not snapshots:
        return None

    latest = dict(snapshots[-1])
    averaged = dict(latest)

    for field_name, latest_value in latest.items():
        if is_latest_value_field(field_name, latest_value, config):
            continue

        values: List[float] = []
        for frame in snapshots:
            value = frame.get(field_name)
            # bool 是 int 的子类，必须在 float() 之前单独排除。
            if isinstance(value, bool):
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(numeric):
                values.append(numeric)

        if values:
            averaged[field_name] = sum(values) / len(values)

    return averaged
