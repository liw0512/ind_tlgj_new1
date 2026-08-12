from __future__ import annotations

import datetime
import threading
import time
import traceback
from typing import Dict, Tuple

from sqlalchemy import create_engine

from system.base.LogUntil import setup_log
from system.base.config.SysConfig import config
from system.model.config.database_schema import (
    ensure_filter_table,
    ensure_model_result_table,
    latest_monthly_table,
)
from system.model.config.process4map_config import PROCESS4MAP_CONFIG

logging = setup_log("wc")


class DataHandler:
    """当前数据库历史数据读取器。

    只认识两类正式月表：t_data1_filter_rt_* 与 t_model_result_*。
    旧 t_data1_rt_*、cluster_label、推荐泵/推荐 pH 等历史 schema 已移除。
    """

    # 兼容旧页面 chart key，同时全部映射到当前真实数据库字段。
    BASE_SERIES: Dict[str, Tuple[str, str]] = {
        "yyq_so2": ("yyq_SO2", "原烟气SO2"),
        "jyq_so2": ("jyq_SO2", "净烟气SO2"),
        "yyq_o2": ("yyq_O2", "原烟气O2"),
        "yyq_ll": ("yyq_LL", "原烟气流量"),
        "jyq_ll": ("jyq_LL", "净烟气流量"),
        "sgjy_md": ("xstshsjy_MD", "石灰石浆液密度"),
        "xst_yw": ("xst_YW", "一级塔液位"),
        "sgjy_ph1": ("xstjy_PH", "一级塔pH"),
        "jyxhb_dl1": ("xstjyxhb_ADL", "循环泵A电流"),
        "jyxhb_dl2": ("xstjyxhb_BDL", "循环泵B电流"),
        "jyxhb_dl3": ("xstjyxhb_CDL", "循环泵C电流"),
        "jyxhb_dl4": ("xstjyxhb_DDL", "循环泵D电流"),
        "xstgjb_adl": ("xstgjb_ADL", "供浆泵A电流"),
        "xstgjb_bdl": ("xstgjb_BDL", "供浆泵B电流"),
        "xst_fmkd1": ("xst_FMKD1", "供浆阀1开度"),
        "xst_fmkd2": ("xst_FMKD2", "供浆阀2开度"),
        "liquid_gas_ratio": ("liquid_gas_ratio", "液气比"),
    }

    MODEL_SERIES: Dict[str, Tuple[str, str]] = {
        "condition_label": ("condition_label", "工况标签"),
        "stable_condition_label": ("stable_condition_label", "稳定工况标签"),
        "action_family": ("slurry_policy_action_family", "供浆动作族"),
        "action_direction": ("slurry_policy_action_direction", "动作方向"),
        "action_magnitude": ("slurry_policy_action_magnitude", "动作强度"),
        "decision_status": ("slurry_policy_decision_status", "决策状态"),
        "effective_target": ("slurry_policy_effective_target", "有效SO2目标"),
    }

    def __init__(self, GLOBAL_DATA):
        self.GLOBAL_DATA = GLOBAL_DATA
        self.engine = create_engine(config["dbconnetion"])
        self.lock = threading.Lock()
        self.filter_table_name = ""
        self.contro_table_name = ""
        self.send_obj = {"chart": [], "data": {}}
        self.mark = {
            "start_time": (
                datetime.datetime.now() - datetime.timedelta(hours=config.get("search_time", 1))
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "args": ["yyq_so2", "jyq_so2", "condition_label"],
            "is_send": False,
            "update_end_time": 1,
        }
        self.getNewDataTableName()
        self.table_update()

    def getNewDataTableName(self):
        self.filter_table_name = latest_monthly_table(
            self.engine, PROCESS4MAP_CONFIG.persistence.filter_table_prefix
        ) or ""
        self.contro_table_name = latest_monthly_table(
            self.engine, PROCESS4MAP_CONFIG.persistence.model_result_table_prefix
        ) or ""
        return self.filter_table_name, self.contro_table_name

    def table_update(self):
        # 与 P4PC 共用同一 schema，调用是幂等的，不再由 DataHandler 定义第二套 CREATE TABLE。
        self.filter_table_name = ensure_filter_table(
            self.engine, PROCESS4MAP_CONFIG.persistence.filter_table_prefix
        )
        self.contro_table_name = ensure_model_result_table(
            self.engine, PROCESS4MAP_CONFIG.persistence.model_result_table_prefix
        )

    @staticmethod
    def _parse_time(value, fallback):
        try:
            return datetime.datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return fallback

    def _query_series(self, table_name: str, column: str, start_time, end_time):
        if not table_name:
            return []
        safe_column = str(column).replace('"', '""')
        sql = (
            f'SELECT "date", "{safe_column}" AS value FROM "{table_name}" '
            'WHERE "date" BETWEEN %s AND %s ORDER BY "date"'
        )
        try:
            rows = self.engine.execute(sql, (start_time, end_time)).fetchall()
            return [
                {
                    "date": row[0].strftime("%Y-%m-%d %H:%M:%S") if hasattr(row[0], "strftime") else str(row[0]),
                    "value": row[1],
                }
                for row in rows
            ]
        except Exception as exc:
            logging.warning("历史序列读取失败 table=%s column=%s: %s", table_name, column, exc)
            return []

    def get_send_data(self):
        now = datetime.datetime.now()
        with self.lock:
            start = self._parse_time(self.mark.get("start_time"), now - datetime.timedelta(hours=1))
            end = self._parse_time(self.mark.get("end_time"), now)
            args = list(self.mark.get("args") or [])

        self.getNewDataTableName()
        charts = []
        for key in args:
            key = str(key)
            if key in self.BASE_SERIES:
                column, title = self.BASE_SERIES[key]
                table = self.filter_table_name
            elif key in self.MODEL_SERIES:
                column, title = self.MODEL_SERIES[key]
                table = self.contro_table_name
            else:
                logging.info("DataHandler 已忽略旧/未知图表字段: %s", key)
                continue
            points = self._query_series(table, column, start, end)
            charts.append({
                "name": key,
                "title": title,
                "step": config.get("rtstep", 1),
                "data": [point["value"] for point in points],
                "time": [point["date"] for point in points],
                "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
            })
        self.send_obj["chart"] = charts
        return self.send_obj

    def miniotor(self):
        while True:
            try:
                self.getNewDataTableName()
            except Exception as exc:
                logging.warning("DataHandler 表名刷新失败: %s", exc)
            time.sleep(60)

    def timing_clean_data(self):
        # 新版图表直接按需查数据库，不再维护几十个内存 map，因此无需逐 map 清理。
        return None

    def start(self):
        logging.info("start datahandler (filter/model tables only)")
        threading.Thread(target=self.miniotor, daemon=True).start()
        while True:
            try:
                should_send = False
                with self.lock:
                    if self.mark.get("is_send"):
                        self.mark["is_send"] = False
                        should_send = True
                if should_send:
                    result = self.get_send_data()
                    if result.get("chart"):
                        self.GLOBAL_DATA["chart"] = result["chart"]
                time.sleep(0.5)
            except Exception as exc:
                traceback.print_exc()
                logging.error("DataHandler.start 异常: %s", exc)
                time.sleep(1)
