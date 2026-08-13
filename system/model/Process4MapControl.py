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
from system.model.config.database_schema import (
    ensure_filter_table,
    ensure_model_result_table,
    insert_filter_row,
    insert_model_result_row,
)
import logging
import shutil
from system.model.map_control.MapControPre import MapControPre  #推荐泵以及PH建议
from system.model.map_control.data_preprocessor1 import DataPreprocessor    #数据预处理
from system.model.map_control.SO2_processor import SO2Processor  # SO2计算
from system.model.map_control.yhfj_and_jzgj_model import ProcessControl as YHFJProcessControl  # 氧化风机和基准供浆计算
from system.model.map_control.tower_power_consumption import TowerPowerCalculator  # 塔电耗计算
from system.model.map_control.cost_calculator import CostCalculator  # 成本计算
from system.model.map_control.StatCalc import PHStatCalc  # 统计计算
logging = setup_log("process_for_mapconsole")
psycopg2.extras.register_uuid()

import time
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
        """添加数据到缓存"""
        current_time = time.time()
        data_with_time = {
            'timestamp': current_time,
            'data': data.copy()
        }
        self.data_buffer.append(data_with_time)
        self.calibration_buffer.append(data_with_time)
        
    def calculate_change_rate(self, field_name, time_window=None):
        """计算指定字段在时间窗口内的变化量"""
        if time_window is None:
            time_window = float(self.config.change_rate_window_seconds)
        if len(self.data_buffer) < 2:
            return 0
            
        current_time = time.time()
        current_value = self.data_buffer[-1]['data'].get(field_name, 0)
        
        # 找到时间窗口前的数据点
        for i in range(len(self.data_buffer) - 2, -1, -1):
            if current_time - self.data_buffer[i]['timestamp'] >= time_window:
                old_value = self.data_buffer[i]['data'].get(field_name, 0)
                return abs(current_value - old_value)
                
        # 如果没有足够的历史数据，使用最早的数据点
        if len(self.data_buffer) >= 2:
            old_value = self.data_buffer[0]['data'].get(field_name, 0)
            return abs(current_value - old_value)
            
        return 0
    
    def detect_calibration_pattern(self):
        """检测校验模式：净烟气SO2突然大幅变化后又快速恢复"""
        if len(self.calibration_buffer) < int(self.config.calibration_min_samples):  # 需要足够的数据点
            return False
            
        current_time = time.time()
        
        # 检查是否在冷却期内
        if (self.last_calibration_time and 
            current_time - self.last_calibration_time < self.calibration_cooldown):
            return True  # 仍在校验期间
            
        # 检测最近10分钟内的SO2变化模式
        recent_data = []
        for item in self.calibration_buffer:
            if current_time - item['timestamp'] <= self.calibration_detection_window:
                recent_data.append(item)
                
        if len(recent_data) < int(self.config.calibration_min_samples):
            return False
            
        # 检测异常波动模式
        calibration_events = 0
        for i in range(1, len(recent_data)):
            prev_so2 = recent_data[i-1]['data'].get('jyq_SO2', 0)
            curr_so2 = recent_data[i]['data'].get('jyq_SO2', 0)
            
            # 检测大幅变化（1分钟内变化>10）
            if abs(curr_so2 - prev_so2) > self.jyq_so2_change_threshold:
                calibration_events += 1
                
        # 如果检测到多次异常波动，认为在校验
        if calibration_events >= int(self.config.calibration_event_count_threshold):
            self.last_calibration_time = current_time
            return True
            
        return False
    
    def validate_data(self, data):
        """
        验证数据是否有效（针对降采样后的30帧均值数据）
        Returns:
            tuple: (is_valid, reason)
        """
        # 添加数据到缓存
        self.add_data(data)

        # 1. 检查校验码（与旧版一致：jym=100 视为校验态无效数据）
        try:
            jym_value = int(data.get('jym', self.config.default_jym))
        except Exception:
            jym_value = int(self.config.default_jym)
        if jym_value == self.calibration_code:
            return False, f"检测到校验码jym={jym_value}，数据无效"

        # 2. 检查净烟气SO2绝对值（降采样均值仍有意义）
        jyq_so2_value = data.get('jyq_SO2', 0)
        if jyq_so2_value > self.jyq_so2_value_threshold:
            return False, f"净烟气SO2均值过大: {jyq_so2_value} > {self.jyq_so2_value_threshold}"

        return True, "数据有效"

    
    def get_status(self):
        """获取验证器状态信息"""
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

    # 系统状态枚举
    class SystemState:
        DATA_COLLECTION = "data_collection"      # 数据收集阶段
        MODEL_TRAINING = "model_training"        # 模型训练阶段
        NORMAL_OPERATION = "normal_operation"    # 正常运行阶段

    def __init__(self,GLOBAL_DATA):
        self.process_config = PROCESS4MAP_CONFIG
        self.slurry_core_config = dict(SLURRY_CORE_BRIDGE_CONFIG)
        self._slurry_pipeline = None
        self._slurry_pipeline_error = None
        self._slurry_pipeline_lock = threading.RLock()
        # # 测试标值start
        # self.system_state = self.SystemState.NORMAL_OPERATION  # 直接进入正常运行阶段
        # self.model_training_completed = True  # 标记模型已训练完成
        # self.is_initial_training = False      # 标记不是初次训练
        # self.is_training = False  # 训练状态标志
        # self.last_training_time = datetime.datetime.now()  # 上次训练时间
        #实际运行标志
        self.is_training = False  # 训练状态标志event
        self.system_state = self.process_config.runtime.initial_system_state  # 初始状态为数据收集
        self.model_training_completed = False  # 模型训练完成标志
        self.is_initial_training = True  # 是否是初次训练
        self.last_training_time = None  #上次训练时间
        ##end

        self.GLOBAL_DATA = GLOBAL_DATA
        self.training_lock = threading.Lock()  # 添加训练锁
        self.limit = pd.DataFrame.from_dict(self.limit)  # 把limit转为dataframe数据类型
        self.engine = create_engine(config["dbconnetion"])
        self.queen = Queue(maxsize=int(self.process_config.runtime.insert_queue_size))  # insert_data()
        self.queue_keys = []
        self.df = None  # insert_data()
        self.count = 0  # insert_data()
        self.data_preprocessor=DataPreprocessor()
        self.filter_write_pool = ThreadPoolExecutor(max_workers=int(self.process_config.runtime.filter_writer_workers), thread_name_prefix='filter_writer')
        self.model_result_pool = ThreadPoolExecutor(max_workers=int(self.process_config.runtime.model_writer_workers), thread_name_prefix='model_writer')
        self.filter_data = pd.DataFrame(columns=self.titles)
        self.filter_table_name = self.process_config.persistence.filter_table_prefix + str(datetime.datetime.now().year) + "_" + str(
            datetime.datetime.now().month)
        self.so2_processor=SO2Processor()   # 初始化SO2处理器
        self.i = 0  # data_clean()
        self.tower_power_calculator = TowerPowerCalculator()  # 塔电耗计算
        self.cost_calculator = CostCalculator()  # 成本计算器初始化
        self.stat_calc = PHStatCalc(comm_T=float(self.process_config.runtime.stat_calc_comm_t))  # 统计计算器初始化
        # 添加数据验证器
        self.data_validator = DataValidator(self.process_config.data_validation)
        # 原烟气 SO2 低值持续时间状态；使用 monotonic 避免系统时钟调整。
        self._unit_stop_condition_since_monotonic = None
        self._unit_stop_elapsed_seconds = 0.0
    
        self.getNewDataTableName()
        self.result = None
        self.send_data = None
        self.pump_name_def = {}
        self.mod_pre_table_name = self.process_config.persistence.model_result_table_prefix + str(datetime.datetime.now().year) + "_" + str(
            datetime.datetime.now().month)
        self.get_pump_name()
        # 确保titles包含date列
        if "date" not in self.titles:
            self.titles = ["date"] + self.titles
        # 初始化空的DataFrame，包含所有列
        self.tempdf = pd.DataFrame(columns=self.titles)

        self.training_event=threading.Event()
        self.snapshot_interval = float(self.process_config.runtime.snapshot_interval_seconds)
        self.map_control_lock = threading.Lock()
        self.snapshot_lock = threading.Lock()
        self._latest_processed_snapshot = None
        self._latest_processed_snapshot_seq = 0
        self._last_snapshot_emit_ts = 0.0
        self._last_filter_emitted_seq = -1
        self._last_model_emitted_seq = -1
        self._last_realtime_published_seq = -1
        # consume_data 幂等游标：避免反复消费同一帧
        self._last_consumed_frame_key = None
        # 写库幂等游标：避免同一结果重复入库
        self._last_filter_written_key = None
        self._last_model_written_key = None
        self.maintenance_interval = float(self.process_config.runtime.maintenance_interval_seconds)
        self.global_data_maxlen = int(self.process_config.runtime.global_data_maxlen)

        self.yhfj_process_control = YHFJProcessControl()   # 基准供浆和氧化风机的计算

        # 有序处理队列：consume_data -> data_queue -> processing_loop -> db_queue -> db_writer_loop
        self.data_queue = Queue(maxsize=int(self.process_config.runtime.data_queue_size))    # 原始数据队列，最多缓60帧
        self.db_queue = Queue(maxsize=int(self.process_config.runtime.db_queue_size))     # 待写库结果队列

        # 若已有第一/第二模块同版本 active_version.json，则启动时直接恢复在线状态。
        self._restore_active_runtime_if_available()

        # 启动状态检查线程（每5min检查一次）
        self.state_check_thread = threading.Thread(target=self.check_system_state)
        self.state_check_thread.start()
        # consume_data 只做入队，不再直接调 clean_data
        self.data_consumer_thread = threading.Thread(target=self.consume_data)
        self.data_consumer_thread.start()
        # processing_loop 从 data_queue 取数据，单线程直接处理后写 db_queue
        self.processing_loop_thread = threading.Thread(target=self.processing_loop, daemon=True)
        self.processing_loop_thread.start()
        # db_writer_loop 串行从 db_queue 消费，保证写库顺序
        self.db_writer_thread = threading.Thread(target=self._db_writer_loop, daemon=True)
        self.db_writer_thread.start()
        # 模型判定周期由 runtime.snapshot_interval_seconds 配置，默认30秒。
        self.snapshot_scheduler_thread = threading.Thread(target=self._snapshot_scheduler_loop, daemon=True)
        self.snapshot_scheduler_thread.start()
        self.maintenance_thread = threading.Thread(target=self._maintenance_loop, daemon=True)
        self.maintenance_thread.start()
        self.training_thread = threading.Thread(target=self.training_worker)
        self.training_thread.daemon = True  # 设置为守护线程
        self.training_thread.start()
        self.training_start_time = None  # 训练开始时间
    def check_training_status(self):
        """检查训练状态"""
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
                        self.GLOBAL_DATA["data"] = deque(data_store[-self.global_data_maxlen:], maxlen=self.global_data_maxlen)
                        logging.warning(f"maintenance normalized GLOBAL_DATA['data'] list to deque(maxlen={self.global_data_maxlen})")
                elif isinstance(data_store, deque):
                    if data_store.maxlen is None:
                        self.GLOBAL_DATA["data"] = deque(list(data_store)[-self.global_data_maxlen:], maxlen=self.global_data_maxlen)
                        logging.warning(f"maintenance bounded GLOBAL_DATA['data'] deque maxlen to {self.global_data_maxlen}")
                else:
                    if data_store is None:
                        self.GLOBAL_DATA["data"] = deque(maxlen=self.global_data_maxlen)
                    else:
                        try:
                            self.GLOBAL_DATA["data"] = deque(list(data_store)[-self.global_data_maxlen:], maxlen=self.global_data_maxlen)
                        except Exception:
                            self.GLOBAL_DATA["data"] = deque(maxlen=self.global_data_maxlen)
                        logging.warning(f"maintenance rebuilt GLOBAL_DATA['data'] as deque(maxlen={self.global_data_maxlen})")
                data_q_size = self.data_queue.qsize() if hasattr(self, "data_queue") else -1
                db_q_size = self.db_queue.qsize() if hasattr(self, "db_queue") else -1
                logging.info(f"maintenance tick data_queue={data_q_size} db_queue={db_q_size} snapshot_seq={self._latest_processed_snapshot_seq}")
            except Exception as e:
                logging.error(f"_maintenance_loop 异常: {str(e)}")
                traceback.print_exc()
            time.sleep(self.maintenance_interval)
    def training_worker(self):
        """训练工作线程"""
        while True:
            try:
                logging.info("训练工作线程等待训练事件...")
                self.training_event.wait()  # 阻塞等待训练信号
                
                # 添加更多日志来追踪状态
                logging.info(f"收到训练事件，当前状态：is_training={self.is_training}, "
                            f"system_state={self.system_state}")
                
                if self.is_training:
                    logging.info("已有训练任务在进行，清除事件并继续等待")
                    self.training_event.clear()
                    continue
                    
                logging.info("开始执行训练流程...")
                self.is_training = True
                
                try:
                    self._do_training()  # 执行实际的训练流程
                except Exception as e:
                    logging.error(f"训练过程发生错误: {str(e)}")
                    traceback.print_exc()
                finally:
                    self.is_training = False
                    self.training_event.clear()  # 清除训练事件
                    logging.info("训练任务处理完成")
                    
            except Exception as e:
                logging.error(f"训练工作线程发生错误: {str(e)}")
                traceback.print_exc()
                time.sleep(float(self.process_config.runtime.training_worker_error_retry_seconds))
    
    @staticmethod
    def _coerce_connection_status(value):
        """将布尔、数字和常见字符串统一转换为通讯状态。"""
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on", "connected", "normal"}:
                return True
            if normalized in {"false", "0", "no", "off", "disconnected", "failed"}:
                return False
        return bool(value)

    def _resolve_connection_status(self, latest_msg=None):
        """优先使用顶层连接状态；缺失时兼容读取最新数据帧中的状态。"""
        global_status = self.GLOBAL_DATA.get("connection_status")
        if global_status is not None:
            return self._coerce_connection_status(global_status)
        if isinstance(latest_msg, dict) and latest_msg.get("connection_status") is not None:
            frame_status = self._coerce_connection_status(
                latest_msg.get("connection_status")
            )
            # 同步到顶层，供前端和后续消费统一读取。
            self.GLOBAL_DATA["connection_status"] = frame_status
            return frame_status
        return False

    def _is_unit_stopped(self, data, now_monotonic=None):
        """按配置字段和比较方式判断停机条件是否连续满足。"""
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
                logging.info(
                    '停机条件开始计时: %s=%s, comparison=%s, threshold=%s',
                    stop_config.field,
                    value,
                    stop_config.comparison,
                    stop_config.threshold,
                )
            self._unit_stop_elapsed_seconds = max(
                0.0,
                now_monotonic - self._unit_stop_condition_since_monotonic,
            )
            return self._unit_stop_elapsed_seconds >= float(stop_config.hold_seconds)

        if self._unit_stop_condition_since_monotonic is not None:
            logging.info('停机条件恢复，清除连续计时: %s=%s', stop_config.field, value)
        self._unit_stop_condition_since_monotonic = None
        self._unit_stop_elapsed_seconds = 0.0
        return False

    def _is_unit_stopped_by_yyq_so2(self, data, now_monotonic=None):
        """兼容旧调用；实际判断字段完全由 UnitStopConfig.field 决定。"""
        return self._is_unit_stopped(data, now_monotonic=now_monotonic)

    def _build_consume_frame_key(self, msg):
        """构造消费幂等键：用于识别是否同一帧"""
        try:
            date_val = str(msg.get("date", ""))
            key_fields = [
                date_val,
                str(msg.get(self.process_config.unit_stop.field, "")),
                str(msg.get("yyq_SO2", "")),
                str(msg.get("jyq_SO2", "")),
                str(msg.get("combined_pump_status", "")),
            ]
            return "|".join(key_fields)
        except Exception:
            return None

    def consume_data(self):
        """只保留最新帧入队：宁可丢中间帧，也不积压旧数据"""
        last_enqueue_time = 0.0
        last_valid_data_time = time.time()

        while True:
            current_time = time.time()
            data_store = self.GLOBAL_DATA.get("data")

            if current_time - last_enqueue_time >= float(self.process_config.runtime.consume_min_interval_seconds) and data_store:
                latest_msg = data_store[-1]
                connection_status = self._resolve_connection_status(latest_msg)
                frame_key = self._build_consume_frame_key(latest_msg)

                # 仅消费新帧，避免重复消费同一条数据
                if frame_key is not None and frame_key == self._last_consumed_frame_key:
                    time.sleep(float(self.process_config.runtime.consume_duplicate_sleep_seconds))
                    continue

                msg = latest_msg.copy()  # 取最新数据副本

                if connection_status or (current_time - last_valid_data_time <= float(self.process_config.runtime.offline_grace_seconds)):
                    if connection_status:
                        last_valid_data_time = current_time
                        map_control = self.GLOBAL_DATA.get("map_control")
                        if isinstance(map_control, dict):
                            map_control["offline_mode"] = False
                            map_control["data_expired"] = False
                    else:
                        if "map_control" in self.GLOBAL_DATA:
                            self.GLOBAL_DATA["map_control"]["offline_mode"] = True
                        logging.warning(
                            f"通讯中断，使用历史数据推理 (已离线: {round(current_time - last_valid_data_time)}秒)"
                        )

                    # 队列满时：先丢弃最旧帧，再放入最新帧（保证实时性）
                    if self.data_queue.full():
                        try:
                            self.data_queue.get_nowait()
                            logging.warning("data_queue full, dropped oldest frame for latest")
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
                        logging.warning("data_queue put_nowait failed, dropped latest frame")
                        last_enqueue_time = current_time
                else:
                    logging.error("通讯中断时间过长，暂停数据处理")
                    if "map_control" in self.GLOBAL_DATA:
                        self.GLOBAL_DATA["map_control"]["data_expired"] = True
                    last_enqueue_time = current_time
            else:
                time.sleep(float(self.process_config.runtime.consume_idle_sleep_seconds))
    def processing_loop(self):
        """从 data_queue 取数据，单线程直接处理并写入 db_queue（不经过线程池内部排队）"""
        while True:
            try:
                item = self.data_queue.get(timeout=float(self.process_config.runtime.processing_queue_timeout_seconds))
            except Empty:
                continue
            except Exception:
                continue

            try:
                result = self.clean_data([item["msg"]])
                if result is not None:
                    if self.db_queue.full():
                        try:
                            self.db_queue.get_nowait()
                            logging.warning("db_queue full, dropped oldest result for latest")
                        except Empty:
                            pass
                    try:
                        self.db_queue.put_nowait(result)
                    except Full:
                        logging.warning("db_queue put_nowait failed, dropped latest result")
            except Exception as e:
                logging.error(f"processing_loop 处理帧异常: {str(e)}")
                traceback.print_exc()
    def _put_db_queue_latest(self, data):
        if data is None:
            return
        if self.db_queue.full():
            try:
                self.db_queue.get_nowait()
                logging.warning("db_queue full, dropped oldest result for latest")
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
        while True:
            try:
                now_ts = time.time()
                if now_ts - self._last_snapshot_emit_ts < self.snapshot_interval:
                    time.sleep(float(self.process_config.runtime.snapshot_poll_interval_seconds))
                    continue
                with self.snapshot_lock:
                    snapshot = self._latest_processed_snapshot.copy() if self._latest_processed_snapshot else None
                    snapshot_seq = self._latest_processed_snapshot_seq
                if snapshot is None:
                    time.sleep(float(self.process_config.runtime.snapshot_poll_interval_seconds))
                    continue
                if snapshot_seq == self._last_filter_emitted_seq and snapshot_seq == self._last_model_emitted_seq:
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
                    stop_config = self.process_config.unit_stop
                    logging.info(
                        "快照检测到机组停机状态 (%s=%s, comparison=%s, threshold=%s, hold=%s秒)，跳过模型结果写库。",
                        stop_config.field,
                        snapshot.get(stop_config.field),
                        stop_config.comparison,
                        stop_config.threshold,
                        stop_config.hold_seconds,
                    )
                    self._last_model_emitted_seq = snapshot_seq
                    continue

                is_valid, validation_reason = self.data_validator.validate_data(snapshot)
                if not is_valid:
                    logging.info(f"快照数据验证失败，跳过模型结果写库但继续推理: {validation_reason}")
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
                snapshot_date = snapshot.get("date")
                lag_seconds = None
                try:
                    if snapshot_date is not None:
                        lag_seconds = round((pd.Timestamp.now() - pd.to_datetime(snapshot_date)).total_seconds(), 3)
                except Exception:
                    lag_seconds = None
                logging.info(
                    f"snapshot_emit seq={snapshot_seq} filter_seq={self._last_filter_emitted_seq} "
                    f"model_seq={self._last_model_emitted_seq} lag_seconds={lag_seconds}"
                )
            except Exception as e:
                logging.error(f"_snapshot_scheduler_loop 异常: {str(e)}")
                traceback.print_exc()
                time.sleep(float(self.process_config.runtime.snapshot_error_retry_seconds))
    def _build_write_key(self, data, write_target):
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

    def _db_writer_loop(self):
        """串行消费 db_queue，按写入目标分发到两张表：
        - _write_target='filter'       -> t_data1_filter_rt_（当前按30秒节流）
        - _write_target='model_result' -> t_model_result_（30秒1条）
        两张表分别走独立线程池，避免互相阻塞。
        """
        while True:
            try:
                data = self.db_queue.get()  # 阻塞等待
                if data is not None:
                    # 取出控制标记，不污染写库数据
                    is_valid = data.pop('_is_valid', True)
                    write_target = data.pop('_write_target', self.process_config.persistence.filter_write_target)  # 默认写实时过滤表
                    invalid_reason = data.pop('_invalid_reason', None)

                    if write_target == 'noop':
                        continue

                    if not is_valid:
                        logging.info(f"_db_writer_loop: 数据无效，跳过写库，原因: {invalid_reason}")
                        continue

                    write_key = self._build_write_key(data, write_target)

                    if write_target == self.process_config.persistence.model_write_target:
                        # 推荐结果写入 t_model_result_
                        try:
                            if write_key is not None and write_key == self._last_model_written_key:
                                logging.debug("model_result write dedupe: skip duplicate")
                                continue
                            self._last_model_written_key = write_key
                            self.model_result_pool.submit(self.add_data_to_databases, [data])
                        except Exception as e:
                            logging.error(f"_db_writer_loop 写 t_model_result_1 失败: {str(e)}")
                    else:
                        # 实时数据 -> t_data1_filter_rt_
                        try:
                            if write_key is not None and write_key == self._last_filter_written_key:
                                logging.debug("filter write dedupe: skip duplicate")
                                continue
                            self._last_filter_written_key = write_key
                            self.filter_write_pool.submit(self.insert_data, data)
                        except Exception as e:
                            logging.error(f"_db_writer_loop 写 t_data1_filter_rt_ 失败: {str(e)}")
            except Exception as e:
                logging.error(f"_db_writer_loop 异常: {str(e)}")
                traceback.print_exc()

    def get_map_pre(self):
        if not hasattr(self, 'map_pre') or self.map_pre is None:
            self.map_pre = MapControPre()
        return self.map_pre
    def get_pump_name(self):
        result = self.engine.execute("select name,layer from t_pump_def order by layer asc").fetchall()
        for i in result:
            self.pump_name_def[str(i[1])] = i[0]

    def getNewDataTableName(self):
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

    def _project_root(self):
        return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

    def _resolve_training_path(self, path_value):
        """解析训练配置路径：绝对路径原样使用，相对路径以项目根目录为基准。"""
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

    def _active_training_watermark(self):
        """读取当前已激活第二模块版本的数据水位。

        watermark 不单独维护一份可漂移状态，而是绑定 active_version.json 指向的
        slurry policy snapshot。只有完整训练并成功激活的版本才会成为下一轮增量起点。
        """
        active_version = self._read_active_version()
        summary_path = (
            Path(self._core_path("slurry_policy_output_root"))
            / "snapshots"
            / active_version
            / "training_summary.json"
        )
        if not summary_path.is_file():
            raise FileNotFoundError(
                "当前激活第二模块缺少 training_summary.json，无法确定增量 watermark: %s"
                % summary_path
            )
        with summary_path.open("r", encoding="utf-8") as stream:
            summary = json.load(stream)
        raw_timestamp = summary.get("last_data_timestamp")
        if raw_timestamp in (None, ""):
            raise RuntimeError(
                "当前激活第二模块 training_summary.json 缺少 last_data_timestamp；"
                "FAST V4 首次升级请重新执行一次完整初次训练。"
            )
        timestamp = pd.to_datetime(raw_timestamp, errors="coerce")
        if pd.isna(timestamp):
            raise RuntimeError(
                "当前激活第二模块 last_data_timestamp 无法解析: %r" % raw_timestamp
            )
        timestamp = pd.Timestamp(timestamp)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert(None)
        return timestamp, active_version

    @staticmethod
    def _normalize_training_dataframe(df, *, context="training"):
        """训练数据统一时间契约：date 可解析、稳定升序，再交给各模块/写 CSV。"""
        if df is None:
            return None
        result = df.copy()
        if "date" not in result.columns:
            raise RuntimeError(f"{context} 缺少必需时间字段 date")
        parsed = pd.to_datetime(result["date"], errors="coerce")
        invalid_count = int(parsed.isna().sum())
        if invalid_count:
            raise RuntimeError(
                f"{context} 存在 {invalid_count} 条无法解析的 date，拒绝进入训练"
            )
        result["date"] = parsed
        # 数据库跨月拼接、驱动返回顺序都不能作为训练时序事实源；这里统一稳定升序。
        result.sort_values("date", inplace=True, kind="mergesort")
        result.reset_index(drop=True, inplace=True)
        if not result["date"].is_monotonic_increasing:
            raise RuntimeError(f"{context} 按 date 排序后仍非单调递增")
        return result

    @staticmethod
    def _parse_activation_time(value):
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
        """返回初次/增量训练统一的数据源和数据量配置。"""
        cfg = self.process_config.training
        normalized = str(mode).strip().lower()
        if normalized == 'initial':
            return {
                'mode': 'initial',
                'source': str(cfg.initial_data_source).strip().lower(),
                'source_csv': cfg.initial_source_csv,
                'days': int(cfg.initial_training_days),
                'minimum_records': int(cfg.initial_minimum_records),
                'database_record_limit': int(cfg.initial_database_record_limit),
                'use_model_result_table': bool(cfg.initial_database_use_model_result_table),
                'work_csv': cfg.initial_work_csv,
            }
        if normalized == 'incremental':
            return {
                'mode': 'incremental',
                'source': str(cfg.incremental_data_source).strip().lower(),
                'source_csv': cfg.incremental_source_csv,
                'days': int(cfg.incremental_training_days),
                'minimum_records': int(cfg.incremental_minimum_records),
                'database_record_limit': int(cfg.incremental_database_record_limit),
                'use_model_result_table': bool(cfg.incremental_database_use_model_result_table),
                'work_csv': cfg.incremental_work_csv,
            }
        raise ValueError(f'未知训练模式: {mode}')

    def _database_target_count(self, settings):
        configured_limit = int(settings.get('database_record_limit', 0))
        if configured_limit > 0:
            return configured_limit
        records_per_day = max(1, int(self.process_config.training.database_records_per_day))
        return max(1, int(settings['days']) * records_per_day)

    def _database_table_names(
        self,
        use_model_result_table=False,
        start_time=None,
        end_time=None,
    ):
        """返回训练可能涉及的月表。

        初次/无 watermark 时保持“本月+上月”兼容行为；增量有 watermark 时按
        watermark 月份一直枚举到当前月份，避免跨月甚至长时间停机后漏数据。
        """
        prefix = (
            self.process_config.persistence.model_result_table_prefix
            if use_model_result_table
            else self.process_config.persistence.filter_table_prefix
        )
        if start_time is None:
            now = datetime.datetime.now()
            current = f"{prefix}{now.year}_{now.month}"
            if now.month == 1:
                previous = f"{prefix}{now.year - 1}_12"
            else:
                previous = f"{prefix}{now.year}_{now.month - 1}"
            return [current, previous]

        start = pd.Timestamp(start_time).to_period("M")
        end = pd.Timestamp(end_time or datetime.datetime.now()).to_period("M")
        if end < start:
            return []
        periods = pd.period_range(start=start, end=end, freq="M")
        return [f"{prefix}{period.year}_{period.month}" for period in periods]

    def _database_table_exists(self, table_name):
        sql = """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            )
        """
        return bool(self.engine.execute(sql, (table_name,)).scalar())

    def _count_recent_database_records(
        self,
        settings,
        *,
        since_time=None,
        until_time=None,
    ):
        target_count = self._database_target_count(settings)
        available = 0
        until = pd.Timestamp(until_time or datetime.datetime.now())
        tables = self._database_table_names(
            settings["use_model_result_table"],
            start_time=since_time,
            end_time=until,
        )
        for table_name in tables:
            try:
                if not self._database_table_exists(table_name):
                    continue
                if since_time is None:
                    row = self.engine.execute(
                        f"SELECT COUNT(*) FROM {table_name}"
                    ).fetchone()
                else:
                    row = self.engine.execute(
                        f"SELECT COUNT(*) FROM {table_name} WHERE date > %s AND date <= %s",
                        (pd.Timestamp(since_time).to_pydatetime(), until.to_pydatetime()),
                    ).fetchone()
                available += int(row[0]) if row else 0
                # readiness 只需要知道是否达到一个训练周期的数据量，不必继续全表计数。
                if available >= target_count:
                    break
            except Exception as exc:
                logging.warning("统计训练表 %s 失败: %s", table_name, exc)
        return min(available, target_count), target_count

    def get_recent_days_data(
        self,
        day,
        use_model_result_table=False,
        record_limit=None,
        minimum_ratio=None,
        since_time=None,
        until_time=None,
    ):
        """读取数据库训练数据；有 watermark 时读取其后的全部新增数据。

        无论数据库自身返回顺序如何，最终都统一按 date 升序稳定排序后返回。
        """
        try:
            settings = {
                "days": int(day),
                "database_record_limit": int(record_limit or 0),
                "use_model_result_table": bool(use_model_result_table),
            }
            target_count = self._database_target_count(settings)
            ratio = (
                float(self.process_config.training.database_minimum_data_ratio)
                if minimum_ratio is None
                else float(minimum_ratio)
            )
            minimum_required = max(1, int(target_count * ratio))
            frames = []
            until = pd.Timestamp(until_time or datetime.datetime.now())
            tables = self._database_table_names(
                use_model_result_table,
                start_time=since_time,
                end_time=until,
            )

            # 无 watermark 的初次训练仍按目标条数截取；有 watermark 的增量必须把
            # watermark 后的所有新增数据读全，不能因“最近3天条数”截断追赶数据。
            remaining = None if since_time is not None else target_count
            for table_name in tables:
                if remaining is not None and remaining <= 0:
                    break
                try:
                    if not self._database_table_exists(table_name):
                        logging.warning("训练数据表不存在: %s", table_name)
                        continue
                    params = []
                    clauses = []
                    if since_time is not None:
                        clauses.append("date > %s")
                        params.append(pd.Timestamp(since_time).to_pydatetime())
                    clauses.append("date <= %s")
                    params.append(until.to_pydatetime())
                    sql = f"SELECT * FROM {table_name} WHERE " + " AND ".join(clauses)
                    # ORDER BY 只是数据库侧优化；P4PC 返回前仍会再次强制排序。
                    sql += " ORDER BY date ASC"
                    if remaining is not None:
                        sql += f" LIMIT {int(remaining)}"
                    result = self.engine.execute(sql, tuple(params))
                    rows = result.fetchall()
                    if rows:
                        frame = pd.DataFrame(rows, columns=result.keys())
                        frames.append(frame)
                        if remaining is not None:
                            remaining -= len(frame)
                        logging.info("从 %s 读取训练数据 %s 条", table_name, len(frame))
                except Exception as exc:
                    logging.warning("读取训练数据表 %s 失败: %s", table_name, exc)

            if not frames:
                logging.warning("数据库未取得训练数据")
                return None

            df = pd.concat(frames, ignore_index=True, sort=False)
            df = self._normalize_training_dataframe(df, context="database training data")
            if since_time is None and len(df) > target_count:
                df = df.tail(target_count).reset_index(drop=True)
                df = self._normalize_training_dataframe(df, context="database training tail")
            logging.info(
                "数据库训练取数完成: watermark=%s, requested_cycle_records=%s, actual=%s, minimum_by_ratio=%s, first=%s, last=%s",
                since_time,
                target_count,
                len(df),
                minimum_required,
                df["date"].iloc[0] if not df.empty else None,
                df["date"].iloc[-1] if not df.empty else None,
            )
            if len(df) < minimum_required:
                logging.warning(
                    "数据库训练数据完整率不足: actual=%s, required=%s",
                    len(df),
                    minimum_required,
                )
            return df
        except Exception as exc:
            logging.error("获取训练数据时发生错误: %s", exc)
            traceback.print_exc()
            return None

    def _load_training_data(self, mode):
        settings = self._training_mode_settings(mode)
        source = settings["source"]
        if source not in {"database", "csv"}:
            raise ValueError(
                f"{mode} data_source={source!r} 无效，仅支持 'database' 或 'csv'"
            )

        watermark_time = None
        watermark_version = None
        if str(mode).strip().lower() == "incremental":
            watermark_time, watermark_version = self._active_training_watermark()
            logging.info(
                "增量训练 watermark: version=%s, last_data_timestamp=%s",
                watermark_version,
                watermark_time,
            )

        if source == "csv":
            source_path = self._resolve_training_path(settings["source_csv"])
            if not source_path:
                raise ValueError(f"{mode} 训练已选择 csv，但 source_csv 未配置")
            if not os.path.isfile(source_path):
                raise FileNotFoundError(f"{mode} 训练 CSV 不存在: {source_path}")
            df = pd.read_csv(source_path)
            df = self._normalize_training_dataframe(df, context=f"{mode} source CSV")
            if watermark_time is not None:
                df = df[df["date"] > watermark_time].copy().reset_index(drop=True)
            logging.info(
                "%s 训练使用指定 CSV: %s, watermark=%s, records=%s",
                mode,
                source_path,
                watermark_time,
                len(df),
            )
            required = settings["minimum_records"]
        else:
            target_count = self._database_target_count(settings)
            df = self.get_recent_days_data(
                day=settings["days"],
                use_model_result_table=settings["use_model_result_table"],
                record_limit=(None if watermark_time is not None else target_count),
                since_time=watermark_time,
                until_time=datetime.datetime.now(),
            )
            ratio_required = int(
                target_count * float(self.process_config.training.database_minimum_data_ratio)
            )
            required = max(settings["minimum_records"], ratio_required)

        if df is None or len(df) < required:
            actual = 0 if df is None else len(df)
            raise RuntimeError(
                f"{mode} 训练数据不足: actual={actual}, required={required}, source={source}, watermark={watermark_time}"
            )
        df = self._normalize_training_dataframe(df, context=f"{mode} final training frame")
        if watermark_time is not None and not df.empty:
            first = pd.Timestamp(df["date"].iloc[0])
            if first <= watermark_time:
                raise RuntimeError(
                    "增量训练数据边界错误：第一条必须严格晚于 active watermark；"
                    f"first={first}, watermark={watermark_time}"
                )
        return df, settings

    def _save_training_work_csv(self, df, settings):
        """训练工作 CSV 的唯一落盘入口；落盘前再次强制按 date 升序。"""
        df = self._normalize_training_dataframe(
            df,
            context=f"{settings['mode']} work CSV",
        )
        output_path = self._resolve_training_path(settings["work_csv"])
        if not output_path:
            raise ValueError(f"{settings['mode']} work_csv 未配置")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        df.to_csv(
            output_path,
            index=False,
            date_format="%Y-%m-%d %H:%M:%S",
        )
        logging.info(
            "%s 训练工作 CSV 已按 date 升序保存: %s, records=%s, first=%s, last=%s",
            settings["mode"],
            output_path,
            len(df),
            df["date"].iloc[0] if not df.empty else None,
            df["date"].iloc[-1] if not df.empty else None,
        )
        return output_path

    def check_data_accumulation(self, mode="initial"):
        """按训练模式检查数据量；增量只统计 active watermark 之后的新数据。"""
        try:
            settings = self._training_mode_settings(mode)
            watermark_time = None
            if str(mode).strip().lower() == "incremental":
                watermark_time, watermark_version = self._active_training_watermark()
                logging.info(
                    "检查增量数据积累: active=%s, watermark=%s",
                    watermark_version,
                    watermark_time,
                )

            if settings["source"] == "csv":
                source_path = self._resolve_training_path(settings["source_csv"])
                if not source_path or not os.path.isfile(source_path):
                    logging.info("%s 训练 CSV 未就绪: %s", mode, source_path)
                    return False
                frame = pd.read_csv(source_path)
                frame = self._normalize_training_dataframe(
                    frame, context=f"{mode} accumulation CSV"
                )
                if watermark_time is not None:
                    frame = frame[frame["date"] > watermark_time]
                count = len(frame)
                required = settings["minimum_records"]
            elif settings["source"] == "database":
                count, target = self._count_recent_database_records(
                    settings,
                    since_time=watermark_time,
                    until_time=datetime.datetime.now(),
                )
                required = max(
                    settings["minimum_records"],
                    int(target * float(self.process_config.training.database_minimum_data_ratio)),
                )
            else:
                logging.error("%s 训练数据源无效: %s", mode, settings["source"])
                return False
            logging.info(
                "%s 训练数据检查: source=%s, watermark=%s, actual=%s, required=%s, cycle_days=%s",
                mode,
                settings["source"],
                watermark_time,
                count,
                required,
                settings["days"],
            )
            return count >= required
        except Exception as exc:
            logging.error("检查 %s 训练数据时发生错误: %s", mode, exc)
            return False

    def insert_data(self, data):
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
        """模型推理和结果处理：使用新的 condition + slurry_policy 集成在线入口。"""
        str_time = data.get("date", pd.Timestamp.now())
        if self.system_state == self.SystemState.NORMAL_OPERATION:
            if self._ensure_slurry_pipeline():
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
        else:
            result = {}

        result["date"] = str_time
        result["model_seq"] = data.get("_snapshot_seq", -1)
        result["_write_target"] = self.process_config.persistence.model_write_target
        result["_is_valid"] = store_to_db
        if not store_to_db:
            result["_invalid_reason"] = "数据验证失败"
            logging.info("数据无效，推理结果将跳过写库")
        else:
            logging.debug("推理结果已标记写入 t_model_result_")

        if result:
            send_copy = {k: v for k, v in result.items() if not str(k).startswith('_')}
            self.send_data = send_copy
            self.result = send_copy
            # 新核心完整输出先放到 GLOBAL_DATA；数据库/前端字段后续再单独适配。
            self._publish_map_control(send_copy)
            self.send()
            logging.info(
                "供浆模型结果: condition=%s, action=%s, magnitude=%s, version=%s",
                send_copy.get("condition_label"),
                send_copy.get("slurry_policy_action_family"),
                send_copy.get("slurry_policy_action_magnitude"),
                send_copy.get("integrated_active_version"),
            )
        return result

    def check_incremental_training(self):
        """按配置周期检查是否需要进行增量训练；CSV 模式也不绕过周期。"""
        try:
            now = datetime.datetime.now()
            interval_days = int(
                self.process_config.training.incremental_trigger_interval_days
            )
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
                logging.warning('已到增量训练周期，但配置的数据源尚未达到数据量要求')
                return False
            logging.info(
                '检测到需要增量训练: interval_days=%s, source=%s',
                interval_days,
                self.process_config.training.incremental_data_source,
            )
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
        """
        实时发送数据到DCS系统，使用update方式更新GLOBAL_DATA
        
        Args:
            realtime_data: 包含实时计算结果的字典
        """
        try:
            # 添加时间戳标记
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
            
            logging.debug("已发送实时数据到DCS系统，使用update方式")
        except Exception as e:
            logging.error(f"发送实时数据到DCS失败: {str(e)}")
            traceback.print_exc()
    def send_to_ws(self):
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

    def send(self):
        try:
            # todo: 待处理，
            # self.udp_server_client.sendto(self.send_data.encode("utf-8"), ('localhost', 28847))
            pass
        except Exception as e:
            traceback.print_exc()
            logging.error("send 方法产生了异常 为" + str(e))

    def add_data_to_databases(self, data):
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

    def limiter(self, data, limit):
        try:
            # 遍历所有变量进行限幅处理
            for var in data.index:
                if var in limit.index:
                    if data.loc[var] < limit.loc['min', var]:
                        data.loc[var] = limit.loc['min', var]
                    if data.loc[var] > limit.loc['max', var]:
                        data.loc[var] = limit.loc['max', var]
            return data
        except Exception as e:
            traceback.print_exc()
            return data

    def outliers_threshold(self, df, low, high):
        # 计算分位数
        quant_df = df.quantile([low, high])
        Q1 = quant_df.iloc[0]  # 上四分位数
        Q3 = quant_df.iloc[1]  # 下四分位数
        return Q3, Q1

    def data_cal_no_fentch(self, sql, parem):
        try:
            self.engine.execute(sql, parem)
        except Exception as ex:
            traceback.print_exc()
            print(str(ex))

    def data_cal(self, sql, parem):
        return self.engine.execute(sql, parem).fetchall()

    def clean_data(self, message):
        """实时预处理：保留上游所有附加字段并传给新的在线核心。"""
        try:
            if not message:
                logging.warning("没有有效数据")
                return None
            msg = dict(message[0])
            # 先复制整帧，避免只按 self.titles 取值时把后续新增现场字段丢掉。
            row_dict = dict(msg)
            if "date" in msg:
                try:
                    row_dict["date"] = pd.to_datetime(msg["date"])
                except Exception as e:
                    logging.error(f"日期转换失败: {str(e)}")
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
                    # 非数值字段不覆盖为0，保持上游原始值继续透传。
                    pass
            # 透传校验码供 DataValidator 使用（不依赖 titles）
            try:
                row_dict["jym"] = int(msg.get("jym", self.process_config.data_validation.default_jym))
            except Exception:
                row_dict["jym"] = int(self.process_config.data_validation.default_jym)
            index = row_dict["date"]
            data_dict = {k: v for k, v in row_dict.items() if k != "date"}
            # 2. 滤波
            filtered_data = self.data_preprocessor.filter_realtime_data(data_dict)
            filtered_data["date"] = index
            # 3. 特征生成
            try:
                realtime_data = self.data_preprocessor.generate_features(filtered_data)
            except Exception as e:
                logging.error(f"基础特征生成失败: {str(e)}")
                realtime_data = filtered_data.copy()
            # 再次补回未被预处理器认识的上游字段，保证后续添加字段无需修改这里。
            for key, value in msg.items():
                if key != "date":
                    realtime_data.setdefault(key, value)
            # SO2处理
            # try:
            #     if "jyq_SO2" in realtime_data and "jyq_LL" in realtime_data:
            #         so2_result = self.so2_processor.process_data(realtime_data)
            #         if so2_result:
            #             realtime_data.update({"M0": so2_result.get("M0", 0), "M1_daily": so2_result.get("M1_daily", 0), "M1_monthly": so2_result.get("M1_monthly", 0)})
            #         else:
            #             realtime_data.update({"M0": 0, "M1_daily": 0, "M1_monthly": 0})
            #     else:
            #         realtime_data.update({"M0": 0, "M1_daily": 0, "M1_monthly": 0})
            # except Exception as e:
            #     logging.error(f"SO2处理失败: {str(e)}")
            #     realtime_data.update({"M0": 0, "M1_daily": 0, "M1_monthly": 0})
            # 基准供浆量和氧化风机流量计算
            # try:
            #     yhfj_result = self.yhfj_process_control.process_data(realtime_data)
            #     if yhfj_result:
            #         realtime_data.update({"xst_base_flow": yhfj_result.get("xst_base_flow"), "apt_base_flow": yhfj_result.get("apt_base_flow"), "xst_fan_flow_mode1": yhfj_result.get("xst_fan_flow_mode1"), "apt_fan_flow_mode1": yhfj_result.get("apt_fan_flow_mode1"), "xst_fan_flow_mode2": yhfj_result.get("xst_fan_flow_mode2"), "apt_fan_flow_mode2": yhfj_result.get("apt_fan_flow_mode2")})
            #     else:
            #         realtime_data.update({"xst_base_flow": 0, "apt_base_flow": 0, "xst_fan_flow_mode1": 0, "apt_fan_flow_mode1": 0, "xst_fan_flow_mode2": 0, "apt_fan_flow_mode2": 0})
            # except Exception as e:
            #     logging.error(f"基准供浆量和氧化风机流量计算失败: {str(e)}")
            #     realtime_data.update({"xst_base_flow": 0, "apt_base_flow": 0, "xst_fan_flow_mode1": 0, "apt_fan_flow_mode1": 0, "xst_fan_flow_mode2": 0, "apt_fan_flow_mode2": 0})
            # 电耗计算
            # try:
            #     power_result = self.tower_power_calculator.calculate_tower_power(realtime_data)
            #     if power_result:
            #         realtime_data.update(power_result)
            # except Exception as e:
            #     logging.error(f"电耗计算失败: {str(e)}")
            # 成本计算
            # try:
            #     cost_result = self.cost_calculator.calculate_cost(realtime_data)
            #     if cost_result:
            #         realtime_data.update(cost_result)
            # except Exception as e:
            #     logging.error(f"成本计算失败: {str(e)}")
            # 控制回路计算
            # try:
            #     control_stats = self.stat_calc.calculate_stats(realtime_data)
            #     if control_stats:
            #         realtime_data.update(control_stats)
            # except Exception as e:
            #     logging.error(f"控制回路计算失败: {str(e)}", exc_info=True)
            # 实时发送
            try:
                rt_date = realtime_data.get("date")
                if rt_date is None or rt_date == "":
                    realtime_data["date"] = index.strftime("%Y-%m-%d %H:%M:%S")
                elif hasattr(rt_date, "strftime"):
                    realtime_data["date"] = rt_date.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    realtime_data["date"] = str(rt_date)
                realtime_seq = self._update_latest_snapshot(realtime_data)
                self.send_realtime_to_dcs(realtime_data, realtime_seq=realtime_seq)
            except Exception as e:
                logging.error(f"实时发送DCS数据失败: {str(e)}")
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
        if not os.path.isfile(paths["snapshot"]):
            raise FileNotFoundError("第一模块初次训练未生成快照: %s" % paths["snapshot"])
        if not os.path.isfile(output_csv):
            raise FileNotFoundError("第一模块初次训练未生成标注CSV: %s" % output_csv)
        return output_csv, paths["snapshot"]

    def _run_condition_incremental(self, python_exe, env, training_csv, active_version, target_version):
        base_snapshot = self._condition_paths_for_version(active_version)["snapshot"]
        target_paths = self._condition_paths_for_version(target_version)
        output_csv = self._core_path("incremental_condition_output_csv")
        script = self._core_path("condition_incremental_script")
        if not os.path.isfile(base_snapshot):
            raise FileNotFoundError("当前激活版本缺少第一模块快照: %s" % base_snapshot)
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
        if not os.path.isfile(target_paths["snapshot"]):
            raise FileNotFoundError("第一模块增量训练未生成快照: %s" % target_paths["snapshot"])
        if not os.path.isfile(output_csv):
            raise FileNotFoundError("第一模块增量训练未生成标注CSV: %s" % output_csv)
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

    def _run_policy_incremental(self, python_exe, env, labeled_csv, condition_snapshot, active_version):
        script = self._core_path("slurry_policy_incremental_script")
        previous = Path(self._core_path("slurry_policy_output_root")) / "snapshots" / active_version
        if not previous.is_dir():
            raise FileNotFoundError("当前激活版本缺少第二模块快照: %s" % previous)
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
        """按配置数据源执行 condition_model + slurry_policy_model 两模块训练。"""
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
                        '初次 condition_model + slurry_policy_model 训练完成并激活: %s',
                        version,
                    )
                    return

                if self.system_state != self.SystemState.NORMAL_OPERATION:
                    logging.warning('当前系统状态不是正常运行，禁止增量训练')
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
                    active_version,
                    target_version,
                )
        except Exception as exc:
            logging.error('供浆核心 %s 训练失败: %s', mode, exc)
            traceback.print_exc()
            if mode == 'initial':
                self.model_training_completed = False
                self.system_state = self.SystemState.DATA_COLLECTION
            # 增量失败时 active_version.json 不变，在线继续使用旧版本。
            raise
        finally:
            self.is_training = False

    def hot_update_models(self, target_version=None):
        """原子激活第一/第二模块同版本对，不再复制Q-learning/PH模型目录。"""
        try:
            if target_version:
                _, env = self._training_env()
                python_exe = config.get('python_exe', 'python')
                script = self._core_path("slurry_policy_activate_script")
                self._run_training_command(
                    'activate-integrated-version',
                    [
                        python_exe, script,
                        '--version', str(target_version),
                        '--config', self._core_path("slurry_policy_config"),
                    ],
                    env,
                    cwd=os.path.dirname(script),
                )
            # 初次启动没有在线对象时立即加载；增量时保留当前对象，让其在下一次
            # evaluate 中读取 active_version.json 并原子切换，保留 WAITING_EFFECT 等状态。
            if self._slurry_pipeline is None:
                return self.reload_models()
            logging.info(
                'active_version.json 已更新为 %s，在线Pipeline将在后续模型周期原子切换',
                target_version or self._read_active_version(),
            )
            return True
        except Exception as e:
            logging.error(f"模型热更新失败: {str(e)}")
            traceback.print_exc()
            return False

    def clean_model_backups(self, days=14):
        """
        清理超过指定天数的模型备份

        Args:
            days: 保留的天数，默认14天
        """
        try:
            # 模型备份目录
            backup_root = self._resolve_training_path(
                self.process_config.training.model_backup_dir
            )
            if not os.path.exists(backup_root):
                print(f"备份目录不存在: {backup_root}")
                return

            # 当前时间
            current_time = time.time()
            # 阈值时间（秒）
            threshold = days * 24 * 3600

            # 统计信息
            deleted_dirs = 0
            total_dirs = 0

            # 遍历备份目录
            for backup_dir in os.listdir(backup_root):
                total_dirs += 1
                dir_path = os.path.join(backup_root, backup_dir)

                # 只处理目录
                if not os.path.isdir(dir_path):
                    continue

                # 获取目录的修改时间
                dir_time = os.path.getmtime(dir_path)

                # 如果目录超过保留天数，则删除
                if (current_time - dir_time) > threshold:
                    try:
                        shutil.rmtree(dir_path)
                        deleted_dirs += 1
                        print(f"已删除过期备份目录: {backup_dir}")
                    except Exception as e:
                        print(f"删除目录失败 {dir_path}: {str(e)}")

            print(f"备份清理完成: 共{total_dirs}个目录，删除了{deleted_dirs}个过期目录")

        except Exception as e:
            print(f"清理模型备份时出错: {str(e)}")
            traceback.print_exc()
        
    def reload_models(self):
        """重新加载 active_version.json 指向的 condition + policy 集成在线Pipeline。"""
        try:
            from system.model.map_control.condition_model.online_condition_classifier import (
                build_online_condition_policy_pipeline,
            )
            candidate = build_online_condition_policy_pipeline(
                snapshot_path='active',
                integration_config=self._integration_config(),
            )
            with self._slurry_pipeline_lock:
                self._slurry_pipeline = candidate
                self._slurry_pipeline_error = None
            logging.info('condition_model + slurry_policy_model 集成在线Pipeline加载完成')
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
                'integrated slurry pipeline unavailable: %s'
                % (self._slurry_pipeline_error or 'UNKNOWN')
            )
        return dict(self._slurry_pipeline.record_execution(dict(feedback)))
    
    def check_system_state(self):
        """定期检查系统状态"""
        while True:
            
            print(f"check_system_state: 当前系统状态: {self.system_state}")
        
            try:
                current_time = datetime.datetime.now()
                
                #with self.training_lock:
                    # 1. 数据收集阶段
                if self.system_state == self.SystemState.DATA_COLLECTION:
                    print("【系统状态】当前阶段：数据收集阶段")
                    if self.check_data_accumulation() and not self.is_training:
                        self.system_state = self.SystemState.MODEL_TRAINING
                        print("【系统状态】切换到：模型训练阶段")
                        #self.trigger_model_training()
                        self.training_event.set()
                
                # 2. 模型训练阶段
                elif self.system_state == self.SystemState.MODEL_TRAINING:
                    print("【系统状态】当前阶段：模型训练阶段")
                    if self.model_training_completed:
                        self.system_state = self.SystemState.NORMAL_OPERATION
                        print("【系统状态】切换到：正常运行阶段")

                # 3. 正常运行阶段
                elif self.system_state == self.SystemState.NORMAL_OPERATION:
                    print("【系统状态】当前阶段：正常运行阶段")
                    # 检查是否需要增量训练
                    if not self.is_training:
                        self.check_incremental_training()

                
                time.sleep(float(self.process_config.runtime.state_check_interval_seconds))
                
            except Exception as e:
                logging.error(f"系统状态检查发生错误: {str(e)}")
                time.sleep(60)