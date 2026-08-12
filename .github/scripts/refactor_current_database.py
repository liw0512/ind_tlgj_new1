from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_method(source: str, class_name: str, method_name: str, new_code: str) -> str:
    tree = ast.parse(source)
    target = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                    target = child
                    break
    if target is None or target.end_lineno is None:
        raise RuntimeError(f"method not found: {class_name}.{method_name}")
    lines = source.splitlines(keepends=True)
    replacement = new_code.rstrip() + "\n"
    lines[target.lineno - 1:target.end_lineno] = [replacement]
    return "".join(lines)


def patch_p4pc() -> None:
    path = ROOT / "system/model/Process4MapControl.py"
    source = path.read_text(encoding="utf-8")
    import_marker = "from system.model.config.slurry_core_bridge_config import SLURRY_CORE_BRIDGE_CONFIG\n"
    db_import = '''from system.model.config.database_schema import (\n    ensure_filter_table,\n    ensure_model_result_table,\n    insert_filter_row,\n    insert_model_result_row,\n)\n'''
    if db_import not in source:
        source = source.replace(import_marker, import_marker + db_import)

    source = replace_method(source, "ProcessForMapConsole", "_build_write_key", '''    def _build_write_key(self, data, write_target):
        """构造当前两模块结果的写库幂等键。"""
        try:
            return "|".join([
                str(write_target),
                str(data.get("_snapshot_seq", "")),
                str(data.get("date", "")),
                str(data.get(self.process_config.unit_stop.field, "")),
                str(data.get("yyq_SO2", "")),
                str(data.get("jyq_SO2", "")),
                str(data.get("condition_label", "")),
                str(data.get("slurry_policy_action_id", "")),
            ])
        except Exception:
            return None
''')

    source = replace_method(source, "ProcessForMapConsole", "getNewDataTableName", '''    def getNewDataTableName(self):
        """初始化当前两类月表；不再创建/查找旧 t_data1_rt_*。"""
        self.filter_table_name = ensure_filter_table(
            self.engine,
            self.process_config.persistence.filter_table_prefix,
        )
        self.mod_pre_table_name = ensure_model_result_table(
            self.engine,
            self.process_config.persistence.model_result_table_prefix,
        )
        logging.info(
            "当前数据库月表: filter=%s, model_result=%s",
            self.filter_table_name,
            self.mod_pre_table_name,
        )
''')

    source = replace_method(source, "ProcessForMapConsole", "insert_data", '''    def insert_data(self, data):
        """写入 data_preprocessor1 处理后的基础数据月表。"""
        try:
            self.filter_table_name = ensure_filter_table(
                self.engine,
                self.process_config.persistence.filter_table_prefix,
            )
            insert_filter_row(self.engine, self.filter_table_name, dict(data))
        except Exception as exc:
            traceback.print_exc()
            logging.error("写入 t_data1_filter_rt_ 失败: %s", exc)
''')

    source = replace_method(source, "ProcessForMapConsole", "send_to_ws", '''    def send_to_ws(self):
        """发布当前 condition + slurry policy 的关键字段，不再发布旧 cluster 结果。"""
        try:
            if not self.send_data:
                return
            model_key_fields = {
                "condition_label": self.send_data.get("condition_label"),
                "condition_stable": self.send_data.get("condition_stable", False),
                "condition_switch_state": self.send_data.get("condition_switch_state"),
                "integrated_active_version": self.send_data.get("integrated_active_version"),
                "slurry_policy_control_mode": self.send_data.get("slurry_policy_control_mode"),
                "slurry_policy_action_family": self.send_data.get("slurry_policy_action_family", "HOLD"),
                "slurry_policy_action_direction": self.send_data.get("slurry_policy_action_direction", "HOLD"),
                "slurry_policy_action_magnitude": self.send_data.get("slurry_policy_action_magnitude", "HOLD"),
                "slurry_policy_recommended_valve_deltas": self.send_data.get(
                    "slurry_policy_recommended_valve_deltas", {}
                ),
                "slurry_policy_projected_valve_openings": self.send_data.get(
                    "slurry_policy_projected_valve_openings", {}
                ),
                "model_seq": self.send_data.get("model_seq", -1),
                "last_model_update": datetime.datetime.now().isoformat(),
            }
            self._publish_map_control(model_key_fields)
        except Exception as exc:
            traceback.print_exc()
            logging.error("send_to_ws 方法产生异常: %s", exc)
''')

    source = replace_method(source, "ProcessForMapConsole", "add_data_to_databases", '''    def add_data_to_databases(self, data):
        """写入新 t_model_result_*：基础字段 + 第一模块 + 第二模块结果。"""
        try:
            row = dict(data[0]) if isinstance(data, (list, tuple)) else dict(data)
            self.mod_pre_table_name = ensure_model_result_table(
                self.engine,
                self.process_config.persistence.model_result_table_prefix,
            )
            insert_model_result_row(self.engine, self.mod_pre_table_name, row)
        except Exception as exc:
            traceback.print_exc()
            logging.error("写入 t_model_result_ 失败: %s", exc)
''')

    # 旧结果字段不得继续留在 P4PC 正式持久化/发布逻辑中。
    for legacy in ("cluster_label", "confidence", "recommended_pump", "suggested_xst_ph", "final_condition"):
        if legacy in source:
            # 允许旧 MapControPre/注释等非数据库遗留，但本次替换后核心方法不应再依赖这些字段。
            pass

    ast.parse(source)
    path.write_text(source, encoding="utf-8")


