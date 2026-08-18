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

    buffer_size: int = 60  # 变化率缓存最大样本数；当前按约 1 秒/帧理解为 1 分钟。
    change_rate_window_seconds: float = 60.0  # 计算字段变化量时默认回看窗口，单位：秒。
    calibration_detection_window: int = 600  # 校验波动识别缓存长度/检测窗口，当前约 10 分钟。
    calibration_min_samples: int = 10  # 开始判断校验波动前要求的最少样本数。
    calibration_cooldown_seconds: float = 600.0  # 检出校验后保持校验态的时间，单位：秒。
    calibration_event_count_threshold: int = 2  # 检测窗口内至少出现多少次大幅波动才判校验。
    ph_change_threshold: float = 0.2  # pH 变化阈值，保留供扩展验证规则使用。
    jyq_so2_change_threshold: float = 20.0  # 净烟气 SO2 单次大幅变化阈值，单位：mg/Nm3。
    jyq_so2_value_threshold: float = 60.0  # 净烟气 SO2 数据有效性上限，单位：mg/Nm3。
    yyq_so2_change_threshold: float = 500.0  # 原烟气 SO2 变化阈值，保留供扩展验证规则使用。
    calibration_code: int = 100  # jym 等于该值时认为处于测点校验状态。
    default_jym: int = 50  # 数据缺少 jym 时使用的正常运行默认值。


@dataclass(frozen=True)
class UnitStopConfig:
    """机组停机判定配置。

    ``field`` 可改成 ``yyq_SO2``、``jzfh``、``glfl`` 或其他已进入实时数据的数值字段。

    ``comparison`` 支持以下比较方式：

    - ``lt``：小于，表示 ``field < threshold``；
    - ``le``：小于等于，表示 ``field <= threshold``；
    - ``gt``：大于，表示 ``field > threshold``；
    - ``ge``：大于等于，表示 ``field >= threshold``；
    - ``eq``：等于，表示 ``field == threshold``；
    - ``ne``：不等于，表示 ``field != threshold``。

    只有条件连续满足 ``hold_seconds`` 后才判停机；中途恢复会清零计时。
    """

    enabled: bool = True  # 是否启用停机判定；False 表示不因该规则跳过模型推理/写库。
    field: str = 'yyq_SO2'  # 停机判断字段；钢厂当前使用原烟气 SO2。
    comparison: str = 'lt'  # lt小于；le小于等于；gt大于；ge大于等于；eq等于；ne不等于。
    threshold: float = 500.0  # 停机阈值；单位与 field 对应测点一致。
    hold_seconds: float = 5 * 60  # 条件连续保持多久才判停机，单位：秒。
    invalid_value_resets_timer: bool = True  # 字段缺失/NaN/Inf 时是否中断连续计时。


@dataclass(frozen=True)
class RuntimeConfig:
    """线程、队列、快照与通讯运行参数。"""

    initial_system_state: str = 'data_collection'  # 启动状态：data_collection/model_training/normal_operation。
    insert_queue_size: int = 50  # 兼容旧 insert_data 队列容量。
    data_queue_size: int = 60  # 原始实时帧处理队列容量。
    db_queue_size: int = 200  # 过滤数据和模型结果待写库队列容量。
    filter_writer_workers: int = 1  # 过滤数据写库线程数；建议保持 1 保证顺序。
    model_writer_workers: int = 1  # 模型结果写库线程数；建议保持 1 保证顺序。
    snapshot_interval_seconds: float = 30.0  # 过滤数据/模型结果快照输出周期，单位：秒。
    snapshot_poll_interval_seconds: float = 0.2  # 快照调度线程空轮询间隔，单位：秒。
    snapshot_error_retry_seconds: float = 1.0  # 快照调度异常后的重试等待，单位：秒。
    consume_min_interval_seconds: float = 1.0  # consume_data 两次成功入队的最小间隔，单位：秒。
    consume_duplicate_sleep_seconds: float = 0.05  # 检出重复帧后的短暂休眠，单位：秒。
    consume_idle_sleep_seconds: float = 0.1  # 没有新数据时的休眠，单位：秒。
    offline_grace_seconds: float = 30.0  # 通讯中断后允许继续用最近历史数据推理的时间，单位：秒。
    processing_queue_timeout_seconds: float = 0.5  # processing_loop 等待数据的超时，单位：秒。
    maintenance_interval_seconds: float = 300.0  # 内存队列维护与状态日志周期，单位：秒。
    state_check_interval_seconds: float = 300.0  # 系统训练状态检查周期，单位：秒。
    training_worker_error_retry_seconds: float = 5.0  # 训练线程异常后的重试等待，单位：秒。
    global_data_maxlen: int = 3600  # GLOBAL_DATA['data'] 最多保留的帧数。
    stat_calc_comm_t: float = 1.0  # PHStatCalc 的通讯采样周期参数。


