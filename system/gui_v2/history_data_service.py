from __future__ import annotations

import datetime as dt
import json
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import pandas as pd
from sqlalchemy import create_engine

from system.base.config.SysConfig import config
from system.model.config.database_schema import table_exists
from system.model.config.plant_config import PLANT_CONFIG, enabled_towers
from system.model.config.process4map_config import PROCESS4MAP_CONFIG
from system.model.config.standard_fields import TARGET_SO2_COLUMN


MAX_DISPLAY_POINTS = 2500
_NICE_BUCKET_SECONDS = (30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400)


def _quote_identifier(name: str) -> str:
    return '"%s"' % str(name).replace('"', '""')


def _as_datetime(value: Any) -> Optional[dt.datetime]:
    try:
        timestamp = pd.to_datetime(value)
    except Exception:
        return None
    if pd.isna(timestamp):
        return None
    if hasattr(timestamp, "to_pydatetime"):
        return timestamp.to_pydatetime()
    return timestamp


def _as_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _as_bool(value: Any) -> Optional[bool]:
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


def _as_reason_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
        except Exception:
            decoded = None
        if isinstance(decoded, list):
            return [str(item) for item in decoded if str(item).strip()]
        return [item.strip() for item in text.split(",") if item.strip()]
    return [str(value)]


def _month_starts(start: dt.datetime, end: dt.datetime) -> Iterable[dt.datetime]:
    cursor = dt.datetime(start.year, start.month, 1)
    final = dt.datetime(end.year, end.month, 1)
    while cursor <= final:
        yield cursor
        if cursor.month == 12:
            cursor = dt.datetime(cursor.year + 1, 1, 1)
        else:
            cursor = dt.datetime(cursor.year, cursor.month + 1, 1)


def _monthly_name(prefix: str, month_start: dt.datetime) -> str:
    return f"{prefix}{month_start.year}_{month_start.month}"


def configured_history_process_fields() -> Dict[str, Any]:
    """返回历史过程曲线所需字段和 GUI 元信息。"""
    process_columns: List[str] = ["yyq_SO2", "jyq_SO2", TARGET_SO2_COLUMN]
    supply_series: List[Dict[str, str]] = []

    towers = enabled_towers(PLANT_CONFIG)
    if towers:
        tower = towers[0]
        ph_column = str(tower.get("ph_column") or "").strip()
        if ph_column:
            process_columns.append(ph_column)

        for flow in tower.get("supply_flows", []) or []:
            column = str(flow.get("column") or "").strip()
            if not column:
                continue
            process_columns.append(column)
            supply_series.append({
                "column": column,
                "name": str(flow.get("display_name") or column),
                "unit": str(flow.get("unit") or ""),
                "side": "right",
            })

        for valve in tower.get("valves", []) or []:
            column = str(valve.get("column") or "").strip()
            if not column:
                continue
            process_columns.append(column)
            supply_series.append({
                "column": column,
                "name": str(valve.get("display_name") or column),
                "unit": "%",
                "side": "right",
            })

        for pump in tower.get("monitor_supply_pumps", []) or []:
            column = str(pump.get("value_column") or "").strip()
            if not column:
                continue
            process_columns.append(column)
            supply_series.append({
                "column": column,
                "name": str(pump.get("display_name") or column),
                "unit": str(pump.get("unit") or ""),
                "side": "right",
            })

        ph_meta = {
            "column": ph_column,
            "name": "浆液 pH",
            "unit": "",
            "side": "left",
        } if ph_column else None
    else:
        ph_meta = None

    return {
        "columns": tuple(dict.fromkeys(column for column in process_columns if column)),
        "ph_series": ph_meta,
        "supply_series": supply_series,
    }


