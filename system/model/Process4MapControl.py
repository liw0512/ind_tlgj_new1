import datetime
import json
import math
import os
import socket
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures.thread import ThreadPoolExecutor
from pathlib import Path
from queue import Queue, Full, Empty
import subprocess
import pandas as pd
import psycopg2.extras
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from system.base.LogUntil import setup_log
from system.base.config.SysConfig import config
from system.model.config.process4map_config import PROCESS4MAP_CONFIG
from system.model.config.slurry_core_bridge_config import SLURRY_CORE_BRIDGE_CONFIG
import logging
import shutil
from system.model.map_control.MapControPre import MapControPre  # 推荐泵以及PH建议（兼容保留，正式在线入口已替换）
from system.model.map_control.data_preprocessor1 import DataPreprocessor
from system.model.map_control.SO2_processor import SO2Processor
from system.model.map_control.yhfj_and_jzgj_model import ProcessControl as YHFJProcessControl
from system.model.map_control.tower_power_consumption import TowerPowerCalculator
from system.model.map_control.cost_calculator import CostCalculator
from system.model.map_control.StatCalc import PHStatCalc
logging = setup_log("process_for_mapconsole")
psycopg2.extras.register_uuid()

from collections import deque
from datetime import timedelta


class DataValidator:
    """数据有效性验证器"""

    def __init__(self, validation_config=None):
        self.config = validation_config or PROCESS4MAP_CONFIG.data_validation
        self.buffer_size = int(self.config.buffer_size)
        self.data_buffer = deque(maxlen=self.buffer_size)
        self.calibration_detection_window = int(self.config.calibration_detection_window)
        self.calibration_buffer = deque(maxlen=self.calibration_detection_window)
        self.last_calibration_time = None
        self.calibration_cooldown = float(self.config.calibration_cooldown_seconds)
        self.ph_change_threshold = float(self.config.ph_change_threshold)
        self.jyq_so2_change_threshold = float(self.config.jyq_so2_change_threshold)
        self.jyq_so2_value_threshold = float(self.config.jyq_so2_value_threshold)
        self.yyq_so2_change_threshold = float(self.config.yyq_so2_change_threshold)
        self.calibration_code = int(self.config.calibration_code)

    def add_data(self, data):
        current_time = time.time()
        data_with_time = {'timestamp': current_time, 'data': data.copy()}
        self.data_buffer.append(data_with_time)
        self.calibration_buffer.append(data_with_time)

    def calculate_change_rate(self, field_name, time_window=None):
        if time_window is None:
            time_window = float(self.config.change_rate_window_seconds)
        if len(self.data_buffer) < 2:
            return 0
        current_time = time.time()
        current_value = self.data_buffer[-1]['data'].get(field_name, 0)
        for i in range(len(self.data_buffer) - 2, -1, -1):
            if current_time - self.data_buffer[i]['timestamp'] >= time_window:
                old_value = self.data_buffer[i]['data'].get(field_name, 0)
                return abs(current_value - old_value)
        old_value = self.data_buffer[0]['data'].get(field_name, 0)
        return abs(current_value - old_value)

    def detect_calibration_pattern(self):
        if len(self.calibration_buffer) < int(self.config.calibration_min_samples):
            return False
        current_time = time.time()
        if self.last_calibration_time and current_time - self.last_calibration_time < self.calibration_cooldown:
            return True
        recent_data = [
            item for item in self.calibration_buffer
            if current_time - item['timestamp'] <= self.calibration_detection_window
        ]
        if len(recent_data) < int(self.config.calibration_min_samples):
            return False
        calibration_events = 0
        for i in range(1, len(recent_data)):
            prev_so2 = recent_data[i - 1]['data'].get('jyq_SO2', 0)
            curr_so2 = recent_data[i]['data'].get('jyq_SO2', 0)
            if abs(curr_so2 - prev_so2) > self.jyq_so2_change_threshold:
                calibration_events += 1
        if calibration_events >= int(self.config.calibration_event_count_threshold):
            self.last_calibration_time = current_time
            return True
        return False

    def validate_data(self, data):
        self.add_data(data)
        try:
            jym_value = int(data.get('jym', self.config.default_jym))
        except Exception:
            jym_value = int(self.config.default_jym)
        if jym_value == self.calibration_code:
            return False, f"检测到校验码jym={jym_value}，数据无效"
        jyq_so2_value = data.get('jyq_SO2', 0)
        if jyq_so2_value > self.jyq_so2_value_threshold:
            return False, f"净烟气SO2均值过大: {jyq_so2_value} > {self.jyq_so2_value_threshold}"
        return True, "数据有效"

    def get_status(self):
        return {
            'buffer_size': len(self.data_buffer),
            'calibration_buffer_size': len(self.calibration_buffer),
            'last_calibration_time': self.last_calibration_time,
            'in_calibration_cooldown': (
                self.last_calibration_time and
                time.time() - self.last_calibration_time < self.calibration_cooldown
            ) if self.last_calibration_time else False
        }


