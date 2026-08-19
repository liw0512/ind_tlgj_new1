from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Mapping, Optional

import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG, enabled_towers
from system.model.config.standard_fields import TARGET_SO2_COLUMN

from .history_data_service import HistoryDataService


class ResponsiveHistoryDataService(HistoryDataService):
    """GUI V2 响应式历史数据服务。

    在 ``HistoryDataService`` 基础上补充两项 GUI 需要的能力：

    1. 根据 ``plant_config`` 动态加入循环泵历史字段；
    2. 为烟气趋势补齐“目标 SO2”历史曲线。

    过程测点仍以 ``t_data1_filter_rt_*`` 为事实源；目标值若过滤表已有记录则直接使用，
    若过滤表目标为空，则按同一时间段的 ``t_model_result_*`` 依次使用：

    ``slurry_policy_effective_target`` -> ``slurry_policy_commanded_target``
    -> ``outlet_so2_target``。

    目标值只允许在很短的模型记录间隔内向后匹配，不跨长时间数据缺口延伸，避免把
    停机、系统未运行或数据库缺失期间错误画成持续存在的目标线。
    """

    def __init__(self, db_url: Optional[str] = None) -> None:
        super().__init__(db_url=db_url)

        meta: Dict[str, Any] = dict(self.process_meta)
        columns: List[str] = list(meta.get("columns", ()) or ())
        circulation_series: List[Dict[str, str]] = []

        for tower in enabled_towers(PLANT_CONFIG):
            tower_name = str(tower.get("display_name") or tower.get("tower_id") or "吸收塔")
            for pump in tower.get("circulation_pumps", []) or []:
                column = str(pump.get("value_column") or "").strip()
                if not column:
                    continue
                columns.append(column)
                circulation_series.append(
                    {
                        "column": column,
                        "name": str(pump.get("display_name") or column),
                        "unit": str(pump.get("unit") or ""),
                        "tower": tower_name,
                        "side": "left",
                    }
                )

        meta["columns"] = tuple(dict.fromkeys(columns))
        meta["circulation_series"] = circulation_series
        self.process_meta = meta

    @staticmethod
    def _model_target_frame(model_rows: pd.DataFrame) -> pd.DataFrame:
        """把模型结果中的多个目标字段规整成 date + outlet_so2_target。"""
        if model_rows is None or model_rows.empty or "date" not in model_rows.columns:
            return pd.DataFrame(columns=["date", TARGET_SO2_COLUMN])

        frame = model_rows.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date")
        if frame.empty:
            return pd.DataFrame(columns=["date", TARGET_SO2_COLUMN])

        preferred_columns = (
            "slurry_policy_effective_target",
            "slurry_policy_commanded_target",
            TARGET_SO2_COLUMN,
        )
        target = pd.Series(float("nan"), index=frame.index, dtype="float64")
        for column in preferred_columns:
            if column not in frame.columns:
                continue
            values = pd.to_numeric(frame[column], errors="coerce")
            target = target.combine_first(values)

        result = pd.DataFrame({
            "date": frame["date"],
            TARGET_SO2_COLUMN: target,
        })
        result = result.dropna(subset=[TARGET_SO2_COLUMN])
        result = result.drop_duplicates(subset=["date"], keep="last")
        return result.reset_index(drop=True)

    def _overlay_model_target(
        self,
        process: pd.DataFrame,
        model_rows: pd.DataFrame,
    ) -> pd.DataFrame:
        """过滤表目标为空时，从临近模型结果补目标值。

        使用 merge_asof 的短容差匹配，不做无限 ffill。当前默认模型/数据库快照约30秒，
        容差取 ``max(120秒, 4个快照周期)``，足以容忍少量漏点，但不会跨长缺口补线。
        """
        if process is None or process.empty or "date" not in process.columns:
            return process

        result = process.copy()
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
        if TARGET_SO2_COLUMN not in result.columns:
            result[TARGET_SO2_COLUMN] = float("nan")
        else:
            result[TARGET_SO2_COLUMN] = pd.to_numeric(
                result[TARGET_SO2_COLUMN], errors="coerce"
            )

        targets = self._model_target_frame(model_rows)
        if targets.empty:
            return result

        tolerance_seconds = max(120.0, float(self.expected_interval_seconds) * 4.0)
        left = result.sort_values("date").reset_index().rename(
            columns={TARGET_SO2_COLUMN: "_filter_target"}
        )
        right = targets.rename(columns={TARGET_SO2_COLUMN: "_model_target"})

        merged = pd.merge_asof(
            left,
            right,
            on="date",
            direction="backward",
            tolerance=pd.Timedelta(seconds=tolerance_seconds),
        )
        merged[TARGET_SO2_COLUMN] = merged["_filter_target"].combine_first(
            merged["_model_target"]
        )
        merged = merged.sort_values("index").set_index("index")
        merged.index.name = None
        merged = merged.drop(columns=["_filter_target", "_model_target"])
        return merged[result.columns].reset_index(drop=True)

    def query(self, start: dt.datetime, end: dt.datetime) -> Dict[str, Any]:
        """一次完成过程曲线、目标曲线、事件与缺口查询，避免重复访问模型月表。"""
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

        # 同一次模型表查询同时服务于：目标曲线补齐 + 历史控制事件提取。
        model_rows = self._query_monthly(
            self.model_prefix,
            self.MODEL_EVENT_COLUMNS,
            start,
            end,
        )
        raw_process = self._overlay_model_target(raw_process, model_rows)
        process = self._prepare_process_frame(raw_process, start, end, gaps)
        events = self._extract_events(model_rows)

        target_point_count = 0
        if TARGET_SO2_COLUMN in process.columns:
            target_point_count = int(
                pd.to_numeric(process[TARGET_SO2_COLUMN], errors="coerce").notna().sum()
            )

        return {
            "start": start,
            "end": end,
            "process": process,
            "events": events,
            "gaps": gaps,
            "process_meta": self.process_meta,
            "raw_process_point_count": raw_process_count,
            "process_point_count": int(len(process)),
            "target_point_count": target_point_count,
            "event_count": int(len(events)),
            "gap_count": int(len(gaps)),
            "gap_duration_seconds": float(sum(item["duration_seconds"] for item in gaps)),
            "gap_threshold_seconds": self.gap_threshold_seconds,
        }