class HistoryDataService:
    """历史趋势只读数据服务。

    PostgreSQL 是唯一历史事实源；本服务不读取 GLOBAL_DATA，也不与 P4PC 的实时写库
    队列共享线程。历史空窗只解释为“无历史数据/未记录”，不能仅凭空窗认定机组停机。
    """

    MODEL_EVENT_COLUMNS = (
        "date",
        "condition_label",
        "stable_condition_label",
        "condition_switch_state",
        "fast_change_active",
        "fast_change_mode",
        "fast_change_recovery_active",
        "slurry_policy_decision_id",
        "slurry_policy_control_mode",
        "slurry_policy_effective_target",
        "slurry_policy_commanded_target",
        TARGET_SO2_COLUMN,
        "slurry_policy_experience_source",
        "slurry_policy_action_direction",
        "slurry_policy_action_magnitude",
        "slurry_policy_recommended_valve_deltas",
        "slurry_policy_decision_status",
        "slurry_policy_reason_codes",
    )

    def __init__(self, db_url: Optional[str] = None) -> None:
        self.db_url = str(db_url or config["dbconnetion"])
        self.engine = create_engine(
            self.db_url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=1,
        )
        persistence = PROCESS4MAP_CONFIG.persistence
        self.filter_prefix = str(persistence.filter_table_prefix)
        self.model_prefix = str(persistence.model_result_table_prefix)
        self.process_meta = configured_history_process_fields()

        expected = float(PROCESS4MAP_CONFIG.runtime.snapshot_interval_seconds)
        self.expected_interval_seconds = max(1.0, expected)
        # 当前30秒入库时，连续缺失超过3分钟才认为存在明确历史空窗；避免偶发漏点切碎曲线。
        self.gap_threshold_seconds = max(180.0, self.expected_interval_seconds * 6.0)

    def close(self) -> None:
        try:
            self.engine.dispose()
        except Exception:
            pass

    def query(self, start: dt.datetime, end: dt.datetime) -> Dict[str, Any]:
        if end <= start:
            raise ValueError("历史查询结束时间必须晚于开始时间")

        process_fields = ("date", *self.process_meta["columns"])
        raw_process = self._query_monthly(
            self.filter_prefix,
            process_fields,
            start,
            end,
        )
        gaps = self._detect_gaps(raw_process, start, end)
        raw_process_count = int(len(raw_process))
        process = self._prepare_process_frame(raw_process, start, end, gaps)

        model_rows = self._query_monthly(
            self.model_prefix,
            self.MODEL_EVENT_COLUMNS,
            start,
            end,
        )
        events = self._extract_events(model_rows)

        return {
            "start": start,
            "end": end,
            "process": process,
            "events": events,
            "gaps": gaps,
            "process_meta": self.process_meta,
            "raw_process_point_count": raw_process_count,
            "process_point_count": int(len(process)),
            "event_count": int(len(events)),
            "gap_count": int(len(gaps)),
            "gap_duration_seconds": float(sum(item["duration_seconds"] for item in gaps)),
            "gap_threshold_seconds": self.gap_threshold_seconds,
        }

    def _table_columns(self, table_name: str) -> set[str]:
        rows = self.engine.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            """,
            (table_name,),
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _query_monthly(
        self,
        prefix: str,
        requested_columns: Sequence[str],
        start: dt.datetime,
        end: dt.datetime,
    ) -> pd.DataFrame:
        frames: List[pd.DataFrame] = []
        all_columns = tuple(dict.fromkeys(str(item) for item in requested_columns if item))

        for month_start in _month_starts(start, end):
            table_name = _monthly_name(prefix, month_start)
            if not table_exists(self.engine, table_name):
                continue
            existing = self._table_columns(table_name)
            selected = [column for column in all_columns if column in existing]
            if "date" not in selected:
                continue

            sql = (
                "SELECT %s FROM %s WHERE %s >= %%s AND %s <= %%s ORDER BY %s"
                % (
                    ", ".join(_quote_identifier(column) for column in selected),
                    _quote_identifier(table_name),
                    _quote_identifier("date"),
                    _quote_identifier("date"),
                    _quote_identifier("date"),
                )
            )
            result = self.engine.execute(sql, (start, end))
            rows = result.fetchall()
            if not rows:
                continue
            frame = pd.DataFrame(rows, columns=list(result.keys()))
            for missing in all_columns:
                if missing not in frame.columns:
                    frame[missing] = None
            frames.append(frame[list(all_columns)])

        if not frames:
            return pd.DataFrame(columns=list(all_columns))

        combined = pd.concat(frames, ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
        combined = combined.dropna(subset=["date"])
        combined = combined.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        return combined.reset_index(drop=True)

    def _detect_gaps(
        self,
        frame: pd.DataFrame,
        start: dt.datetime,
        end: dt.datetime,
    ) -> List[Dict[str, Any]]:
        """识别明确的数据空窗，但不把空窗自动解释为停机。"""
        if frame.empty or "date" not in frame.columns:
            duration = max(0.0, (end - start).total_seconds())
            if duration <= self.gap_threshold_seconds:
                return []
            return [{
                "start": start,
                "end": end,
                "duration_seconds": duration,
                "kind": "missing",
                "label": "无历史数据 / 未记录",
            }]

        stamps = [
            item for item in (_as_datetime(value) for value in frame["date"].tolist())
            if item is not None
        ]
        if not stamps:
            return []
        stamps.sort()

        expected = dt.timedelta(seconds=self.expected_interval_seconds)
        gaps: List[Dict[str, Any]] = []

        def add_gap(gap_start: dt.datetime, gap_end: dt.datetime) -> None:
            duration = (gap_end - gap_start).total_seconds()
            if duration <= self.gap_threshold_seconds:
                return
            gaps.append({
                "start": gap_start,
                "end": gap_end,
                "duration_seconds": duration,
                "kind": "missing",
                "label": "无历史数据 / 未记录",
            })

        # 查询窗口开头/结尾同样可能没有记录。仅超过阈值才标成空窗。
        add_gap(start, stamps[0] - expected)
        for previous, current in zip(stamps[:-1], stamps[1:]):
            if (current - previous).total_seconds() <= self.gap_threshold_seconds:
                continue
            gap_start = previous + expected
            gap_end = current - expected
            if gap_end <= gap_start:
                gap_start = previous
                gap_end = current
            add_gap(gap_start, gap_end)
        add_gap(stamps[-1] + expected, end)
        return gaps

    @staticmethod
    def _choose_bucket_seconds(start: dt.datetime, end: dt.datetime) -> int:
        span_seconds = max(1.0, (end - start).total_seconds())
        ideal = max(30.0, span_seconds / float(MAX_DISPLAY_POINTS))
        for candidate in _NICE_BUCKET_SECONDS:
            if candidate >= ideal:
                return candidate
        return int(math.ceil(ideal / 3600.0) * 3600)

    @staticmethod
    def _timestamp_inside_gap(value: Any, gaps: Sequence[Mapping[str, Any]]) -> bool:
        timestamp = _as_datetime(value)
        if timestamp is None:
            return False
        for gap in gaps:
            gap_start = _as_datetime(gap.get("start"))
            gap_end = _as_datetime(gap.get("end"))
            if gap_start is not None and gap_end is not None and gap_start <= timestamp <= gap_end:
                return True
        return False

    def _prepare_process_frame(
        self,
        frame: pd.DataFrame,
        start: dt.datetime,
        end: dt.datetime,
        gaps: Sequence[Mapping[str, Any]],
    ) -> pd.DataFrame:
        if frame.empty:
            return frame

        frame = frame.copy()
        for column in frame.columns:
            if column == "date":
                continue
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

        if len(frame) <= MAX_DISPLAY_POINTS:
            return frame.reset_index(drop=True)

        bucket = self._choose_bucket_seconds(start, end)
        indexed = frame.set_index("date")
        aggregations = {
            column: ("last" if column == TARGET_SO2_COLUMN else "median")
            for column in frame.columns
            if column != "date"
        }
        reduced = indexed.resample(f"{bucket}s").agg(aggregations)

        # 目标值只允许跨很短的偶发空点补齐，绝不跨长时间未记录区间延伸。
        if TARGET_SO2_COLUMN in reduced.columns:
            short_fill_seconds = min(self.gap_threshold_seconds / 2.0, 120.0)
            fill_limit = max(1, int(short_fill_seconds // max(1, bucket)))
            reduced[TARGET_SO2_COLUMN] = reduced[TARGET_SO2_COLUMN].ffill(limit=fill_limit)

        reduced = reduced.reset_index()
        if gaps:
            mask = reduced["date"].map(lambda value: self._timestamp_inside_gap(value, gaps))
            value_columns = [column for column in reduced.columns if column != "date"]
            reduced.loc[mask, value_columns] = float("nan")

        reduced = reduced.dropna(how="all", subset=[column for column in reduced.columns if column != "date"])
        return reduced.reset_index(drop=True)

    @staticmethod
    def _row_value(row: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            value = row.get(key)
            if value is not None and not (isinstance(value, float) and math.isnan(value)):
                return value
        return None

    def _extract_events(self, frame: pd.DataFrame) -> List[Dict[str, Any]]:
        if frame.empty:
            return []

        rows = frame.sort_values("date").to_dict("records")
        events: List[Dict[str, Any]] = []
        previous_timestamp: Optional[dt.datetime] = None
        previous_condition: Optional[str] = None
        previous_fast: Optional[bool] = None
        previous_target: Optional[float] = None
        previous_status: Optional[str] = None
        last_action_signature: Optional[tuple[str, str]] = None
        last_action_time: Optional[dt.datetime] = None

        for row in rows:
            timestamp = _as_datetime(row.get("date"))
            if timestamp is None:
                continue

            # 跨明确的数据缺口后，不沿用缺口前状态，避免把重启后的第一条记录误判为切换事件。
            if (
                previous_timestamp is not None
                and (timestamp - previous_timestamp).total_seconds() > self.gap_threshold_seconds
            ):
                previous_condition = None
                previous_fast = None
                previous_target = None
                previous_status = None
                last_action_signature = None
                last_action_time = None
            previous_timestamp = timestamp

            reasons = _as_reason_list(row.get("slurry_policy_reason_codes"))
            condition_raw = self._row_value(row, "stable_condition_label", "condition_label")
            condition = str(condition_raw) if condition_raw not in (None, "") else None

            if previous_condition is not None and condition and condition != previous_condition:
                events.append({
                    "time": timestamp,
                    "type": "condition",
                    "title": f"工况 {previous_condition} → {condition}",
                    "condition": condition,
                    "reason_codes": reasons,
                })
            if condition:
                previous_condition = condition

            fast = _as_bool(row.get("fast_change_active"))
            if previous_fast is not None and fast is not None and fast != previous_fast:
                events.append({
                    "time": timestamp,
                    "type": "fast" if fast else "recovery",
                    "title": "进入快速扰动" if fast else "退出快速扰动",
                    "condition": condition,
                    "reason_codes": reasons,
                })
            if fast is not None:
                previous_fast = fast

            target = _as_float(self._row_value(
                row,
                "slurry_policy_effective_target",
                "slurry_policy_commanded_target",
                TARGET_SO2_COLUMN,
            ))
            if previous_target is not None and target is not None and abs(target - previous_target) > 1e-9:
                events.append({
                    "time": timestamp,
                    "type": "target",
                    "title": f"目标 SO₂ {previous_target:.1f} → {target:.1f}",
                    "condition": condition,
                    "reason_codes": reasons,
                })
            if target is not None:
                previous_target = target

            status = str(row.get("slurry_policy_decision_status") or "").upper()
            mode = str(row.get("slurry_policy_control_mode") or "").upper()
            if status == "BLOCKED" and previous_status != "BLOCKED":
                events.append({
                    "time": timestamp,
                    "type": "blocked",
                    "title": "控制阻断",
                    "condition": condition,
                    "status": status,
                    "mode": mode,
                    "reason_codes": reasons,
                })
            previous_status = status or previous_status

            direction = str(row.get("slurry_policy_action_direction") or "").upper()
            magnitude = str(row.get("slurry_policy_action_magnitude") or "").upper()
            if status in {"RECOMMENDED", "ACTION_RECOMMENDED"} and direction in {"INCREASE", "DECREASE"}:
                signature = (direction, magnitude)
                should_add = signature != last_action_signature
                if last_action_time is not None:
                    should_add = should_add or (timestamp - last_action_time).total_seconds() >= 300.0
                else:
                    should_add = True
                if should_add:
                    events.append({
                        "time": timestamp,
                        "type": "action",
                        "title": "增加供浆" if direction == "INCREASE" else "减少供浆",
                        "condition": condition,
                        "direction": direction,
                        "magnitude": magnitude,
                        "source": str(row.get("slurry_policy_experience_source") or ""),
                        "status": status,
                        "mode": mode,
                        "reason_codes": reasons,
                    })
                    last_action_signature = signature
                    last_action_time = timestamp

        events.sort(key=lambda item: item["time"])
        return events