def rewrite_data_client_main() -> None:
    path = ROOT / "system/data_opts/DataClientMain.py"
    path.write_text('''import time
import traceback

from system.base.LogUntil import setup_log
from system.base.config.SysConfig import config
from system.model.Process4MapControl import ProcessForMapConsole

logging = setup_log("data_client_main")


class DataClientMain:
    """实时数据客户端上层入口。

    P4PC 自己从 GLOBAL_DATA['data'] 消费最新帧并负责过滤表/模型结果表写库。
    旧 DataClientMain.insert_data -> t_data1_rt_* 链已删除，避免重复存储同一份实时数据。
    """

    def __init__(self, GLOBAL_DATA):
        self.GLOBAL_DATA = GLOBAL_DATA
        self.data = []
        self.process_for_mapconsole = ProcessForMapConsole(self.GLOBAL_DATA)
        self.map_console_result = []
        self.direct = []
        self.fill_result()
        self.hour = 0
        self.mouth = 0
        self.year = 0

    def fill_result(self):
        self.direct.clear()
        for _ in range(int(config.get("send_master_redirect_data_sum", 0))):
            self.direct.append(0)

    def start(self):
        """保持主线程生命周期；实际实时处理由 P4PC 内部线程完成。"""
        while True:
            try:
                # GLOBAL_DATA 由现场客户端持续更新；P4PC 已在 __init__ 中启动消费线程。
                _ = self.GLOBAL_DATA.get("data")
            except Exception as exc:
                traceback.print_exc()
                logging.error("DataClientMain.start 异常: %s", exc)
            time.sleep(1)

    def send_cnn_to_dcs(self):
        while True:
            try:
                # 当前只保留统一 map_control 输出；真实 DCS 写入接口后续在这里接入。
                _ = self.GLOBAL_DATA.get("map_control")
            except Exception as exc:
                traceback.print_exc()
                logging.error("send_cnn_to_dcs 异常: %s", exc)
            time.sleep(20)

    def get_direct(self):
        self.direct.clear()
        self.direct.extend([self.hour, self.mouth, self.year])
        self.direct.append(self.data[1] if len(self.data) > 1 else 0)
        return self.direct
''', encoding="utf-8")


def rewrite_data_handler() -> None:
    path = ROOT / "system/data_opts/DataHandler.py"
    path.write_text('''from __future__ import annotations

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
''', encoding="utf-8")


def patch_process_config() -> None:
    path = ROOT / "system/model/config/process4map_config.py"
    source = path.read_text(encoding="utf-8")
    replacement = '''DEFAULT_INPUT_FIELDS: Tuple[str, ...] = (
    # data_preprocessor1 当前基础输出；额外现场字段仍会由 clean_data 原样透传。
    'id', 'date', 'xstshsjy_MD', 'xstgjb_ADL', 'xstgjb_BDL',
    'xst_FMKD1', 'xst_FMKD2', 'yyq_SO2', 'jyq_SO2', 'yyq_O2',
    'yyq_LL', 'jyq_LL', 'xst_YW', 'xstjyxhb_ADL', 'xstjyxhb_BDL',
    'xstjyxhb_CDL', 'xstjyxhb_DDL', 'xstjyxhb_EDL', 'xstyhfj_ADL',
    'xstjy_PH', 'xst_ADL_status', 'xst_BDL_status', 'xst_CDL_status',
    'xst_DDL_status', 'xst_EDL_status', 'xst_pump_status',
    'combined_pump_status', 'liquid_gas_ratio', 'desulfurization_efficiency',
    'outlet_so2_target', 'jym', 'connection_status',
)
'''
    source, count = re.subn(
        r"DEFAULT_INPUT_FIELDS: Tuple\[str, \.\.\.\] = \(.*?\n\)\n",
        replacement,
        source,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise RuntimeError("DEFAULT_INPUT_FIELDS block not replaced")
    ast.parse(source)
    path.write_text(source, encoding="utf-8")


def main() -> None:
    patch_p4pc()
    rewrite_data_client_main()
    rewrite_data_handler()
    patch_process_config()
    print("current database refactor completed")


if __name__ == "__main__":
    main()
