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
    latest_monthly_table,
    monthly_table_name,
)
from system.model.config.mfac_database_schema import ensure_mfac_model_result_table
from system.model.config.process4map_config import PROCESS4MAP_CONFIG

logging = setup_log("wc")


class DataHandler:
    """当前数据库历史数据读取器。

    正式月表只有 t_data1_filter_rt_* 与 t_model_result_*。第二模块新数据优先
    查询 canonical ``mfac_*`` 字段；旧 ``slurry_policy_*`` 仅用于历史行兼容。
    """

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
        "jyxhb_dl5": ("xstjyxhb_EDL", "循环泵D电流"),
        "xstgjb_adl": ("xstgjb_ADL", "供浆泵A电流"),
        "xstgjb_bdl": ("xstgjb_BDL", "供浆泵B电流"),
        "xst_fmkd1": ("xst_FMKD", "供浆阀开度"),
        "liquid_gas_ratio": ("liquid_gas_ratio", "液气比"),
    }

    # chart key is stable. Tuple = canonical column, title, legacy fallback.
    MODEL_SERIES: Dict[str, Tuple[str, str, str]] = {
        "condition_label": ("condition_label", "工况标签", ""),
        "stable_condition_label": ("stable_condition_label", "稳定工况标签", ""),
        "action_family": (
            "mfac_action_family", "MFAC供浆动作族", "slurry_policy_action_family"
        ),
        "action_direction": (
            "mfac_action_direction", "MFAC动作方向", "slurry_policy_action_direction"
        ),
        "action_magnitude": (
            "mfac_action_magnitude", "MFAC动作强度", "slurry_policy_action_magnitude"
        ),
        "decision_status": (
            "mfac_decision_status", "MFAC决策状态", "slurry_policy_decision_status"
        ),
        "effective_target": (
            "mfac_effective_target", "有效SO2目标", "slurry_policy_effective_target"
        ),
        "algorithm_target_supply_flow": (
            "mfac_algorithm_target_supply_flow", "MFAC目标供浆量", ""
        ),
        "qbase_effective": (
            "mfac_qbase_effective", "MFAC动态Qbase", ""
        ),
        "residual_mfac_hold": (
            "mfac_residual_mfac_hold", "MFAC保持修正量", ""
        ),
    }

    def __init__(self, GLOBAL_DATA):
        self.GLOBAL_DATA = GLOBAL_DATA
        self.engine = create_engine(config["dbconnetion"])
        self.lock = threading.Lock()
        persistence = PROCESS4MAP_CONFIG.persistence
        self.filter_table_name = monthly_table_name(persistence.filter_table_prefix)
        self.contro_table_name = monthly_table_name(
            persistence.model_result_table_prefix
        )
        self._schema_ready = False
        self.send_obj = {"chart": [], "data": {}}
        self.mark = {
            "start_time": (
                datetime.datetime.now()
                - datetime.timedelta(hours=config.get("search_time", 1))
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "args": ["yyq_so2", "jyq_so2", "condition_label"],
            "is_send": False,
            "update_end_time": 1,
        }
        self.table_update()

    def getNewDataTableName(self):
        persistence = PROCESS4MAP_CONFIG.persistence
        try:
            self.filter_table_name = latest_monthly_table(
                self.engine, persistence.filter_table_prefix
            ) or monthly_table_name(persistence.filter_table_prefix)
            self.contro_table_name = latest_monthly_table(
                self.engine, persistence.model_result_table_prefix
            ) or monthly_table_name(persistence.model_result_table_prefix)
            return self.filter_table_name, self.contro_table_name
        except Exception as exc:
            logging.warning("刷新历史月表名称失败，保留当前表名: %s", exc)
            return self.filter_table_name, self.contro_table_name

    def table_update(self):
        persistence = PROCESS4MAP_CONFIG.persistence
        try:
            self.filter_table_name = ensure_filter_table(
                self.engine, persistence.filter_table_prefix
            )
            self.contro_table_name = ensure_mfac_model_result_table(
                self.engine, persistence.model_result_table_prefix
            )
            self._schema_ready = True
            return True
        except Exception as exc:
            self._schema_ready = False
            logging.warning(
                "历史数据处理器启动建表失败，页面继续并等待后续刷新: %s",
                exc,
            )
            return False

    @staticmethod
    def _parse_time(value, fallback):
        try:
            return datetime.datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return fallback

    @staticmethod
    def _safe_column(column: str) -> str:
        return str(column).replace('"', '""')

    def _query_series(self, table_name: str, column: str, start_time, end_time):
        if not table_name:
            return []
        safe_column = self._safe_column(column)
        sql = (
            f'SELECT "date", "{safe_column}" AS value FROM "{table_name}" '
            'WHERE "date" BETWEEN %s AND %s ORDER BY "date"'
        )
        rows = self.engine.execute(sql, (start_time, end_time)).fetchall()
        return [
            {
                "date": (
                    row[0].strftime("%Y-%m-%d %H:%M:%S")
                    if hasattr(row[0], "strftime")
                    else str(row[0])
                ),
                "value": row[1],
            }
            for row in rows
        ]

    def _query_model_series(
        self,
        canonical: str,
        legacy: str,
        start_time,
        end_time,
    ):
        if not legacy:
            try:
                return self._query_series(
                    self.contro_table_name, canonical, start_time, end_time
                )
            except Exception as exc:
                logging.warning(
                    "历史MFAC序列读取失败 column=%s: %s", canonical, exc
                )
                return []

        # Both columns are present after ensure_mfac_model_result_table().
        # COALESCE keeps old rows readable after canonical columns are added:
        # new rows use mfac_*, migration-era rows fall back to slurry_policy_*.
        canonical_safe = self._safe_column(canonical)
        legacy_safe = self._safe_column(legacy)
        sql = (
            f'SELECT "date", COALESCE("{canonical_safe}", "{legacy_safe}") AS value '
            f'FROM "{self.contro_table_name}" '
            'WHERE "date" BETWEEN %s AND %s ORDER BY "date"'
        )
        try:
            rows = self.engine.execute(sql, (start_time, end_time)).fetchall()
            return [
                {
                    "date": (
                        row[0].strftime("%Y-%m-%d %H:%M:%S")
                        if hasattr(row[0], "strftime")
                        else str(row[0])
                    ),
                    "value": row[1],
                }
                for row in rows
            ]
        except Exception as exc:
            logging.warning(
                "历史MFAC兼容序列读取失败 canonical=%s legacy=%s: %s",
                canonical,
                legacy,
                exc,
            )
            return []

    def get_send_data(self):
        now = datetime.datetime.now()
        with self.lock:
            start = self._parse_time(
                self.mark.get("start_time"),
                now - datetime.timedelta(hours=1),
            )
            end = self._parse_time(self.mark.get("end_time"), now)
            args = list(self.mark.get("args") or [])

        if not getattr(self, "_schema_ready", False):
            self.table_update()
        else:
            self.getNewDataTableName()
        charts = []
        for key in args:
            key = str(key)
            if key in self.BASE_SERIES:
                column, title = self.BASE_SERIES[key]
                try:
                    points = self._query_series(
                        self.filter_table_name, column, start, end
                    )
                except Exception as exc:
                    logging.warning(
                        "历史基础序列读取失败 column=%s: %s", column, exc
                    )
                    points = []
            elif key in self.MODEL_SERIES:
                column, title, legacy = self.MODEL_SERIES[key]
                points = self._query_model_series(column, legacy, start, end)
            else:
                logging.info("DataHandler 已忽略未知图表字段: %s", key)
                continue
            charts.append(
                {
                    "name": key,
                    "title": title,
                    "step": config.get("rtstep", 1),
                    "data": [point["value"] for point in points],
                    "time": [point["date"] for point in points],
                    "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
                    "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        self.send_obj["chart"] = charts
        return self.send_obj

    def miniotor(self):
        while True:
            try:
                if not getattr(self, "_schema_ready", False):
                    self.table_update()
                else:
                    self.getNewDataTableName()
            except Exception as exc:
                logging.warning("DataHandler 表名刷新失败: %s", exc)
            time.sleep(60)

    def timing_clean_data(self):
        return None

    def start(self):
        logging.info("start datahandler (filter/MFAC model tables only)")
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