class ProcessForMapConsole:
    process_config = PROCESS4MAP_CONFIG
    titles = list(PROCESS4MAP_CONFIG.input_fields)
    if PROCESS4MAP_CONFIG.unit_stop.field not in titles:
        titles.append(PROCESS4MAP_CONFIG.unit_stop.field)
    limit = {key: value.copy() for key, value in PROCESS4MAP_CONFIG.limits.items()}

    class SystemState:
        DATA_COLLECTION = "data_collection"
        MODEL_TRAINING = "model_training"
        NORMAL_OPERATION = "normal_operation"

    def __init__(self, GLOBAL_DATA):
        self.process_config = PROCESS4MAP_CONFIG
        self.slurry_core_config = dict(SLURRY_CORE_BRIDGE_CONFIG)
        self._slurry_pipeline = None
        self._slurry_pipeline_error = None
        self._slurry_pipeline_lock = threading.RLock()

        self.is_training = False
        self.system_state = self.process_config.runtime.initial_system_state
        self.model_training_completed = False
        self.is_initial_training = True
        self.last_training_time = None

        self.GLOBAL_DATA = GLOBAL_DATA
        self.training_lock = threading.Lock()
        self.limit = pd.DataFrame.from_dict(self.limit)
        self.engine = create_engine(config["dbconnetion"])
        self.queen = Queue(maxsize=int(self.process_config.runtime.insert_queue_size))
        self.queue_keys = []
        self.df = None
        self.count = 0
        self.data_preprocessor = DataPreprocessor()
        self.filter_write_pool = ThreadPoolExecutor(
            max_workers=int(self.process_config.runtime.filter_writer_workers),
            thread_name_prefix='filter_writer'
        )
        self.model_result_pool = ThreadPoolExecutor(
            max_workers=int(self.process_config.runtime.model_writer_workers),
            thread_name_prefix='model_writer'
        )
        self.filter_data = pd.DataFrame(columns=self.titles)
        self.filter_table_name = (
            self.process_config.persistence.filter_table_prefix
            + str(datetime.datetime.now().year) + "_" + str(datetime.datetime.now().month)
        )
        self.so2_processor = SO2Processor()
        self.i = 0
        self.tower_power_calculator = TowerPowerCalculator()
        self.cost_calculator = CostCalculator()
        self.stat_calc = PHStatCalc(comm_T=float(self.process_config.runtime.stat_calc_comm_t))
        self.data_validator = DataValidator(self.process_config.data_validation)
        self._unit_stop_condition_since_monotonic = None
        self._unit_stop_elapsed_seconds = 0.0

        self.getNewDataTableName()
        self.result = None
        self.send_data = None
        self.pump_name_def = {}
        self.mod_pre_table_name = (
            self.process_config.persistence.model_result_table_prefix
            + str(datetime.datetime.now().year) + "_" + str(datetime.datetime.now().month)
        )
        self.get_pump_name()
        if "date" not in self.titles:
            self.titles = ["date"] + self.titles
        self.tempdf = pd.DataFrame(columns=self.titles)

        self.training_event = threading.Event()
        # 这是唯一的在线模型判定周期。原默认值为30秒，但可直接在
        # PROCESS4MAP_CONFIG.runtime.snapshot_interval_seconds 中配置为任意正数。
        self.snapshot_interval = float(self.process_config.runtime.snapshot_interval_seconds)
        self.map_control_lock = threading.Lock()
        self.snapshot_lock = threading.Lock()
        self._latest_processed_snapshot = None
        self._latest_processed_snapshot_seq = 0
        self._last_snapshot_emit_ts = 0.0
        self._last_filter_emitted_seq = -1
        self._last_model_emitted_seq = -1
        self._last_realtime_published_seq = -1
        self._last_consumed_frame_key = None
        self._last_filter_written_key = None
        self._last_model_written_key = None
        self.maintenance_interval = float(self.process_config.runtime.maintenance_interval_seconds)
        self.global_data_maxlen = int(self.process_config.runtime.global_data_maxlen)

        self.yhfj_process_control = YHFJProcessControl()
        self.data_queue = Queue(maxsize=int(self.process_config.runtime.data_queue_size))
        self.db_queue = Queue(maxsize=int(self.process_config.runtime.db_queue_size))

        # 若已有合法 active_version.json，优先恢复正式在线版本；失败则仍按原状态机采集/训练。
        self._restore_active_runtime_if_available()

        self.state_check_thread = threading.Thread(target=self.check_system_state)
        self.state_check_thread.start()
        self.data_consumer_thread = threading.Thread(target=self.consume_data)
        self.data_consumer_thread.start()
        self.processing_loop_thread = threading.Thread(target=self.processing_loop, daemon=True)
        self.processing_loop_thread.start()
        self.db_writer_thread = threading.Thread(target=self._db_writer_loop, daemon=True)
        self.db_writer_thread.start()
        self.snapshot_scheduler_thread = threading.Thread(target=self._snapshot_scheduler_loop, daemon=True)
        self.snapshot_scheduler_thread.start()
        self.maintenance_thread = threading.Thread(target=self._maintenance_loop, daemon=True)
        self.maintenance_thread.start()
        self.training_thread = threading.Thread(target=self.training_worker)
        self.training_thread.daemon = True
        self.training_thread.start()
        self.training_start_time = None

    def check_training_status(self):
        status = {
            'is_training': self.is_training,
            'system_state': self.system_state,
            'training_event_is_set': self.training_event.is_set(),
            'training_thread_alive': self.training_thread.is_alive() if hasattr(self, 'training_thread') else False
        }
        logging.info(f"当前训练状态: {status}")
        return status

    def _maintenance_loop(self):
        while True:
            try:
                data_store = self.GLOBAL_DATA.get("data")
                if isinstance(data_store, list):
                    if len(data_store) > self.global_data_maxlen:
                        self.GLOBAL_DATA["data"] = deque(
                            data_store[-self.global_data_maxlen:], maxlen=self.global_data_maxlen
                        )
                elif isinstance(data_store, deque):
                    if data_store.maxlen is None:
                        self.GLOBAL_DATA["data"] = deque(
                            list(data_store)[-self.global_data_maxlen:], maxlen=self.global_data_maxlen
                        )
                else:
                    if data_store is None:
                        self.GLOBAL_DATA["data"] = deque(maxlen=self.global_data_maxlen)
                    else:
                        try:
                            self.GLOBAL_DATA["data"] = deque(
                                list(data_store)[-self.global_data_maxlen:], maxlen=self.global_data_maxlen
                            )
                        except Exception:
                            self.GLOBAL_DATA["data"] = deque(maxlen=self.global_data_maxlen)
                data_q_size = self.data_queue.qsize() if hasattr(self, "data_queue") else -1
                db_q_size = self.db_queue.qsize() if hasattr(self, "db_queue") else -1
                logging.info(
                    f"maintenance tick data_queue={data_q_size} db_queue={db_q_size} "
                    f"snapshot_seq={self._latest_processed_snapshot_seq}"
                )
            except Exception as e:
                logging.error(f"_maintenance_loop 异常: {str(e)}")
                traceback.print_exc()
            time.sleep(self.maintenance_interval)

    def training_worker(self):
        while True:
            try:
                logging.info("训练工作线程等待训练事件...")
                self.training_event.wait()
                logging.info(
                    f"收到训练事件，当前状态：is_training={self.is_training}, "
                    f"system_state={self.system_state}"
                )
                if self.is_training:
                    logging.info("已有训练任务在进行，清除事件并继续等待")
                    self.training_event.clear()
                    continue
                self.is_training = True
                try:
                    self._do_training()
                except Exception as e:
                    logging.error(f"训练过程发生错误: {str(e)}")
                    traceback.print_exc()
                finally:
                    self.is_training = False
                    self.training_event.clear()
                    logging.info("训练任务处理完成")
            except Exception as e:
                logging.error(f"训练工作线程发生错误: {str(e)}")
                traceback.print_exc()
                time.sleep(float(self.process_config.runtime.training_worker_error_retry_seconds))

    @staticmethod
    def _coerce_connection_status(value):
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on", "connected", "normal"}:
                return True
            if normalized in {"false", "0", "no", "off", "disconnected", "failed"}:
                return False
        return bool(value)

    def _resolve_connection_status(self, latest_msg=None):
        global_status = self.GLOBAL_DATA.get("connection_status")
        if global_status is not None:
            return self._coerce_connection_status(global_status)
        if isinstance(latest_msg, dict) and latest_msg.get("connection_status") is not None:
            frame_status = self._coerce_connection_status(latest_msg.get("connection_status"))
            self.GLOBAL_DATA["connection_status"] = frame_status
            return frame_status
        return False

    def _is_unit_stopped(self, data, now_monotonic=None):
        stop_config = self.process_config.unit_stop
        if not stop_config.enabled:
            self._unit_stop_condition_since_monotonic = None
            self._unit_stop_elapsed_seconds = 0.0
            return False
        if now_monotonic is None:
            now_monotonic = time.monotonic()
        raw_value = data.get(stop_config.field) if isinstance(data, dict) else None
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = math.nan
        if not math.isfinite(value):
            if stop_config.invalid_value_resets_timer:
                self._unit_stop_condition_since_monotonic = None
                self._unit_stop_elapsed_seconds = 0.0
            return False
        comparisons = {
            'lt': lambda current, threshold: current < threshold,
            'le': lambda current, threshold: current <= threshold,
            'gt': lambda current, threshold: current > threshold,
            'ge': lambda current, threshold: current >= threshold,
            'eq': lambda current, threshold: current == threshold,
            'ne': lambda current, threshold: current != threshold,
        }
        comparator = comparisons.get(str(stop_config.comparison).lower())
        if comparator is None:
            logging.error('未知停机比较方式: %s', stop_config.comparison)
            return False
        condition_met = comparator(value, float(stop_config.threshold))
        if condition_met:
            if self._unit_stop_condition_since_monotonic is None:
                self._unit_stop_condition_since_monotonic = now_monotonic
            self._unit_stop_elapsed_seconds = max(
                0.0, now_monotonic - self._unit_stop_condition_since_monotonic
            )
            return self._unit_stop_elapsed_seconds >= float(stop_config.hold_seconds)
        self._unit_stop_condition_since_monotonic = None
        self._unit_stop_elapsed_seconds = 0.0
        return False

    def _is_unit_stopped_by_yyq_so2(self, data, now_monotonic=None):
        return self._is_unit_stopped(data, now_monotonic=now_monotonic)

    def _build_consume_frame_key(self, msg):
        try:
            return "|".join([
                str(msg.get("date", "")),
                str(msg.get(self.process_config.unit_stop.field, "")),
                str(msg.get("yyq_SO2", "")),
                str(msg.get("jyq_SO2", "")),
                str(msg.get("combined_pump_status", "")),
            ])
        except Exception:
            return None

    def consume_data(self):
        last_enqueue_time = 0.0
        last_valid_data_time = time.time()
        while True:
            current_time = time.time()
            data_store = self.GLOBAL_DATA.get("data")
            if (
                current_time - last_enqueue_time >= float(
                    self.process_config.runtime.consume_min_interval_seconds
                ) and data_store
            ):
                latest_msg = data_store[-1]
                connection_status = self._resolve_connection_status(latest_msg)
                frame_key = self._build_consume_frame_key(latest_msg)
                if frame_key is not None and frame_key == self._last_consumed_frame_key:
                    time.sleep(float(self.process_config.runtime.consume_duplicate_sleep_seconds))
                    continue
                msg = latest_msg.copy()
                if connection_status or (
                    current_time - last_valid_data_time
                    <= float(self.process_config.runtime.offline_grace_seconds)
                ):
                    if connection_status:
                        last_valid_data_time = current_time
                        map_control = self.GLOBAL_DATA.get("map_control")
                        if isinstance(map_control, dict):
                            map_control["offline_mode"] = False
                            map_control["data_expired"] = False
                    else:
                        if "map_control" in self.GLOBAL_DATA:
                            self.GLOBAL_DATA["map_control"]["offline_mode"] = True
                    if self.data_queue.full():
                        try:
                            self.data_queue.get_nowait()
                        except Empty:
                            pass
                    try:
                        self.data_queue.put_nowait({
                            "msg": msg,
                            "connection_status": connection_status,
                            "last_valid_data_time": last_valid_data_time,
                        })
                        self._last_consumed_frame_key = frame_key
                        last_enqueue_time = current_time
                    except Full:
                        last_enqueue_time = current_time
                else:
                    logging.error("通讯中断时间过长，暂停数据处理")
                    if "map_control" in self.GLOBAL_DATA:
                        self.GLOBAL_DATA["map_control"]["data_expired"] = True
                    last_enqueue_time = current_time
            else:
                time.sleep(float(self.process_config.runtime.consume_idle_sleep_seconds))

    def processing_loop(self):
        while True:
            try:
                item = self.data_queue.get(
                    timeout=float(self.process_config.runtime.processing_queue_timeout_seconds)
                )
            except Empty:
                continue
            except Exception:
                continue
            try:
                result = self.clean_data([item["msg"]])
                if result is not None:
                    self._put_db_queue_latest(result)
            except Exception as e:
                logging.error(f"processing_loop 处理帧异常: {str(e)}")
                traceback.print_exc()

    def _put_db_queue_latest(self, data):
        if data is None:
            return
        if self.db_queue.full():
            try:
                self.db_queue.get_nowait()
            except Empty:
                pass
        try:
            self.db_queue.put_nowait(data)
        except Full:
            logging.warning("db_queue put_nowait failed, dropped latest result")

    def _update_latest_snapshot(self, realtime_data):
        if realtime_data is None:
            return None
        with self.snapshot_lock:
            self._latest_processed_snapshot_seq += 1
            snapshot_seq = self._latest_processed_snapshot_seq
            snapshot = realtime_data.copy()
            snapshot["_snapshot_seq"] = snapshot_seq
            snapshot["_snapshot_time"] = datetime.datetime.now().isoformat()
            self._latest_processed_snapshot = snapshot
            return snapshot_seq

    def _snapshot_scheduler_loop(self):
        """按 runtime.snapshot_interval_seconds 触发快照写库与新核心在线推理。"""
        while True:
            try:
                now_ts = time.time()
                if now_ts - self._last_snapshot_emit_ts < self.snapshot_interval:
                    time.sleep(float(self.process_config.runtime.snapshot_poll_interval_seconds))
                    continue
                with self.snapshot_lock:
                    snapshot = (
                        self._latest_processed_snapshot.copy()
                        if self._latest_processed_snapshot else None
                    )
                    snapshot_seq = self._latest_processed_snapshot_seq
                if snapshot is None:
                    time.sleep(float(self.process_config.runtime.snapshot_poll_interval_seconds))
                    continue
                if (
                    snapshot_seq == self._last_filter_emitted_seq
                    and snapshot_seq == self._last_model_emitted_seq
                ):
                    self._last_snapshot_emit_ts = now_ts
                    time.sleep(float(self.process_config.runtime.snapshot_poll_interval_seconds))
                    continue
                self._last_snapshot_emit_ts = now_ts

                filter_payload = snapshot.copy()
                filter_payload["_write_target"] = self.process_config.persistence.filter_write_target
                filter_payload["_is_valid"] = True
                self._put_db_queue_latest(filter_payload)
                self._last_filter_emitted_seq = snapshot_seq

                if self._is_unit_stopped(snapshot):
                    self._last_model_emitted_seq = snapshot_seq
                    continue

                is_valid, validation_reason = self.data_validator.validate_data(snapshot)
                if not is_valid:
                    logging.info(
                        f"快照数据验证失败，跳过模型结果写库但继续推理: {validation_reason}"
                    )
                if self.system_state == self.SystemState.NORMAL_OPERATION:
                    model_payload = self.insert_Mod(snapshot.copy(), None, store_to_db=is_valid)
                else:
                    model_payload = snapshot.copy()
                    model_payload["_write_target"] = self.process_config.persistence.model_write_target
                    model_payload["_is_valid"] = is_valid
                    if not is_valid:
                        model_payload["_invalid_reason"] = validation_reason
                if model_payload is not None:
                    model_payload["_snapshot_seq"] = snapshot_seq
                    self._put_db_queue_latest(model_payload)
                    self._last_model_emitted_seq = snapshot_seq
            except Exception as e:
                logging.error(f"_snapshot_scheduler_loop 异常: {str(e)}")
                traceback.print_exc()
                time.sleep(float(self.process_config.runtime.snapshot_error_retry_seconds))

    def _build_write_key(self, data, write_target):
        try:
            return "|".join([
                str(write_target),
                str(data.get("_snapshot_seq", "")),
                str(data.get("date", "")),
                str(data.get(self.process_config.unit_stop.field, "")),
                str(data.get("yyq_SO2", "")),
                str(data.get("jyq_SO2", "")),
                str(data.get("combined_pump_status", "")),
                str(data.get("recommended_pump", "")),
            ])
        except Exception:
            return None

    def _db_writer_loop(self):
        """数据库结构暂不修改，仍沿用原过滤表/结果表写入逻辑。"""
        while True:
            try:
                data = self.db_queue.get()
                if data is None:
                    continue
                is_valid = data.pop('_is_valid', True)
                write_target = data.pop(
                    '_write_target', self.process_config.persistence.filter_write_target
                )
                invalid_reason = data.pop('_invalid_reason', None)
                if write_target == 'noop':
                    continue
                if not is_valid:
                    logging.info(
                        f"_db_writer_loop: 数据无效，跳过写库，原因: {invalid_reason}"
                    )
                    continue
                write_key = self._build_write_key(data, write_target)
                if write_target == self.process_config.persistence.model_write_target:
                    if write_key is not None and write_key == self._last_model_written_key:
                        continue
                    self._last_model_written_key = write_key
                    self.model_result_pool.submit(self.add_data_to_databases, [data])
                else:
                    if write_key is not None and write_key == self._last_filter_written_key:
                        continue
                    self._last_filter_written_key = write_key
                    self.filter_write_pool.submit(self.insert_data, data)
            except Exception as e:
                logging.error(f"_db_writer_loop 异常: {str(e)}")
                traceback.print_exc()

    def get_map_pre(self):
        """兼容旧外部调用；正式在线推理不再使用 MapControPre。"""
        if not hasattr(self, 'map_pre') or self.map_pre is None:
            self.map_pre = MapControPre()
        return self.map_pre

    def get_pump_name(self):
        result = self.engine.execute("select name,layer from t_pump_def order by layer asc").fetchall()
        for i in result:
            self.pump_name_def[str(i[1])] = i[0]

    def getNewDataTableName(self):
        result = self.engine.execute(
            "select tablename from pg_tables where schemaname ='public'"
        ).fetchall()
        djh = {"t_data1_filter_rt": []}
        for i in result:
            table_name = str(i[0])
            if table_name.startswith("t_data1_filter_rt_"):
                parts = table_name.split('_')
                if len(parts) == 6 and parts[4].isdigit() and parts[5].isdigit():
                    djh["t_data1_filter_rt"].append(table_name)
        def sort_by_year_month(table_name):
            parts = table_name.split('_')
            return int(parts[4]), int(parts[5])
        djh["t_data1_filter_rt"] = sorted(
            djh["t_data1_filter_rt"], key=sort_by_year_month, reverse=True
        )
        if djh["t_data1_filter_rt"]:
            self.filter_table_name = djh["t_data1_filter_rt"][0]
        else:
            now = datetime.datetime.now()
            self.filter_table_name = f"t_data1_filter_rt_{now.year}_{now.month}"
            self.engine.execute(
                f"""
                DROP TABLE IF EXISTS "public".{self.filter_table_name};
                CREATE TABLE "public".{self.filter_table_name} (
                    "id" uuid NOT NULL,
                    "date" timestamp(6) NOT NULL,
                    "xstshsjy_MD" float8,
                    "yyq_SO2" float8,
                    "jyq_SO2" float8,
                    "yyq_O2" float8,
                    "yyq_LL" float8,
                    "jyq_LL" float8,
                    "xst_YW" float8,
                    "xstjyxhb_ADL" float8,
                    "xstjyxhb_BDL" float8,
                    "xstjyxhb_CDL" float8,
                    "xstjyxhb_DDL" float8,
                    "xstjyxhb_EDL" float8,
                    "xstyhfj_ADL" float8,
                    "xstjy_PH" float8,
                    "xst_ADL_status" int,
                    "xst_BDL_status" int,
                    "xst_CDL_status" int,
                    "xst_DDL_status" int,
                    "xst_EDL_status" int,
                    "xst_pump_status" varchar(20),
                    "combined_pump_status" varchar(20),
                    "liquid_gas_ratio" float8,
                    "desulfurization_efficiency" float8
                );
                """
            )
            self.engine.execute(
                'ALTER TABLE "' + self.filter_table_name + '" ADD PRIMARY KEY ("id")'
            )
            index_name = "index_" + self.filter_table_name
            self.engine.execute(
                f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "public".{self.filter_table_name} '
                'USING btree ("date" "pg_catalog"."timestamp_ops" ASC NULLS LAST);'
            )
            self.engine.execute(
                "insert into t_table_name(id,table_name,table_alias) values (%s,%s,%s)",
                (
                    uuid.uuid4(), self.filter_table_name,
                    "数据过滤表_" + str(now.year) + "_" + str(now.month)
                )
            )

    def _project_root(self):
        return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

    def _resolve_training_path(self, path_value):
        if not path_value:
            return ''
        expanded = os.path.expandvars(os.path.expanduser(str(path_value)))
        if os.path.isabs(expanded):
            return os.path.normpath(expanded)
        return os.path.normpath(os.path.join(self._project_root(), expanded))

    def _core_path(self, key):
        return os.path.normpath(str(self.slurry_core_config[key]))

    def _active_version_file(self):
        return Path(self._core_path("active_version_file"))

    @staticmethod
    def _version_number(version):
        text = str(version).strip()
        if text.lower().startswith("v") and text[1:].isdigit():
            return int(text[1:])
        raise ValueError("版本必须是 v### 格式: %r" % version)

    def _next_version(self, version):
        return "v%03d" % (self._version_number(version) + 1)

    def _read_active_version(self):
        path = self._active_version_file()
        if not path.is_file():
            raise FileNotFoundError("增量训练前未找到 active_version.json: %s" % path)
        with path.open("r", encoding="utf-8") as stream:
            pointer = json.load(stream)
        version = str(
            pointer.get("integrated_version") or pointer.get("policy_version") or ""
        ).strip()
        self._version_number(version)
        return version

    def _parse_activation_time(self, value):
        if value in (None, ""):
            return None
        try:
            parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
        except Exception:
            return None

    def _restore_active_runtime_if_available(self):
        active_path = self._active_version_file()
        if not active_path.is_file():
            return False
        try:
            with active_path.open("r", encoding="utf-8") as stream:
                pointer = json.load(stream)
            version = str(
                pointer.get("integrated_version") or pointer.get("policy_version") or ""
            ).strip()
            self._version_number(version)
            if not self.reload_models():
                raise RuntimeError(self._slurry_pipeline_error or "集成在线模型加载失败")
            self.is_initial_training = False
            self.model_training_completed = True
            self.system_state = self.SystemState.NORMAL_OPERATION
            activated = self._parse_activation_time(pointer.get("activated_at"))
            self.last_training_time = activated or datetime.datetime.fromtimestamp(
                active_path.stat().st_mtime
            )
            logging.info("检测到已激活同版本模型 %s，恢复 NORMAL_OPERATION", version)
            return True
        except Exception as exc:
            logging.error("恢复 active_version.json 失败，继续原状态机: %s", exc)
            self._slurry_pipeline = None
            self._slurry_pipeline_error = str(exc)
            return False

    def _training_mode_settings(self, mode):
        cfg = self.process_config.training
        normalized = str(mode).strip().lower()
        if normalized == 'initial':
            settings = {
                'mode': 'initial',
                'source': str(cfg.initial_data_source).strip().lower(),
                'source_csv': cfg.initial_source_csv,
                'days': int(cfg.initial_training_days),
                'minimum_records': int(cfg.initial_minimum_records),
                'database_record_limit': int(cfg.initial_database_record_limit),
                'use_model_result_table': bool(cfg.initial_database_use_model_result_table),
                'work_csv': cfg.initial_work_csv,
            }
        elif normalized == 'incremental':
            settings = {
                'mode': 'incremental',
                'source': str(cfg.incremental_data_source).strip().lower(),
                'source_csv': cfg.incremental_source_csv,
                'days': int(cfg.incremental_training_days),
                'minimum_records': int(cfg.incremental_minimum_records),
                'database_record_limit': int(cfg.incremental_database_record_limit),
                'use_model_result_table': bool(cfg.incremental_database_use_model_result_table),
                'work_csv': cfg.incremental_work_csv,
            }
        else:
            raise ValueError(f'未知训练模式: {mode}')

        # 数据库模式的数据量随在线判定周期同步变化，不再把2880条/天写死为事实。
        if settings['source'] == 'database':
            interval = max(0.001, float(self.snapshot_interval))
            records_per_day = max(1, int(round(24 * 60 * 60 / interval)))
            target_count = max(1, settings['days'] * records_per_day)
            settings['database_record_limit'] = target_count
            settings['minimum_records'] = max(
                1,
                int(target_count * float(cfg.database_minimum_data_ratio))
            )
        return settings

    def _database_target_count(self, settings):
        configured_limit = int(settings.get('database_record_limit', 0))
        if configured_limit > 0:
            return configured_limit
        interval = max(0.001, float(self.snapshot_interval))
        records_per_day = max(1, int(round(24 * 60 * 60 / interval)))
        return max(1, int(settings['days']) * records_per_day)

    def _database_table_names(self, use_model_result_table=False):
        now = datetime.datetime.now()
        prefix = (
            self.process_config.persistence.model_result_table_prefix
            if use_model_result_table
            else self.process_config.persistence.filter_table_prefix
        )
        current = f'{prefix}{now.year}_{now.month}'
        if now.month == 1:
            previous = f'{prefix}{now.year - 1}_12'
        else:
            previous = f'{prefix}{now.year}_{now.month - 1}'
        return [current, previous]

    def _database_table_exists(self, table_name):
        sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            )
        """
        return bool(self.engine.execute(sql, (table_name,)).scalar())

    def _count_recent_database_records(self, settings):
        target_count = self._database_target_count(settings)
        available = 0
        for table_name in self._database_table_names(settings['use_model_result_table']):
            try:
                if not self._database_table_exists(table_name):
                    continue
                row = self.engine.execute(f'SELECT COUNT(*) FROM {table_name}').fetchone()
                available += int(row[0]) if row else 0
                if available >= target_count:
                    break
            except Exception as exc:
                logging.warning('统计训练表 %s 失败: %s', table_name, exc)
        return min(available, target_count), target_count

    def get_recent_days_data(
        self, day, use_model_result_table=False, record_limit=None, minimum_ratio=None
    ):
        try:
            settings = {
                'days': int(day),
                'database_record_limit': int(record_limit or 0),
                'use_model_result_table': bool(use_model_result_table),
            }
            target_count = self._database_target_count(settings)
            ratio = (
                float(self.process_config.training.database_minimum_data_ratio)
                if minimum_ratio is None else float(minimum_ratio)
            )
            minimum_required = max(1, int(target_count * ratio))
            frames = []
            remaining = target_count
            for table_name in self._database_table_names(use_model_result_table):
                if remaining <= 0:
                    break
                try:
                    if not self._database_table_exists(table_name):
                        continue
                    result = self.engine.execute(
                        f'SELECT * FROM {table_name} ORDER BY date DESC LIMIT {int(remaining)}'
                    )
                    rows = result.fetchall()
                    if rows:
                        frame = pd.DataFrame(rows, columns=result.keys())
                        frames.append(frame)
                        remaining -= len(frame)
                except Exception as exc:
                    logging.warning('读取训练数据表 %s 失败: %s', table_name, exc)
            if not frames:
                return None
            df = pd.concat(frames, ignore_index=True, sort=False)
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'], errors='coerce')
                df = df.sort_values('date').reset_index(drop=True)
            if len(df) > target_count:
                df = df.tail(target_count).reset_index(drop=True)
            if len(df) < minimum_required:
                logging.warning(
                    '数据库训练数据完整率不足: actual=%s, required=%s',
                    len(df), minimum_required
                )
            return df
        except Exception as exc:
            logging.error('获取训练数据时发生错误: %s', exc)
            traceback.print_exc()
            return None

    def _load_training_data(self, mode):
        settings = self._training_mode_settings(mode)
        source = settings['source']
        if source not in {'database', 'csv'}:
            raise ValueError(
                f"{mode} data_source={source!r} 无效，仅支持 'database' 或 'csv'"
            )
        if source == 'csv':
            source_path = self._resolve_training_path(settings['source_csv'])
            if not source_path or not os.path.isfile(source_path):
                raise FileNotFoundError(f'{mode} 训练 CSV 不存在: {source_path}')
            df = pd.read_csv(source_path)
            required = settings['minimum_records']
        else:
            target_count = self._database_target_count(settings)
            df = self.get_recent_days_data(
                day=settings['days'],
                use_model_result_table=settings['use_model_result_table'],
                record_limit=target_count,
            )
            required = settings['minimum_records']
        if df is None or len(df) < required:
            actual = 0 if df is None else len(df)
            raise RuntimeError(
                f'{mode} 训练数据不足: actual={actual}, required={required}, source={source}'
            )
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
            df = df.sort_values('date').reset_index(drop=True)
        return df, settings

    def _save_training_work_csv(self, df, settings):
        output_path = self._resolve_training_path(settings['work_csv'])
        if not output_path:
            raise ValueError(f"{settings['mode']} work_csv 未配置")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        df.to_csv(output_path, index=False)
        logging.info(
            '%s 训练工作 CSV 已保存: %s, records=%s',
            settings['mode'], output_path, len(df)
        )
        return output_path

    def check_data_accumulation(self, mode='initial'):
        try:
            settings = self._training_mode_settings(mode)
            if settings['source'] == 'csv':
                source_path = self._resolve_training_path(settings['source_csv'])
                if not source_path or not os.path.isfile(source_path):
                    return False
                count = len(pd.read_csv(source_path))
                required = settings['minimum_records']
            elif settings['source'] == 'database':
                count, target = self._count_recent_database_records(settings)
                required = settings['minimum_records']
            else:
                return False
            logging.info(
                '%s 训练数据检查: source=%s, actual=%s, required=%s, days=%s',
                mode, settings['source'], count, required, settings['days']
            )
            return count >= required
        except Exception as exc:
            logging.error('检查 %s 训练数据时发生错误: %s', mode, exc)
            return False

    def insert_data(self, data):
        """原数据库过滤表结构/insert 暂时保持不变。"""
        try:
            if not self.filter_table_name.endswith(
                str(datetime.datetime.now().year) + "_" + str(datetime.datetime.now().month)
            ):
                self.filter_table_name = (
                    self.process_config.persistence.filter_table_prefix
                    + str(datetime.datetime.now().year) + "_" + str(datetime.datetime.now().month)
                )
                self.engine.execute(
                    f"""
                    DROP TABLE IF EXISTS "public".{self.filter_table_name};
                    CREATE TABLE "public".{self.filter_table_name} (
                        "id" uuid NOT NULL,
                        "date" timestamp(6) NOT NULL,
                        "xstshsjy_MD" float8,
                        "yyq_SO2" float8,
                        "jyq_SO2" float8,
                        "yyq_O2" float8,
                        "yyq_LL" float8,
                        "jyq_LL" float8,
                        "xst_YW" float8,
                        "xstjyxhb_ADL" float8,
                        "xstjyxhb_BDL" float8,
                        "xstjyxhb_CDL" float8,
                        "xstjyxhb_DDL" float8,
                        "xstjyxhb_EDL" float8,
                        "xstyhfj_ADL" float8,
                        "xstjy_PH" float8,
                        "xst_ADL_status" int,
                        "xst_BDL_status" int,
                        "xst_CDL_status" int,
                        "xst_DDL_status" int,
                        "xst_EDL_status" int,
                        "xst_pump_status" varchar(20),
                        "combined_pump_status" varchar(20),
                        "liquid_gas_ratio" float8,
                        "desulfurization_efficiency" float8
                    );
                    """
                )
                self.engine.execute(
                    'ALTER TABLE "' + self.filter_table_name + '" ADD PRIMARY KEY ("id")'
                )
            self.data_cal_no_fentch(
                f"""
                insert into {self.filter_table_name} (
                    "id", "date", "xstshsjy_MD","yyq_SO2","jyq_SO2",
                    "yyq_O2","yyq_LL","jyq_LL","xst_YW",
                    "xstjyxhb_ADL", "xstjyxhb_BDL", "xstjyxhb_CDL", "xstjyxhb_DDL",
                    "xstjyxhb_EDL","xstyhfj_ADL","xstjy_PH",
                    "xst_ADL_status", "xst_BDL_status", "xst_CDL_status", "xst_DDL_status",
                    "xst_EDL_status", "xst_pump_status", "combined_pump_status",
                    "liquid_gas_ratio", "desulfurization_efficiency"
                ) values
                (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    uuid.uuid4(), data.get("date", pd.Timestamp.now()),
                    data.get("xstshsjy_MD", 0), data.get("yyq_SO2", 0),
                    data.get("jyq_SO2", 0), data.get("yyq_O2", 0),
                    data.get("yyq_LL", 0), data.get("jyq_LL", 0),
                    data.get("xst_YW", 0), data.get("xstjyxhb_ADL", 0),
                    data.get("xstjyxhb_BDL", 0), data.get("xstjyxhb_CDL", 0),
                    data.get("xstjyxhb_DDL", 0), data.get("xstjyxhb_EDL", 0),
                    data.get("xstyhfj_ADL", 0), data.get("xstjy_PH", 0),
                    data.get("xst_ADL_status", 0), data.get("xst_BDL_status", 0),
                    data.get("xst_CDL_status", 0), data.get("xst_DDL_status", 0),
                    data.get("xst_EDL_status", 0), data.get("xst_pump_status", ""),
                    data.get("combined_pump_status", ""), data.get("liquid_gas_ratio", 0),
                    data.get("desulfurization_efficiency", 0),
                )
            )
        except Exception as e:
            traceback.print_exc()
            logging.error("方法产生了异常为insert_data 中的" + str(e))

    def _integration_config(self):
        import copy
        from system.model.map_control.condition_model.condition_config import (
            ONLINE_CONDITION_CLASSIFY_CONFIG,
        )
        bridge = copy.deepcopy(
            ONLINE_CONDITION_CLASSIFY_CONFIG.get("slurry_policy_online", {})
        )
        bridge["enabled"] = True
        bridge["config_spec"] = self._core_path("slurry_policy_config")
        bridge["external_version_management"] = True
        integrated = dict(bridge.get("integrated_version") or {})
        integrated.update({
            "enabled": True,
            "active_version_file": self._core_path("active_version_file"),
            "hot_reload_enabled": True,
            "reload_check_interval_seconds": max(1.0, float(self.snapshot_interval)),
            "verify_condition_snapshot_hash": True,
            "require_atomic_pair_switch": True,
            "reset_condition_stability_window": True,
            "preserve_runtime_control_state": True,
            "keep_current_version_on_failure": True,
        })
        bridge["integrated_version"] = integrated
        return bridge

    def _ensure_slurry_pipeline(self):
        with self._slurry_pipeline_lock:
            if self._slurry_pipeline is not None:
                return True
        return self.reload_models()

    def _runtime_target(self, data, explicit_target):
        if explicit_target not in (None, ""):
            try:
                return float(explicit_target)
            except (TypeError, ValueError):
                pass
        column = str(self.slurry_core_config.get("target_column", "outlet_so2_target"))
        value = data.get(column)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
        return None

    @staticmethod
    def _safe_online_hold(data, error):
        result = dict(data)
        result.update({
            "condition_valid": False,
            "condition_stable": False,
            "version_consistent": False,
            "slurry_policy_decision_status": "BLOCKED",
            "slurry_policy_control_mode": "BLOCKED",
            "slurry_policy_action_id": "HOLD",
            "slurry_policy_action_family": "HOLD",
            "slurry_policy_action_direction": "HOLD",
            "slurry_policy_action_magnitude": "HOLD",
            "slurry_policy_recommended_valve_deltas": {},
            "slurry_policy_projected_valve_openings": {},
            "slurry_policy_reason_codes": [
                "INTEGRATED_SLURRY_MODEL_UNAVAILABLE", error or "UNKNOWN"
            ],
        })
        return result

    def insert_Mod(self, data, target_so2, store_to_db=True):
        """在线模型统一入口；由可配置快照周期调用 condition + slurry_policy Pipeline。"""
        str_time = data.get("date", pd.Timestamp.now())
        if self.system_state != self.SystemState.NORMAL_OPERATION:
            result = dict(data)
        elif self._ensure_slurry_pipeline():
            try:
                result = dict(self._slurry_pipeline.process(
                    dict(data),
                    target=self._runtime_target(data, target_so2),
                    execution_context={},
                ))
            except Exception as exc:
                logging.error("供浆集成在线推理失败: %s", exc)
                traceback.print_exc()
                result = self._safe_online_hold(data, str(exc))
        else:
            result = self._safe_online_hold(data, self._slurry_pipeline_error)

        result["date"] = str_time
        result["model_seq"] = data.get("_snapshot_seq", -1)
        result["_write_target"] = self.process_config.persistence.model_write_target
        result["_is_valid"] = store_to_db
        if not store_to_db:
            result["_invalid_reason"] = "数据验证失败"

        send_copy = {
            k: v for k, v in result.items() if not str(k).startswith('_')
        }
        self.send_data = send_copy
        self.result = send_copy
        self._publish_map_control(send_copy)
        self.send()
        logging.info(
            "供浆模型结果: condition=%s action=%s magnitude=%s version=%s",
            send_copy.get("condition_label"),
            send_copy.get("slurry_policy_action_family"),
            send_copy.get("slurry_policy_action_magnitude"),
            send_copy.get("integrated_active_version"),
        )
        return result

    def check_incremental_training(self):
        try:
            now = datetime.datetime.now()
            interval_days = int(self.process_config.training.incremental_trigger_interval_days)
            due = (
                self.system_state == self.SystemState.NORMAL_OPERATION
                and (
                    self.last_training_time is None
                    or (now - self.last_training_time).total_seconds()
                    >= interval_days * 24 * 3600
                )
            )
            if not due:
                return False
            if not self.check_data_accumulation(mode='incremental'):
                return False
            self.is_initial_training = False
            self.training_event.clear()
            self.training_event.set()
            return True
        except Exception as exc:
            logging.error('检查增量训练异常: %s', exc)
            return False

    def _publish_map_control(self, payload):
        if payload is None:
            return
        with self.map_control_lock:
            current = self.GLOBAL_DATA.get("map_control", {})
            merged = dict(current)
            merged.update(payload)
            self.GLOBAL_DATA["map_control"] = merged

    def send_realtime_to_dcs(self, realtime_data, realtime_seq=None):
        try:
            realtime_data_with_marks = realtime_data.copy()
            realtime_data_with_marks.update({
                "realtime_update": True,
                "last_realtime_update": datetime.datetime.now().isoformat(),
                "data_source": "realtime_calculation"
            })
            if realtime_seq is not None:
                realtime_data_with_marks["realtime_seq"] = realtime_seq
                realtime_data_with_marks["last_snapshot_seq"] = realtime_seq
                self._last_realtime_published_seq = realtime_seq
            self._publish_map_control(realtime_data_with_marks)
        except Exception as e:
            logging.error(f"发送实时数据到DCS失败: {str(e)}")
            traceback.print_exc()

    def send_to_ws(self):
        """旧前端接口兼容保留，本轮不改前端字段。"""
        try:
            if self.send_data:
                model_key_fields = {
                    'recommended_pump': self.send_data.get('recommended_pump', ''),
                    'suggested_xst_ph': self.send_data.get('suggested_xst_ph'),
                    'cluster_label': self.send_data.get('cluster_label'),
                    'confidence': self.send_data.get('confidence', 0),
                    'model_seq': self.send_data.get('model_seq', -1),
                    'last_model_update': datetime.datetime.now().isoformat(),
                }
                self._publish_map_control(model_key_fields)
        except Exception as exc:
            logging.error('send_to_ws 方法产生异常: %s', exc)

    def send(self):
        pass

    def add_data_to_databases(self, data):
        """原结果表结构/insert 暂时保持不变，后续单独调整。"""
        if not self.mod_pre_table_name.endswith(
            str(datetime.datetime.now().year) + "_" + str(datetime.datetime.now().month)
        ):
            self.mod_pre_table_name = (
                self.process_config.persistence.model_result_table_prefix
                + str(datetime.datetime.now().year) + "_" + str(datetime.datetime.now().month)
            )
            fields = [
                '"id" uuid NOT NULL', '"date" timestamp(6) NOT NULL',
                '"xstshsjy_MD" float8', '"yyq_SO2" float8', '"jyq_SO2" float8',
                '"yyq_O2" float8', '"yyq_LL" float8', '"jyq_LL" float8',
                '"xst_YW" float8', '"xstjyxhb_ADL" float8',
                '"xstjyxhb_BDL" float8', '"xstjyxhb_CDL" float8',
                '"xstjyxhb_DDL" float8', '"xstjyxhb_EDL" float8',
                '"xstyhfj_ADL" float8', '"xstjy_PH" float8',
                '"xst_ADL_status" int', '"xst_BDL_status" int',
                '"xst_CDL_status" int', '"xst_DDL_status" int',
                '"xst_EDL_status" int', '"xst_pump_status" varchar(20)',
                '"combined_pump_status" varchar(20)', '"liquid_gas_ratio" float8',
                '"desulfurization_efficiency" float8', '"cluster_label" int',
                '"timestamp" timestamp(6)', '"confidence" float8',
                '"recommended_pump" varchar(20)', '"drop_flag" varchar(20)',
                '"suggested_xst_ph" float8', '"event_type" varchar(80)',
                '"is_stable" varchar(20)', '"cache_size" int', '"final_condition" int'
            ]
            sql = (
                f'DROP TABLE IF EXISTS "public".{self.mod_pre_table_name}; '
                f'CREATE TABLE "public".{self.mod_pre_table_name} ({", ".join(fields)})'
            )
            self.engine.execute(sql)
            self.engine.execute(
                'ALTER TABLE "public".' + self.mod_pre_table_name
                + ' ADD CONSTRAINT "%s" PRIMARY KEY ("id");',
                ("primary_" + self.mod_pre_table_name)
            )
        try:
            data = data[0]
            if "date" in data and isinstance(data["date"], str):
                data["date"] = pd.to_datetime(data["date"], errors="coerce")
            if "timestamp" in data and isinstance(data["timestamp"], str):
                data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
            if isinstance(data.get("is_stable"), bool):
                data["is_stable"] = "true" if data["is_stable"] else "false"
            elif data.get("is_stable") is None:
                data["is_stable"] = "false"
            data["drop_flag"] = "" if data.get("drop_flag") is None else str(data.get("drop_flag", ""))
            sql = f"""
                insert into {self.mod_pre_table_name}(
                "id", "date","xstshsjy_MD", "yyq_SO2","jyq_SO2",
                "yyq_O2", "yyq_LL", "jyq_LL", "xst_YW",
                "xstjyxhb_ADL", "xstjyxhb_BDL", "xstjyxhb_CDL", "xstjyxhb_DDL", "xstjyxhb_EDL",
                "xstyhfj_ADL", "xstjy_PH",
                "xst_ADL_status", "xst_BDL_status", "xst_CDL_status", "xst_DDL_status","xst_EDL_status",
                "xst_pump_status", "combined_pump_status", "liquid_gas_ratio", "desulfurization_efficiency",
                "cluster_label","timestamp","confidence","recommended_pump","drop_flag","suggested_xst_ph",
                "event_type","is_stable","cache_size","final_condition"
                ) values(
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """
            args = (
                uuid.uuid4(), data.get("date", pd.Timestamp.now()),
                data.get("xstshsjy_MD", 0), data.get("yyq_SO2", 0),
                data.get("jyq_SO2", 0), data.get("yyq_O2", 0), data.get("yyq_LL", 0),
                data.get("jyq_LL", 0), data.get("xst_YW", 0), data.get("xstjyxhb_ADL", 0),
                data.get("xstjyxhb_BDL", 0), data.get("xstjyxhb_CDL", 0),
                data.get("xstjyxhb_DDL", 0), data.get("xstjyxhb_EDL", 0),
                data.get("xstyhfj_ADL", 0), data.get("xstjy_PH", 0),
                data.get("xst_ADL_status", 0), data.get("xst_BDL_status", 0),
                data.get("xst_CDL_status", 0), data.get("xst_DDL_status", 0),
                data.get("xst_EDL_status", 0), data.get("xst_pump_status", ""),
                data.get("combined_pump_status", ""), data.get("liquid_gas_ratio", 0),
                data.get("desulfurization_efficiency", 0), data.get("cluster_label", None),
                data.get("timestamp", pd.Timestamp.now()), data.get("confidence", 0),
                data.get("recommended_pump", ""), data.get("drop_flag", ""),
                data.get("suggested_xst_ph", 0), data.get("event_type", ""),
                data.get("is_stable", "false"), data.get("cache_size", 0),
                data.get("final_condition", 0)
            )
            self.engine.execute(str(sql), args)
        except Exception as e:
            traceback.print_exc()
            logging.error("model_pre,add_data_to_databases ==>%s", str(e))

    def limiter(self, data, limit):
        try:
            for var in data.index:
                if var in limit.index:
                    if data.loc[var] < limit.loc['min', var]:
                        data.loc[var] = limit.loc['min', var]
                    if data.loc[var] > limit.loc['max', var]:
                        data.loc[var] = limit.loc['max', var]
            return data
        except Exception:
            traceback.print_exc()
            return data

    def outliers_threshold(self, df, low, high):
        quant_df = df.quantile([low, high])
        return quant_df.iloc[1], quant_df.iloc[0]

    def data_cal_no_fentch(self, sql, parem):
        try:
            self.engine.execute(sql, parem)
        except Exception:
            traceback.print_exc()

    def data_cal(self, sql, parem):
        return self.engine.execute(sql, parem).fetchall()

    def clean_data(self, message):
        """实时预处理入口。

        只对 p4pc 已配置字段执行原有数值转换/滤波/特征生成；上游额外传入的
        字段一律保留并继续传到最新快照，因此后续新增阀位、二级塔或其他现场
        字段时，不需要在本函数维护第二套字段清单。

        本函数按原始采集节奏处理数据；真正的 condition + policy 模型调用仍由
        _snapshot_scheduler_loop 按 snapshot_interval_seconds 的可配置周期触发。
        """
        try:
            if not message:
                logging.warning("没有有效数据")
                return None
            msg = dict(message[0])
            row_dict = dict(msg)
            if "date" in msg:
                try:
                    row_dict["date"] = pd.to_datetime(msg["date"])
                except Exception:
                    row_dict["date"] = pd.Timestamp.now()
            else:
                row_dict["date"] = pd.Timestamp.now()

            for col in self.titles:
                if col == "date":
                    continue
                if col not in row_dict:
                    row_dict[col] = 0.0
                    continue
                try:
                    row_dict[col] = float(row_dict[col])
                except (ValueError, TypeError):
                    # 非数值现场字段不再被强行覆盖为0，直接原样透传。
                    pass

            try:
                row_dict["jym"] = int(
                    msg.get("jym", self.process_config.data_validation.default_jym)
                )
            except Exception:
                row_dict["jym"] = int(self.process_config.data_validation.default_jym)

            index = row_dict["date"]
            data_dict = {k: v for k, v in row_dict.items() if k != "date"}
            filtered_data = self.data_preprocessor.filter_realtime_data(data_dict)
            filtered_data["date"] = index
            try:
                realtime_data = self.data_preprocessor.generate_features(filtered_data)
            except Exception as e:
                logging.error(f"基础特征生成失败: {str(e)}")
                realtime_data = filtered_data.copy()

            # 关键：任何未进入现有预处理配置的现场字段继续原样传递给新核心。
            for key, value in msg.items():
                if key != "date":
                    realtime_data.setdefault(key, value)

            rt_date = realtime_data.get("date")
            if rt_date is None or rt_date == "":
                realtime_data["date"] = index.strftime("%Y-%m-%d %H:%M:%S")
            elif hasattr(rt_date, "strftime"):
                realtime_data["date"] = rt_date.strftime("%Y-%m-%d %H:%M:%S")
            else:
                realtime_data["date"] = str(rt_date)

            realtime_seq = self._update_latest_snapshot(realtime_data)
            self.send_realtime_to_dcs(realtime_data, realtime_seq=realtime_seq)
            return None
        except Exception as e:
            logging.error(f"clean_data 方法出现了异常: {str(e)}")
            traceback.print_exc()
            return None

    def _run_training_command(self, label, args, env, cwd=None):
        logging.info('%s 命令: %s', label, ' '.join(map(str, args)))
        result = subprocess.run(
            [str(value) for value in args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=cwd,
            shell=False,
        )
        stdout = result.stdout.decode('utf-8', errors='replace')
        stderr = result.stderr.decode('utf-8', errors='replace')
        if stdout:
            logging.info('%s 标准输出: %s', label, stdout)
        if result.returncode != 0:
            logging.error('%s 失败: %s', label, stderr)
            raise RuntimeError(f'{label} 失败: {stderr}')
        if stderr:
            logging.warning('%s 标准错误输出: %s', label, stderr)

    def _training_env(self):
        project_root = self._project_root()
        env = os.environ.copy()
        python_paths = [
            project_root,
            os.path.dirname(__file__),
            os.path.join(project_root, 'system'),
            os.path.join(project_root, 'system', 'model'),
            os.path.join(project_root, 'system', 'model', 'map_control'),
        ]
        if env.get('PYTHONPATH'):
            python_paths.append(env['PYTHONPATH'])
        env['PYTHONPATH'] = os.pathsep.join(python_paths)
        return project_root, env

    def _condition_paths_for_version(self, version):
        root = Path(self._core_path("condition_snapshots_dir")) / version
        return {
            "snapshot": str(root / "condition_snapshot.json"),
            "report": str(root / "auto_merge_report.json"),
        }

    def _run_condition_initial(self, python_exe, env, training_csv, version):
        paths = self._condition_paths_for_version(version)
        output_csv = self._core_path("initial_condition_output_csv")
        script = self._core_path("condition_initial_script")
        self._run_training_command(
            "initial-condition-model",
            [
                python_exe, script,
                "--input", training_csv,
                "--output", output_csv,
                "--snapshot-output", paths["snapshot"],
                "--merge-statistics-output", self._core_path("condition_merge_statistics"),
                "--auto-merge-report", paths["report"],
                "--snapshot-version", version,
            ],
            env,
            cwd=os.path.dirname(script),
        )
        return output_csv, paths["snapshot"]

    def _run_condition_incremental(
        self, python_exe, env, training_csv, active_version, target_version
    ):
        base_snapshot = self._condition_paths_for_version(active_version)["snapshot"]
        target_paths = self._condition_paths_for_version(target_version)
        output_csv = self._core_path("incremental_condition_output_csv")
        script = self._core_path("condition_incremental_script")
        self._run_training_command(
            "incremental-condition-model",
            [
                python_exe, script,
                "--base-snapshot", base_snapshot,
                "--input", training_csv,
                "--output", output_csv,
                "--snapshot-output", target_paths["snapshot"],
                "--merge-statistics-output", self._core_path("condition_merge_statistics"),
                "--auto-merge-report", target_paths["report"],
                "--snapshot-version", target_version,
            ],
            env,
            cwd=os.path.dirname(script),
        )
        return output_csv, target_paths["snapshot"]

    def _run_policy_initial(self, python_exe, env, labeled_csv, condition_snapshot):
        script = self._core_path("slurry_policy_initial_script")
        self._run_training_command(
            "initial-slurry-policy",
            [
                python_exe, script,
                "--input", labeled_csv,
                "--output", self._core_path("slurry_policy_output_root"),
                "--condition-snapshot", condition_snapshot,
                "--config", self._core_path("slurry_policy_config"),
            ],
            env,
            cwd=os.path.dirname(script),
        )

    def _run_policy_incremental(
        self, python_exe, env, labeled_csv, condition_snapshot, active_version
    ):
        script = self._core_path("slurry_policy_incremental_script")
        previous = (
            Path(self._core_path("slurry_policy_output_root"))
            / "snapshots" / active_version
        )
        self._run_training_command(
            "incremental-slurry-policy",
            [
                python_exe, script,
                "--input", labeled_csv,
                "--output", self._core_path("slurry_policy_output_root"),
                "--previous", str(previous),
                "--condition-snapshot", condition_snapshot,
                "--config", self._core_path("slurry_policy_config"),
            ],
            env,
            cwd=os.path.dirname(script),
        )

    def _do_training(self):
        """p4pc 唯一训练入口：condition_model -> slurry_policy_model。"""
        mode = 'initial' if self.is_initial_training else 'incremental'
        try:
            with self.training_lock:
                logging.info('=== 开始供浆核心 %s 训练流程 ===', mode)
                df, settings = self._load_training_data(mode)
                training_csv = self._save_training_work_csv(df, settings)
                _, env = self._training_env()
                python_exe = config.get('python_exe', 'python')

                if mode == 'initial':
                    version = str(self.slurry_core_config.get("initial_version", "v001"))
                    labeled_csv, condition_snapshot = self._run_condition_initial(
                        python_exe, env, training_csv, version
                    )
                    self._run_policy_initial(
                        python_exe, env, labeled_csv, condition_snapshot
                    )
                    if not self.hot_update_models(version):
                        raise RuntimeError("初次同版本模型激活/加载失败")
                    self.is_initial_training = False
                    self.model_training_completed = True
                    self.system_state = self.SystemState.NORMAL_OPERATION
                    self.last_training_time = datetime.datetime.now()
                    logging.info(
                        '初次 condition_model + slurry_policy_model 训练并激活完成: %s',
                        version
                    )
                    return

                if self.system_state != self.SystemState.NORMAL_OPERATION:
                    logging.warning('当前系统状态不是 NORMAL_OPERATION，禁止增量训练')
                    return

                active_version = self._read_active_version()
                target_version = self._next_version(active_version)
                labeled_csv, condition_snapshot = self._run_condition_incremental(
                    python_exe, env, training_csv, active_version, target_version
                )
                self._run_policy_incremental(
                    python_exe, env, labeled_csv, condition_snapshot, active_version
                )
                if not self.hot_update_models(target_version):
                    raise RuntimeError("增量同版本模型激活失败")
                self.last_training_time = datetime.datetime.now()
                logging.info(
                    '增量 condition_model + slurry_policy_model 完成: %s -> %s',
                    active_version, target_version
                )
        except Exception as exc:
            logging.error('供浆核心 %s 训练失败: %s', mode, exc)
            traceback.print_exc()
            if mode == 'initial':
                self.model_training_completed = False
                self.system_state = self.SystemState.DATA_COLLECTION
            # 增量失败不改变 active_version.json，在线继续使用旧版本。
            raise
        finally:
            self.is_training = False

    def hot_update_models(self, target_version=None):
        """新核心热更新：只做同版本原子激活，不再复制Q-learning/PH目录。

        若在线 Pipeline 已存在，active_version.json 更新后由其
        IntegratedVersionManager 在后续模型周期中原子切换并保留运行状态；
        初次训练或进程启动时没有 Pipeline，则立即加载已激活版本。
        """
        try:
            if target_version:
                _, env = self._training_env()
                python_exe = config.get('python_exe', 'python')
                script = self._core_path("slurry_policy_activate_script")
                self._run_training_command(
                    "activate-integrated-version",
                    [
                        python_exe, script,
                        "--version", str(target_version),
                        "--config", self._core_path("slurry_policy_config"),
                    ],
                    env,
                    cwd=os.path.dirname(script),
                )
            if self._slurry_pipeline is None:
                return self.reload_models()
            logging.info(
                "active_version.json 已更新为 %s；现有在线Pipeline将在后续周期原子热切换",
                target_version or self._read_active_version(),
            )
            return True
        except Exception as e:
            logging.error(f"模型热更新失败: {str(e)}")
            traceback.print_exc()
            return False

    def clean_model_backups(self, days=14):
        """兼容旧调用。新核心使用版本化快照，不再复制临时模型目录。"""
        return None

    def reload_models(self):
        """加载 active_version.json 指向的 condition + policy 同版本 Pipeline。"""
        try:
            from system.model.map_control.condition_model.online_condition_classifier import (
                build_online_condition_policy_pipeline,
            )
            candidate = build_online_condition_policy_pipeline(
                snapshot_path="active",
                integration_config=self._integration_config(),
            )
            with self._slurry_pipeline_lock:
                self._slurry_pipeline = candidate
                self._slurry_pipeline_error = None
            logging.info("condition_model + slurry_policy_model 集成在线Pipeline加载完成")
            return True
        except Exception as e:
            self._slurry_pipeline_error = str(e)
            logging.error(f"重新加载集成在线模型失败: {str(e)}")
            traceback.print_exc()
            return False

    def record_slurry_execution(self, feedback):
        """后续DCS实际执行反馈接入口。"""
        if not self._ensure_slurry_pipeline():
            raise RuntimeError(
                "integrated slurry pipeline unavailable: %s"
                % (self._slurry_pipeline_error or "UNKNOWN")
            )
        return dict(self._slurry_pipeline.record_execution(dict(feedback)))

    def check_system_state(self):
        while True:
            try:
                if self.system_state == self.SystemState.DATA_COLLECTION:
                    if self.check_data_accumulation() and not self.is_training:
                        self.system_state = self.SystemState.MODEL_TRAINING
                        self.training_event.set()
                elif self.system_state == self.SystemState.MODEL_TRAINING:
                    if self.model_training_completed:
                        self.system_state = self.SystemState.NORMAL_OPERATION
                elif self.system_state == self.SystemState.NORMAL_OPERATION:
                    if not self.is_training:
                        self.check_incremental_training()
                time.sleep(float(self.process_config.runtime.state_check_interval_seconds))
            except Exception as e:
                logging.error(f"系统状态检查发生错误: {str(e)}")
                time.sleep(60)