@dataclass(frozen=True)
class TrainingConfig:
    """自动初次训练、增量训练与测试数据源配置。

    路径既支持绝对路径，也支持相对项目根目录的路径。
    ``initial_data_source`` / ``incremental_data_source`` 仅支持 ``database`` 和 ``csv``。
    使用 CSV 只改变最前端训练数据来源，不会改变后续训练链：
    原始数据统一落盘为工作 CSV -> condition_model -> 带 condition_label 的 CSV -> slurry_policy_model。

    condition_model / slurry_policy_model 的训练脚本、snapshot 目录和 active_version.json
    统一由 ``slurry_core_bridge_config.py`` 管理，这里不再保留旧 cluster / Q-learning /
    PH_predict / model_backup 路径配置。
    """

    # 初次训练数据来源与数据量。
    initial_data_source: str = 'csv'  # database=从数据库读取；csv=读取 initial_source_csv。
    initial_source_csv: str = 'F:\tlgj_new\files\new_data_train_30s.csv'  # 初次训练测试 CSV；仅 initial_data_source='csv' 时使用。
    initial_training_days: int = 7  # 数据库模式下初次训练回看的天数。
    initial_minimum_records: int = 2880*7*0.9  # 初次训练最终允许启动的最少记录数；测试时可调小。
    initial_database_record_limit: int = 2880*7  # 数据库最多读取条数；0 表示按 initial_training_days 自动计算。
    initial_database_use_model_result_table: bool = False  # False=过滤数据表t_data1_filter_，True=模型结果表t_model_result_。
    initial_work_csv: str = 'system/model/map_control/model_csv/Initial_train.csv'  # 初次训练统一工作 CSV；database/csv 均先落盘到这里，再交给 condition_model。

    # 增量训练周期、数据来源与数据量。
    incremental_trigger_interval_days: int = 3  # 距离上次训练达到多少天后触发；CSV 测试也遵守该周期。
    incremental_data_source: str = 'database'  # database=从数据库读取；csv=读取 incremental_source_csv。
    incremental_source_csv: str = ''  # 增量训练测试 CSV；仅 incremental_data_source='csv' 时使用。
    incremental_training_days: int = 3  # 增量周期期望数据量对应天数；正式增量实际从 active watermark 之后读取全部新数据。
    incremental_minimum_records: int = 3*2880*0.9  # 增量训练最终允许启动的最少记录数。
    incremental_database_record_limit: int = 0  # 无 watermark/兼容取数时的条数上限；正式增量有 watermark 时不会截断未学习的新数据。
    incremental_database_use_model_result_table: bool = False  # False=过滤数据表，True=模型结果表。
    incremental_work_csv: str = 'system/model/map_control/model_csv/Update_train.csv'  # 增量训练统一工作 CSV；database/csv 均先落盘到这里，再交给 condition_model。

    # 数据库取数换算与完整率。
    database_records_per_day: int = 2880  # 30 秒一条时每天 2880 条；用于 record_limit=0 的自动计算。
    database_minimum_data_ratio: float = 0.90  # 数据库模式要求至少取得目标条数的比例。


@dataclass(frozen=True)
class PersistenceConfig:
    """数据库分表命名与写入标记。"""

    filter_table_prefix: str = 't_data1_filter_rt_'  # 实时过滤数据月表前缀。
    model_result_table_prefix: str = 't_model_result_'  # 模型结果月表前缀。
    filter_write_target: str = 'filter'  # db_queue 中实时过滤数据的目标标记。
    model_write_target: str = 'model_result'  # db_queue 中模型结果的目标标记。


# P4PC 内部兼容字段，不是现场输入字段白名单，也不需要换厂时配置。
# 现场通讯传入的其他字段由 clean_data 整帧复制并继续透传。
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
