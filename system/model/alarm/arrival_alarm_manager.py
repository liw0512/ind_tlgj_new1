"""按 GLOBAL_DATA 最新帧到达变化计算实时数据年龄。

现场 DCS/PLC 时间戳可能和上位机有时钟偏差；报警中的“数据超时”应判断数据是否持续
到达，而不是简单用业务 ``date`` 与本机当前时间相减。本类只调整用于超时判断的虚拟
时间戳，不修改 GLOBAL_DATA 原始数据。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Tuple

from .alarm_manager import AlarmManager, _latest_raw


def _frame_marker(raw: Mapping[str, Any]) -> Tuple[Any, ...]:
    """构造轻量帧指纹；优先时间/序号，并用关键数值兜底。"""
    keys = (
        "date",
        "realtime_seq",
        "seq",
        "yyq_SO2",
        "jyq_SO2",
        "xstjy_PH",
        "xst_FMKD",
        "xstshsjy_LL",
        "jym",
    )
    return tuple(str(raw.get(key, "")) for key in keys)


class ArrivalAwareAlarmManager(AlarmManager):
    def __init__(self) -> None:
        super().__init__()
        self._arrival_marker: Optional[Tuple[Any, ...]] = None
        self._last_arrival_monotonic: Optional[float] = None

    def evaluate_global_data(
        self,
        global_data: Mapping[str, Any],
        *,
        now: Optional[datetime] = None,
        now_monotonic: Optional[float] = None,
    ):
        now = now or datetime.now()
        now_monotonic = time.monotonic() if now_monotonic is None else float(now_monotonic)
        raw = _latest_raw(global_data)

        if raw:
            marker = _frame_marker(raw)
            if self._arrival_marker != marker or self._last_arrival_monotonic is None:
                self._arrival_marker = marker
                self._last_arrival_monotonic = now_monotonic

        if raw and self._last_arrival_monotonic is not None:
            age_seconds = max(0.0, now_monotonic - self._last_arrival_monotonic)
            proxy_raw = dict(raw)
            # 仅给 AlarmManager 的超时计算提供本地到达年龄；不回写原始 GLOBAL_DATA。
            proxy_raw["date"] = (now - timedelta(seconds=age_seconds)).isoformat(sep=" ")
            proxy = {
                "data": [proxy_raw],
                "map_control": global_data.get("map_control"),
                "connection_status": global_data.get("connection_status"),
            }
            result = super().evaluate_global_data(
                proxy,
                now=now,
                now_monotonic=now_monotonic,
            )
            result["source_realtime_timestamp"] = raw.get("date")
            return result

        return super().evaluate_global_data(
            global_data,
            now=now,
            now_monotonic=now_monotonic,
        )
