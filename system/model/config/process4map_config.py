"""Process4MapControl 专用配置。

本文件集中管理 ``system/model/Process4MapControl.py`` 的运行/训练参数。

厂级现场字段、单塔/双塔、阀门和泵拓扑不在这里重复配置：
- 通讯层负责把现场点位映射成系统使用的字段名，并将完整数据帧写入 GLOBAL_DATA；
- P4PC 对通讯层传入的完整 dict 做透传和预处理，不维护现场字段白名单；
- 真正随厂变化的物理/信号拓扑统一由 ``plant_config.py`` 管理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass(frozen=True)
class DataValidationConfig:
    """实时数据有效性与校验态识别参数。"""

    buffer_size: int = 60
    change_rate_window_seconds: float = 60.0
    calibration_detection_window: int = 600
    calibration_min_samples: int = 30
    calibration_cooldown_seconds: float = 600.0
    calibration_event_count_threshold: int = 2
    ph_change_threshold: float = 0.2
    jyq_so2_change_threshold: float = 20.0
    jyq_so2_value_threshold: float = 60.0
    yyq_so2_change_threshold: float = 500.0
    calibration_code: int = 100
    default_jym: int = 50


@dataclass(frozen=True)
class UnitStopConfig:
    """机组停机判定配置。

    ``field`` 可改成 ``yyq_SO2``、``jzfh``、``glfl`` 或其他已进入实时数据的数值字段。
    ``comparison`` 支持 lt/le/gt/ge/eq/ne。只有条件连续满足 ``hold_seconds``
    后才判停机；中途恢复会清零计时。
    """

    enabled: bool = True
    field: str = 'yyq_SO2'
    comparison: str = 'lt'
    threshold: float = 500.0
    hold_seconds: float = 5 * 60
    invalid_value_resets_timer: bool = True


@dataclass(frozen=True)
class SnapshotAggregationConfig:
    """10秒模型/写库快照的聚合语义。

    实时 ``clean_data`` 仍按1秒处理和发布；这里只控制低频快照如何从最近若干个
    已预处理实时帧中形成。默认数值过程量取均值，离散语义字段严格取末帧。
    """

    window_size: int = 3
    latest_value_fields: Tuple[str, ...] = (
        'date',
        'id',
        'jym',
        'connection_status',
        'outlet_so2_target',
    )
    latest_value_prefixes: Tuple[str, ...] = ('_',)
    latest_value_suffixes: Tuple[str, ...] = (
        '_status',
        '_id',
        '_code',
        '_seq',
    )


@dataclass(frozen=True)
class RuntimeConfig:
    """线程、队列、快照与通讯运行参数。"""

    initial_system_state: str = 'data_collection'
    insert_queue_size: int = 50
    data_queue_size: int = 60
    db_queue_size: int = 200
    filter_writer_workers: int = 1
    model_writer_workers: int = 1
    snapshot_interval_seconds: float = 10.0
    snapshot_poll_interval_seconds: float = 0.2
    snapshot_error_retry_seconds: float = 1.0
    consume_min_interval_seconds: float = 1.0
    consume_duplicate_sleep_seconds: float = 0.05
    consume_idle_sleep_seconds: float = 0.1
    offline_grace_seconds: float = 30.0
    processing_queue_timeout_seconds: float = 0.5
    maintenance_interval_seconds: float = 300.0
    state_check_interval_seconds: float = 300.0
    training_worker_error_retry_seconds: float = 5.0
    global_data_maxlen: int = 3600
    stat_calc_comm_t: float = 1.0


@dataclass(frozen=True)
class TrainingConfig:
    """第一模块 + MFAC 第二模块的统一离线训练生命周期。

    CSV 仅改变最前端数据来源，不改变训练顺序：

    原始训练数据
      -> condition_model 生成 ConditionSnapshot vN 和带工况标签 CSV
      -> MFAC HistoricalEpisodeEngine / historical prior 训练
      -> 两个模块同版本原子激活。

    周期离线重训统一按7天触发。该周期只负责刷新 ConditionSnapshot 和 MFAC
    historical prior；在线 MFAC 的 phi/confidence 仍由有效因果响应事件独立递推，
    不等待7天，也不会被同版本离线 prior 反复覆盖。
    """

    # 初次训练：连续7天形成第一版 condition + MFAC historical prior。
    initial_data_source: str = 'csv'
    initial_source_csv: str = 'F:/tlgj_new/files/new_data_train_10s.csv'
    initial_training_days: int = 7
    initial_minimum_records: int = 54_432
    initial_database_record_limit: int = 60_480
    initial_database_use_model_result_table: bool = False
    initial_work_csv: str = 'system/model/map_control/model_csv/Initial_train.csv'

    # 周期版本刷新：每7天先训练新 ConditionSnapshot，再训练同版本 MFAC。
    # 有 active watermark 时实际读取 watermark 之后全部未学习新数据，不人为截断。
    incremental_trigger_interval_days: int = 7
    incremental_data_source: str = 'database'
    incremental_source_csv: str = ''
    incremental_training_days: int = 7
    incremental_minimum_records: int = 54_432
    incremental_database_record_limit: int = 0
    incremental_database_use_model_result_table: bool = False
    incremental_work_csv: str = 'system/model/map_control/model_csv/Update_train.csv'

    # 10秒一条：8640条/天；完整率门槛仍为90%。
    database_records_per_day: int = 8_640
    database_minimum_data_ratio: float = 0.90


@dataclass(frozen=True)
class PersistenceConfig:
    """数据库分表命名与写入标记。"""

    filter_table_prefix: str = 't_data1_filter_rt_'
    model_result_table_prefix: str = 't_model_result_'
    filter_write_target: str = 'filter'
    model_write_target: str = 'model_result'


_P4PC_INTERNAL_FRAME_FIELDS: Tuple[str, ...] = ('date',)


DEFAULT_LIMITS: Dict[str, Dict[str, float | str]] = {
    'yyq_SO2': {'min': 0.0, 'max': 1.207110e5, 'comments': '原烟气SO2'},
    'jyq_LL': {'min': 1.690000e6, 'max': 1.730000e6, 'comments': '净烟气流量'},
    'xst_YW': {'min': 0.0, 'max': 1.021710e3, 'comments': '吸收塔液位'},
    'xstshsjy_LL': {'min': 0.0, 'max': 1.852200e2, 'comments': '吸收塔石灰石浆液流量'},
    'xstjyxhb_ADL': {'min': 0.0, 'max': 1.917120e2, 'comments': '循环泵A电流'},
    'xstjyxhb_BDL': {'min': 0.0, 'max': 1.998170e2, 'comments': '循环泵B电流'},
    'xstjyxhb_CDL': {'min': 0.0, 'max': 1.499540e2, 'comments': '循环泵C电流'},
    'xstjyxhb_DDL': {'min': 0.0, 'max': 1.500460e2, 'comments': '循环泵D电流'},
    'xstjyxhb_EDL': {'min': 0.0, 'max': 1.500460e2, 'comments': '循环泵E电流'},
}


@dataclass(frozen=True)
class Process4MapControlConfig:
    """Process4MapControl 的运行/训练参数入口。"""

    data_validation: DataValidationConfig = field(default_factory=DataValidationConfig)
    unit_stop: UnitStopConfig = field(default_factory=UnitStopConfig)
    snapshot_aggregation: SnapshotAggregationConfig = field(default_factory=SnapshotAggregationConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    persistence: PersistenceConfig = field(default_factory=PersistenceConfig)
    limits: Dict[str, Dict[str, float | str]] = field(
        default_factory=lambda: {key: value.copy() for key, value in DEFAULT_LIMITS.items()}
    )

    @property
    def input_fields(self) -> Tuple[str, ...]:
        """仅供旧 P4PC ``titles`` 初始化兼容；不限制通讯层实际输入字段。"""
        return _P4PC_INTERNAL_FRAME_FIELDS


PROCESS4MAP_CONFIG = Process4MapControlConfig()
