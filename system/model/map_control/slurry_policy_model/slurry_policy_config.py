"""湿法脱硫供浆历史动作响应模型——算法配置。

厂级物理/信号参数不再在本文件重复维护。工况轴、SO2安全范围、
单塔/双塔、pH、供浆流量、供浆泵及监测设备拓扑统一来自：
``system/model/config/plant_config.py``。

本文件只负责第二模块自身的：
- 离线训练与历史动作事件提取；
- 工况归属、邻域经验、全厂先验与扰动识别；
- 动作强度/响应效果/可靠性统计；
- 在线目标流量控制、动作筛选、动作节流和执行反馈；
- 模型版本加载、运行日志和训练产物保存。

注释约定：
- ``minutes`` 表示分钟；``seconds`` 表示秒；``cycles`` 表示在线决策周期数；
- 0~1 小数通常表示比例/权重；百分制评分通常为 0~100；
- ``None`` 通常表示不在本处单独指定，而是继承离线训练结果或中央厂级配置；
- 本次仅补充参数说明，不改变任何现有参数值和算法逻辑。

项目路径按当前仓库根目录自动生成，不需要因为 F:/tlgj、F:/tlgj_new 或部署目录
变化而修改配置。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from system.model.config.plant_config import PLANT_CONFIG as SITE_PLANT_CONFIG


# ============================================================================
# 项目路径
# ============================================================================
CONDITION_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "condition_model"
MODEL_CSV_ROOT = PROJECT_ROOT / "system" / "model" / "map_control" / "model_csv"
POLICY_OUTPUT_ROOT = PROJECT_ROOT / "files" / "slurry_policy_model_output"

# 第二模块使用中央厂级配置的深拷贝，并只补充本模块运行所需的项目路径。
# towers / valves / supply_pumps / pH / SO2 等都不在这里再写一份；标准过程字段名固定。
PLANT_CONFIG = copy.deepcopy(SITE_PLANT_CONFIG)
PLANT_CONFIG["paths"] = {
    # 初次训练默认输入：第一模块初次工况划分完成后的 CSV。
    "default_initial_input": str(MODEL_CSV_ROOT / "Initial_train_after_condition.csv"),
    # 增量训练默认输入：第一模块增量工况划分完成后的 CSV。
    "default_incremental_input": str(MODEL_CSV_ROOT / "Incremental_train_after_condition.csv"),
    # 第二模块版本快照、全厂先验、manifest 等模型产物根目录。
    "output_root": str(POLICY_OUTPUT_ROOT),
    # 第一模块 condition snapshot 根目录，用于版本对齐和工况空间读取。
    "condition_snapshots_dir": str(CONDITION_ROOT / "snapshots"),
    # P4PC 同版本原子激活指针；在线只加载这里正式激活的版本。
    "active_policy_version_file": str(POLICY_OUTPUT_ROOT / "active_version.json"),
    # 在线决策日志、执行日志和运行状态持久化目录。
    "online_runtime_dir": str(PROJECT_ROOT / "files" / "slurry_policy_online_runtime"),
}

# 从中央厂级配置读取净烟气 SO2 安全下限/上限。
_SAFE_SO2_LO, _SAFE_SO2_HI = [
    float(value) for value in PLANT_CONFIG["outlet_so2_safe_range"]
]
# SO2 状态离散边界，共 8 个边界、7 个区间。
# 当安全范围仍为 0~35 时等价于 [0, 5, 10, 15, 20, 25, 30, 35]；
# 安全范围改变时按相同比例自动缩放，避免这里重复硬编码 35。
_OUTLET_SO2_STATE_EDGES = [
    _SAFE_SO2_LO + (_SAFE_SO2_HI - _SAFE_SO2_LO) * index / 7.0
    for index in range(8)
]


# ============================================================================
# 离线训练参数
# ============================================================================
TRAINING_CONFIG = {
    "policy_semantics_version": "ACTUAL_SUPPLY_FLOW_V1",
    # ------------------------------------------------------------------------
    # 训练进度显示。只影响终端输出，不影响算法结果。
    # ------------------------------------------------------------------------
    "progress": {
        # 是否显示训练进度条。
        "enabled": True,
        # 进度条字符宽度。
        "bar_width": 32,
        # 两次进度刷新之间的最短时间，单位：秒。调大可减少控制台刷新开销。
        "min_interval_seconds": 0.20,
        # 是否显示当前阶段已经运行的耗时。
        "show_elapsed": True,
    },

    # ------------------------------------------------------------------------
    # 训练性能优化。原则上只影响速度/内存，不应改变训练语义。
    # ------------------------------------------------------------------------
    "performance": {
        # True：读取 CSV 时尽量只加载训练真正需要的字段，降低内存占用。
        "read_only_required_columns": True,
        # True：若时间已经单调有序，则跳过重复排序。
        "skip_sort_when_already_ordered": True,
        # True：对高频 groupby 键转 categorical，以降低内存并提高分组速度。
        "categorical_groupby_keys": True,
        # 是否记录各训练阶段耗时，便于定位性能瓶颈。
        "record_stage_timings": True,
    },

    # ------------------------------------------------------------------------
    # CSV 输入输出及时间字段解析。
    # ------------------------------------------------------------------------
    "io": {
        # CSV 默认编码；utf-8-sig 可兼容带 BOM 的 Excel/Windows 导出文件。
        "csv_encoding": "utf-8-sig",
        # 时间字符串格式。None 表示交给 pandas 自动解析；固定格式可提高大文件解析速度。
        "timestamp_format": None,
        # 同一时间戳出现重复行时保留哪一条；last=保留最后一条。
        "drop_duplicate_timestamp_keep": "last",
        # True：缺少必要字段立即报错；False 会更宽松，但工业训练不建议静默缺字段。
        "strict_required_columns": True,
    },

    # ------------------------------------------------------------------------
    # 原始历史数据预处理。
    # ------------------------------------------------------------------------
    "preprocessing": {
        # 实际供浆流量的短窗口中值滤波点数，用于抑制单点毛刺。
        # 9个10秒点保持原3个30秒点的90秒中位数窗口，不改变长期趋势。
        "supply_flow_rolling_median_points": 9,
        # 连续时间序列允许的最大断点，单位：秒。
        # 相邻样本间隔超过该值时切断事件段，避免跨长缺口拼接动作/响应。
        "max_continuous_gap_seconds": 180,
        # True：训练字段主动转为数值，非法字符串转 NaN 后按质量规则处理。
        "coerce_numeric": True,
    },

    # ------------------------------------------------------------------------
    # 供浆流量动作事件切片参数。
    # 一次历史动作最终会被切成：基线段 → 动作段 → 响应延迟 → 响应观察段。
    # ------------------------------------------------------------------------
    "episode": {
        # 动作发生前用于计算基线状态的历史窗口，单位：分钟。
        "baseline_minutes": 5.0,
        # 用于确认一次供浆流量动作的检测窗口，单位：分钟。
        "action_detection_window_minutes": 2.0,
        # 单个动作事件允许持续的最大时间，单位：分钟；超过后不再无限扩展动作段。
        "max_action_duration_minutes": 20.0,
        # 供浆流量停止明显变化并持续稳定多久后认为动作结束，单位：分钟。
        "action_end_stable_minutes": 1.5,
        # 两次同方向/连续动作之间小于该间隔时可合并成一个事件，单位：分钟。
        "action_merge_gap_minutes": 1.0,
        # 动作完成后等待过程传递的纯延迟，单位：分钟；该阶段不直接用于主响应评分。
        "response_delay_minutes": 3.0,
        # 延迟之后观察 SO2/pH 等实际效果的窗口长度，单位：分钟。
        "response_window_minutes": 10.0,
        # True：响应观察期内又出现新的供浆流量动作，则原动作响应被判为受干扰。
        "invalidate_followup_action_in_response": True,
        # 一个基线/响应窗口实际有效数据覆盖率下限，0~1。
        # 低于该值说明缺测过多，不宜用于评价动作效果。
        "minimum_window_coverage_ratio": 0.70,
        # 增量训练时额外读取新窗口开始前多少分钟的历史尾部，单位：分钟。
        # 用于识别跨增量边界的动作/响应，不代表重复训练旧数据。
        "incremental_context_tail_minutes": 60.0,
        # 用于识别短时间反向动作的时间尺度，单位：分钟；可辅助判断追调/反复调节。
        "short_reverse_action_minutes": 20.0,
    },

    # ------------------------------------------------------------------------
    # 历史 episode 应归属于哪个工况的规则。
    # ------------------------------------------------------------------------
    "condition_attribution": {
        # 是否启用第一模块工况归属；关闭后会失去按工况学习的核心能力。
        "enabled": True,
        # 动作事件以动作开始时所在工况作为锚点。
        "action_anchor_mode": "ACTION_START",
        # 以下两个键名是历史兼容名称，实际含义分别是“第1工况轴”和“第2工况轴”
        # 允许 episode 在窗口内偏离锚点多少个基础网格，并不再固定代表负荷/SO2。
        "max_load_grid_offset": 2,
        "max_inlet_so2_grid_offset": 3,
        # episode 周围落在允许邻域内的有效样本比例下限，0~1。
        "minimum_neighborhood_coverage_ratio": 0.60,
        # False：仅基础 grid_id 切换本身不直接认定为 transient，还需结合真实扰动趋势。
        "grid_change_alone_is_transient": False,
        # False：仅 condition_label 变化本身不直接认定为 transient。
        "condition_label_change_alone_is_transient": False,
        # 附近网格证据的权重方式：按 episode 的邻域覆盖率进行折算。
        "nearby_evidence_weight_mode": "COVERAGE_RATIO",
    },

    # ------------------------------------------------------------------------
    # 离线状态离散化。用于把连续过程状态转成可统计的状态键。
    # ------------------------------------------------------------------------
    "state": {
        # 净烟气 SO2 状态边界；由中央安全范围自动生成。
        "outlet_so2_edges": _OUTLET_SO2_STATE_EDGES,
        # 净烟气 SO2 慢趋势阈值；用于状态标签，不等同于排放限值。
        "outlet_so2_trend_slow_rate": 0.20,
        # 净烟气 SO2 快趋势阈值；绝对变化率达到该值时进入更强趋势状态。
        "outlet_so2_trend_fast_rate": 0.80,
        # 状态键中是否包含第一模块工况状态信息。
        "include_condition_state_key": True,
        # 状态键中是否包含 NORMAL/SLOW/FAST 等扰动模式。
        "include_disturbance_mode": True,
    },

    "action_magnitude": {"semantics": "ACTUAL_SUPPLY_FLOW_V1"},

    # ------------------------------------------------------------------------
    # 动作后的 SO2 / pH 响应方向、强度和稳定性评价。
    # ------------------------------------------------------------------------
    "response": {
        # SO2 前后变化绝对值小于该值时认为“方向不显著”，单位与 jyq_SO2 相同。
        "so2_direction_deadband": 0.50,
        # pH 前后变化绝对值小于该值时认为“方向不显著”。
        "ph_direction_deadband": 0.02,
        # auto：按历史有效响应幅度自动标定 WEAK/SMALL/MEDIUM/STRONG；fixed 使用固定边界。
        "effect_strength_mode": "auto",
        # auto 模式下响应强度 SMALL 分界分位数。
        "effect_strength_small_quantile": 0.40,
        # auto 模式下响应强度 MEDIUM 分界分位数。
        "effect_strength_medium_quantile": 0.75,
        # fixed 模式或样本不足时的 SO2 响应幅度边界。
        "effect_strength_fixed_bins": {
            # 响应幅度 <= 1.0 视为 WEAK。
            "weak_max": 1.0,
            # > WEAK 且 <= 2.5 视为 SMALL。
            "small_max": 2.5,
            # > SMALL 且 <= 5.0 视为 MEDIUM；更大为 STRONG。
            "medium_max": 5.0,
        },
        # 稳定响应允许的 SO2 窗口极差上限；None 表示由数据/现有逻辑决定，不额外硬限制。
        "stable_so2_range_max": None,
        # 判断响应曲线振荡时，相邻差值绝对值小于该值不计入有效正负号变化。
        "oscillation_diff_deadband": 0.30,
        # 响应窗口内差分方向最多允许改变多少次；超过后认为振荡明显。
        "max_oscillation_sign_changes": 4,
    },

    # ------------------------------------------------------------------------
    # episode 是否可进入策略统计的基本有效性规则。
    # ------------------------------------------------------------------------
    "validity": {
        # True：第一模块 condition_valid=False 的 episode 不作为正常策略经验。
        "require_condition_valid": True,
        # True：工况轴越界后被第一模块 clip 到边界格的数据仍允许学习；
        # 若希望完全排除越界经验可设 False。
        "allow_out_of_range_clipped": True,
        # True：发生 pH/SO2 安全违规的 episode 仍保留用于“安全历史负样本”；
        # 保留不代表允许在线采用该动作。
        "keep_safety_violation_episodes": True,
    },

    # ------------------------------------------------------------------------
    # 动作 profile 可靠性评分。用于判断一条历史经验是否达到 SUPPORTED。
    # ------------------------------------------------------------------------
    "reliability": {
        # 事件数达到该值时，support 的事件数量分项基本视为充分。
        "reference_event_count": 20,
        # 独立连续数据段数量参考值；防止很多事件其实都来自同一段过程。
        "reference_segment_count": 10,
        # 覆盖独立自然日数量参考值；用于衡量时间覆盖广度。
        "reference_day_count": 10,
        # 可靠性总分各分项权重，总和应保持 1.0。
        "weights": {
            # 样本数量/来源覆盖充分程度。
            "support": 0.25,
            # 同一动作对 SO2/pH 影响方向是否一致。
            "direction_consistency": 0.25,
            # 动作后的过程响应是否平稳、少振荡。
            "response_stability": 0.20,
            # 历史执行过程中是否较少触碰 SO2/pH 安全边界。
            "safety_history": 0.20,
            # 是否跨多个独立日期/时段都有证据。
            "time_coverage": 0.10,
        },
        # 动作 profile 至少需要的有效事件数；低于该值不能认为得到基本支持。
        "minimum_supported_events": 5,
        # 至少覆盖多少个独立连续数据段。
        "minimum_supported_segments": 3,
        # 至少覆盖多少个独立日期。
        "minimum_supported_days": 2,
    },

    # ------------------------------------------------------------------------
    # 第一模块 condition snapshot 与第二模块 policy snapshot 的版本一致性。
    # ------------------------------------------------------------------------
    "version_alignment": {
        # True：第二模块版本号跟随作为训练输入的第一模块 snapshot 版本。
        "follow_condition_snapshot_version": True,
        # True：允许第一模块版本号出现跨号；仍需满足实际 snapshot/映射一致性校验。
        "allow_condition_version_jump": True,
        # True：有效 episode 无法解析到 condition/grid 时训练直接失败，不允许悄悄丢失正常样本。
        "fail_on_unresolved_valid_episode": True,
        # False：本身已判无效的 episode 无法解析工况时不阻断整体训练。
        "fail_on_unresolved_invalid_episode": False,
        # True：严格校验输入 CSV 中 condition_label/grid 等映射是否与冻结 snapshot 一致。
        "strict_input_mapping_check": True,
    },

    # ------------------------------------------------------------------------
    # 离线训练产物及版本保留策略。
    # ------------------------------------------------------------------------
    "output": {
        # True：第二模块输出目录名称使用第一模块 condition snapshot 的 v### 版本号。
        "version_directory_name_from_condition_snapshot": True,
        # 第二模块最多保留多少个版本目录。
        # 注意：长期更推荐由 P4PC 在同版本原子激活成功后统一清理完整版本对。
        "max_versions_to_keep": 5,
        # True：某工况实际存在动作 profile 时才写对应 pickle，减少大量空文件。
        "write_pickle_only_when_profiles_exist": True,
        # 是否保存 episode pickle，供增量训练快速读取和追溯。
        "write_episode_pickle": True,
        # 增量训练时优先读取上一版本 episode pickle，避免反复解析大 CSV。
        "prefer_episode_pickle_for_incremental_read": True,
        # 是否同时输出完整 episode CSV，便于人工检查/调试；会增加磁盘占用。
        "write_full_episode_csv": True,
        # 是否保存增量训练所需的上下文尾部 pickle。
        "write_context_tail_pickle": True,
        # 是否同时保存上下文尾部 CSV，便于人工检查。
        "write_context_tail_csv": True,
        # JSON 文件缩进空格数，只影响可读性和文件大小。
        "json_indent": 2,
    },
}


# ============================================================================
# 在线策略参数
# ============================================================================
ONLINE_POLICY_CONFIG = {
    # ------------------------------------------------------------------------
    # 第二模块唯一规范输出。阀位只是监测测点，不再是推荐动作或回退输出。
    # ------------------------------------------------------------------------
    "control_output": {
        "type": "TARGET_SUPPLY_FLOW",
    },
    "target_flow_execution": {
        # 内置适配器当前只生成并校验执行预览，不包含任何DCS写操作。
        "adapter_mode": "DRY_RUN",
    },

    # ------------------------------------------------------------------------
    # 正式模型加载、热切换和缓存。
    # ------------------------------------------------------------------------
    "model_loading": {
        # True：必须存在 active_version.json 才允许在线加载正式版本。
        "require_active_version_file": True,
        # False：active_version.json 缺失时禁止自行寻找“最新目录”上线，避免未激活模型误投入控制。
        "allow_latest_snapshot_fallback": False,
        # True：加载模型时校验 manifest 中记录的文件哈希，防止文件损坏/半写入。
        "verify_manifest_hashes": True,
        # 独立在线模式检查 active_version 是否变化的周期，单位：秒。
        "reload_check_interval_seconds": 30.0,
        # True：要求 condition snapshot 与 slurry policy 使用同一 integrated version。
        "require_integrated_version_pair": True,
        # 外部/P4PC 切换模型版本时，尽量保留 WAITING_EFFECT、反向锁等运行状态。
        "preserve_runtime_state_on_external_switch": True,
        # 在线最多缓存多少个工况策略对象，增大可减少重复磁盘读取但占用更多内存。
        "condition_policy_cache_size": 8,
    },

    # ------------------------------------------------------------------------
    # SO2 主控制目标、安全限值和目标切换策略。
    # ------------------------------------------------------------------------
    "so2_control": {
        # 没有收到实时目标时使用的默认净烟气 SO2 目标，单位：mg/Nm3。
        "default_target": 20.0,
        # 优先使用运行时输入目标；缺失时回退 default_target。
        "target_source_mode": "RUNTIME_WITH_CONFIG_FALLBACK",
        # 在线允许接受的目标范围，超出范围的目标不直接用于控制，单位：mg/Nm3。
        "allowed_target_range": [5.0, 30.0],
        # 当前 SO2 与目标误差绝对值 <= 该值时视为目标附近，优先 HOLD，单位：mg/Nm3。
        "target_deadband": 1.0,
        # None = 永远继承中央 PLANT_CONFIG.outlet_so2_safe_range 上限；
        # 不建议在这里再维护第二套排放硬上限。
        "emission_limit": None,
        # 距排放上限小于该余量时进入 WARNING 区域，单位：mg/Nm3。
        # 例如上限 35、margin=5，则 >=30 时开始明显偏安全控制。
        "emission_warning_margin": 5.0,
        # 距排放上限小于该余量时进入 EMERGENCY 区域，单位：mg/Nm3。
        "emission_emergency_margin": 1.0,
        # True：目标值突然变化时不立即一步跳到新目标，而是做受控过渡。
        "target_transition_enabled": True,
        # 有效控制目标每分钟最多变化多少，单位：mg/Nm3/min；调小更平滑、更保守。
        "maximum_effective_target_change_per_minute": 1.0,
        # 目标变化后额外 HOLD 多少个10秒决策周期；3次保持原来的30秒保护时间。
        "hold_cycles_after_target_change": 3,
        # True：减浆动作比加浆动作采用更保守的准入逻辑，降低 SO2 上冲风险。
        "decrease_slurry_more_conservative": True,
        # 只有 SO2 比目标低至少这么多时才允许考虑经济性减浆，单位：mg/Nm3。
        "minimum_low_side_error_for_decrease": 2.0,
    },

    # ------------------------------------------------------------------------
    # NORMAL 模式下：根据目标偏差决定允许的最大动作强度，并逐步放大动作。
    # ------------------------------------------------------------------------
    "regular_control": {
        # 目标误差绝对值 <= 该值时定义为 SMALL error，单位：mg/Nm3。
        "small_error_threshold": 2.0,
        # 目标误差绝对值 <= 该值时定义为 MEDIUM error；更大则 LARGE。
        "medium_error_threshold": 5.0,
        "progressive_action": {
            # True：同方向连续动作采用 SMALL → MEDIUM → STRONG 的渐进策略，不首步就给大动作。
            "enabled": True,
            # 每轮新的同方向调节序列允许的初始最大动作等级。
            "initial_max_magnitude": "SMALL",
            # 已完成多少次同方向匹配动作后，可以把上限提升到 MEDIUM。
            "medium_after_completed_matching_actions": 1,
            # 已完成多少次同方向匹配动作后，可以把上限提升到 STRONG。
            "strong_after_completed_matching_actions": 2,
            # True：NORMAL 普通偏差即使连续动作，也不允许 STRONG；只有 WARNING/EMERGENCY 才可用。
            "strong_only_warning_or_emergency": True,
        },
        # 不同控制紧迫等级允许的最大动作强度。
        "maximum_magnitude_by_level": {
            # 已在目标死区内：只能 HOLD。
            "TARGET_HOLD": "HOLD",
            # 小偏差：最大 SMALL。
            "TARGET_SMALL": "SMALL",
            # 中偏差：最大 MEDIUM。
            "TARGET_MEDIUM": "MEDIUM",
            # 大偏差：理论上最大 STRONG，但还会继续受 progressive_action 等规则限制。
            "TARGET_LARGE": "STRONG",
            # 接近排放上限：允许 STRONG 加浆。
            "WARNING": "STRONG",
            # 紧急排放风险：允许 STRONG 加浆。
            "EMERGENCY": "STRONG",
        },
    },

    # ------------------------------------------------------------------------
    # 离线动作 profile 在线准入门槛。先过门槛，再参与候选排序。
    # ------------------------------------------------------------------------
    "profile_acceptance": {
        # 本工况局部经验允许使用的可靠性状态。
        "local_allowed_status": ["SUPPORTED"],
        # 历史动作效果方向一致性下限，0~1；调高更保守但可用动作更少。
        "minimum_direction_consistency": 0.55,
        # 历史安全评分下限，百分制；低于该值的动作不进入在线候选。
        "minimum_safety_history_score": 80.0,
        # 综合可靠性总分下限，百分制。
        "minimum_reliability_total_score": 45.0,
        # 历史响应被判为“稳定”的比例下限，0~1。
        "minimum_stable_response_ratio": 0.50,
    },

    # ------------------------------------------------------------------------
    # 在线动作节流、WAITING_EFFECT 和反向锁。
    # ------------------------------------------------------------------------
    "action_stability": {
        # True：推荐动作发出后必须等待实际执行反馈，再正式进入“动作已执行/等待效果”状态。
        "wait_for_actual_execution_feedback": True,
        # 推荐后最长等待执行反馈的时间，单位：秒；超时后按在线状态机规则处理。
        "recommendation_feedback_timeout_seconds": 90.0,
        # 两次实际动作之间的最短时间，单位：分钟；避免过于频繁调节。
        "minimum_action_interval_minutes": 3.0,
        # 一次动作后禁止立即做相反方向动作的锁定时间，单位：分钟；抑制来回振荡。
        "reverse_action_lock_minutes": 10.0,
        # 单小时最多允许执行的动作次数，属于硬节流上限。
        "maximum_actions_per_hour": 6,
        # None：在线等待响应延迟沿用离线 TRAINING_CONFIG.episode.response_delay_minutes。
        "response_delay_minutes": None,
        # None：在线等待效果窗口沿用离线 TRAINING_CONFIG.episode.response_window_minutes。
        "response_window_minutes": None,
        # True：处于 WAITING_EFFECT 时阻止普通 NORMAL 再发新动作，避免动作效果相互叠加难以归因。
        "block_normal_actions_while_waiting_effect": True,
        # 稳定工况发生切换后额外 HOLD 的10秒在线周期数；3次仍为30秒。
        "condition_switch_hold_cycles": 3,
        # 热加载新模型后额外 HOLD 的10秒在线周期数；3次仍为30秒。
        "model_reload_hold_cycles": 3,
    },

    # ------------------------------------------------------------------------
    # FAST_CHANGE 动作策略。FAST 的识别/状态机参数不在这里，统一由
    # fast_change_mode/fast_change_config.py 管理；这里仅定义第二模块如何消费 FAST。
    # ------------------------------------------------------------------------
    "fast_policy": {
        "transient_exact_enabled": True,
        "transient_direction_pool_enabled": True,
        "allow_regular_policy_fallback": False,
        "allow_preemptive_increase": True,
        "target_band_preemptive_max_magnitude": "SMALL",
        "combined_rise_max_magnitude": "MEDIUM",
        "recovery_drop_max_decrease_magnitude": "SMALL",
        # FAST_RISE 历史动作即使净烟气绝对值仍上涨，只要安全且上涨速度被明显压制，
        # 仍可作为有效保护经验进入候选。
        "minimum_transient_safe_ratio": 0.85,
        "minimum_transient_rate_reduction": -0.10,
        # FAST 风险继续升级时，允许突破普通 WAITING_EFFECT，但仍保留执行反馈、
        # 反向锁和每小时动作次数等硬节流。
        "allow_waiting_effect_risk_escalation": True,
        "risk_escalation_minimum_action_interval_minutes": 1.0,
    },

    # ------------------------------------------------------------------------
    # 在线日志。日志文件写入 PLANT_CONFIG.paths.online_runtime_dir。
    # ------------------------------------------------------------------------
    "logging": {
        # 是否记录在线决策、执行结果和运行状态。
        "enabled": True,
        # 每个决策周期的候选、筛选原因、最终动作等 JSONL 日志文件名。
        "decision_log_filename": "online_decisions.jsonl",
        # 实际执行反馈/执行动作记录 JSONL 文件名。
        "execution_log_filename": "online_executions.jsonl",
        # WAITING_EFFECT、反向锁、最近动作等在线状态持久化文件名。
        "runtime_state_filename": "online_runtime_state.json",
    },
}
