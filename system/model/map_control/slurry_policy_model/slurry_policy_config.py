"""湿法脱硫供浆历史动作响应模型——统一配置文件。

换厂时主要修改本文件一次：
1. 初次、增量输入路径和输出目录；
2. 输入 CSV 的时间列名；
3. 塔数量、每座塔的 pH 安全区间；
4. 每座塔的供浆阀数量、字段、开度范围和有效动作阈值；
5. 必要的时间窗口和自适应训练参数。

固定约定：
- 第一模块输出字段名保持不变，第二模块内部直接读取，不再重复配置字段映射；
- 负荷、原烟气 SO2、净烟气 SO2 固定读取 jzfh、yyq_SO2、jyq_SO2；
- 不需要配置厂区 ID、时区、condition_snapshot.json；
- 初次训练默认读取 Initial_train_after_condition.csv；
- 增量训练默认读取 Incremental_train_after_condition.csv；
- 命令行传入 --input 时覆盖默认输入路径。
"""


PLANT_CONFIG = {
    "paths": {
        # 第一模块初次训练标注结果。
        "default_initial_input": r"F:\tlgj\files\Initial_train_after_condition.csv",

        # 第一模块增量训练标注结果。
        "default_incremental_input": r"F:\tlgj\files\Incremental_train_after_condition.csv",

        # 第二模块离线训练固定输出根目录。
        "output_root": r"F:\tlgj\files\slurry_policy_model_output",

        # 第一模块正式快照根目录。初次/增量第二模块均读取这里的
        # v###/condition_snapshot.json，并使用完全相同的 v### 版本号。
        "condition_snapshots_dir": r"F:\tlgj\system\model\map_control\condition_model\snapshots",

        # 第一、第二模块共同使用的正式激活版本指针。第一模块快照先训练完成时
        # 不更新该文件；只有第二模块同版本训练也完成并通过激活校验后才原子更新。
        "active_policy_version_file": r"F:\tlgj\files\slurry_policy_model_output\active_version.json",

        # 在线运行状态与决策日志目录。
        "online_runtime_dir": r"F:\tlgj\files\slurry_policy_online_runtime",
    },

    # 输入 CSV 已有的时间列。当前数据使用 date；它不是要求新增 timestamp 字段。
    "time_column": "date",

    # 净烟气 SO2 硬安全范围。动态控制目标不参与离线训练。
    "outlet_so2_safe_range": [0.0, 35.0],

    # 可选：实际供浆泵启停状态字段。
    # 某列在一个阀门动作响应窗口内发生 0→1、1→0 或运行组合变化时，
    # 该片段无法只归因于阀门动作，默认判为 INVALID。没有接入测点时保持空列表。
    "supply_pump_state_columns": [],

    # 塔配置：
    # - 某厂只有一级塔：删除二级塔项，或设置 enabled=False；
    # - 某塔有 1/2/3 个阀门：直接增删 valves 列表项；
    # - 塔数量、pH 安全区间、阀门数量及开度范围属于厂级固定结构。
    "towers": [
        {
            "tower_id": "xst",
            "display_name": "一级塔",
            "enabled": True,
            "ph_column": "xstjy_PH",

            # 该厂一级塔固定 pH 安全运行范围。
            "ph_safe_range": [4.6, 5.6],

            # 靠近上下限时的保护带宽度，仅用于状态分档和风险统计。
            "ph_guard_band": 0.15,

            "valves": [
                {
                    "valve_id": "xst_v1",
                    "display_name": "一级塔供浆阀1",
                    "column": "xst_FMKD1",
                    "min_opening": 0.0,
                    "max_opening": 100.0,

                    # 阀位累计净变化达到该值才认定为真实 ACTION。
                    # 单位与 CSV 阀位字段一致，通常为开度百分点。
                    "action_threshold": 0.50,
                },
                {
                    "valve_id": "xst_v2",
                    "display_name": "一级塔供浆阀2",
                    "column": "xst_FMKD2",
                    "min_opening": 0.0,
                    "max_opening": 100.0,
                    "action_threshold": 0.50,
                },
            ],
        },
        {
            "tower_id": "apt",
            "display_name": "二级塔",
            "enabled": True,
            "ph_column": "aptjy_PH",
            "ph_safe_range": [5.6, 6.5],
            "ph_guard_band": 0.15,
            "valves": [
                {
                    "valve_id": "apt_v1",
                    "display_name": "二级塔供浆阀",
                    "column": "apt_FMKD",
                    "min_opening": 0.0,
                    "max_opening": 100.0,
                    "action_threshold": 0.50,
                }
            ],
        },
    ],
}


TRAINING_CONFIG = {
    "progress": {
        # 默认在终端显示单行百分比进度条；初次和增量训练均生效。
        "enabled": True,

        # 进度条字符宽度，只影响显示，不影响训练结果。
        "bar_width": 32,

        # 最短刷新间隔，避免大数据训练时过于频繁刷新终端。
        "min_interval_seconds": 0.20,

        # 是否显示从本次训练启动开始计算的已用时间。
        "show_elapsed": True,
    },


    "performance": {
        # 严格等价性能优化：只读取第二模块实际使用的字段，避免无关 DCS 测点
        # 增加 CSV 解析、内存复制和排序开销。不会改变动作/HOLD/响应定义。
        "read_only_required_columns": True,

        # 数据本身已经按时间升序且无重复时间戳时，跳过全表排序。程序会先校验，
        # 发现乱序或重复时仍按原逻辑排序并去重。
        "skip_sort_when_already_ordered": True,

        # 聚合前把高重复字符串键转为 pandas category，并采用 observed=True 分组。
        # 仅降低内存和 groupby 开销，不改变分组键。
        "categorical_groupby_keys": True,

        # 输出 performance_report.json，记录读取、片段提取、重映射、聚合和写盘耗时。
        "record_stage_timings": True,

        # 临近工况策略按目标工况分批展开，避免一次性构造超大 mapped DataFrame。
        # 只影响内存峰值和执行效率，不减少任何 episode，也不改变聚合结果。
        "neighbor_target_condition_batch_size": 64,

        # 单批临近映射预计展开行数上限。0 表示仅按上面的工况数量分批。
        # 程序会按 source-grid 事件数估计展开规模，再确定批次边界。
        "neighbor_max_expanded_rows_per_batch": 500000,
    },

    "io": {
        "csv_encoding": "utf-8-sig",

        # None 表示让 pandas 自动解析 date 列；格式完全固定时可填写格式字符串。
        "timestamp_format": None,
        "drop_duplicate_timestamp_keep": "last",
        "strict_required_columns": True,
    },

    "preprocessing": {
        # 阀位仅做短窗口中位数去抖；不对阶跃动作插值。
        "valve_rolling_median_points": 3,

        # 相邻记录间隔超过该秒数时切成独立连续段。
        "max_continuous_gap_seconds": 180,
        "coerce_numeric": True,
    },

    "episode": {
        # 动作前基线统计窗口。
        "baseline_minutes": 5.0,

        # 动作累计变化检测回看窗口。
        "action_detection_window_minutes": 2.0,

        # 一次动作允许持续的最长时间。
        "max_action_duration_minutes": 20.0,

        # 阀位稳定达到该时长后判定动作结束。
        "action_end_stable_minutes": 1.5,

        # 相邻动作间隔小于该值时合并为同一连续动作。
        "action_merge_gap_minutes": 1.0,

        # 动作结束后等待过程迟滞。
        "response_delay_minutes": 3.0,

        # 迟滞后评价 SO2 和 pH 的窗口。
        "response_window_minutes": 10.0,

        # 响应窗口内又出现新动作时，无法单独归因，判 INVALID。
        "invalidate_followup_action_in_response": True,

        # ACTION 前后该分钟数内不提取 HOLD。
        "hold_action_guard_minutes": 3.0,

        # HOLD 决策片段长度和抽样步长；相等表示不重叠。
        "hold_episode_minutes": 15.0,
        "hold_stride_minutes": 15.0,

        # 每个连续稳定段最多抽取多少个 HOLD，防止 HOLD 数量失衡。
        "max_hold_episodes_per_segment": 48,

        # 基线/响应窗口实际覆盖率最低要求。
        "minimum_window_coverage_ratio": 0.70,

        # 增量训练保留上一批末尾数据，用于跨批边界动作识别。
        "incremental_context_tail_minutes": 60.0,

        # 动作后该时长内出现反向动作，记录为短周期反调。
        "short_reverse_action_minutes": 20.0,
    },

    # 第二模块的“锚点工况 + 邻域容忍归属”。第一模块细网格和
    # condition_label 完全不修改；这里只决定一个历史动作片段可否继续
    # 计入动作发生时（HOLD 为窗口主导）的工况。
    "condition_attribution": {
        "enabled": True,
        "action_anchor_mode": "ACTION_START",
        "hold_anchor_mode": "MAJORITY_CONDITION",

        # 允许邻域：负荷轴上下 2 个基础格，原烟气 SO2 轴上下 3 个基础格。
        "max_load_grid_offset": 2,
        "max_inlet_so2_grid_offset": 3,

        # 片段中至少 60% 的可解析网格记录位于上述邻域内，才计入锚点工况。
        "minimum_neighborhood_coverage_ratio": 0.60,

        # 单纯跨 grid/condition 不再自动判快变；快变仍由单位时间变化率判定。
        "grid_change_alone_is_transient": False,
        "condition_label_change_alone_is_transient": False,

        # EXACT_LOCAL 与 NEARBY_ACCEPTED 只作为审计来源，不形成两套在线策略。
        # 邻域片段证据权重取 neighborhood_coverage_ratio。
        "nearby_evidence_weight_mode": "COVERAGE_RATIO",
    },

    # 临近工况回退策略。空间半径直接复用上面的 ±2/±3，避免无限跨工况。
    "neighbor_policy": {
        "enabled": True,
        "include_same_condition": True,
        "include_global_only": False,
        "distance_weight_mode": "LINEAR_AXIS",
        "minimum_mapping_weight": 0.10,
    },

    # 全厂经验降级为“动作方向与安全先验”，不输出可直接执行的代表阀位增量。
    # 下列门槛用于判断证据是否真正覆盖多个连续区域，而非被单一工况垄断。
    "plant_action_prior": {
        "enabled": True,
        "global_only_evidence_weight": 0.50,
        "minimum_source_conditions": 3,
        "minimum_source_grids": 3,
        "minimum_events_per_source_grid": 2,
        "maximum_single_condition_share": 0.60,
        "maximum_single_grid_share": 0.50,
        "minimum_cross_grid_direction_consistency": 0.70,
    },

    "disturbance": {
        # auto：初次训练按本厂历史分布标定；增量默认冻结上一版结果。
        # fixed：始终使用下方固定阈值。
        "mode": "auto",
        "trend_window_minutes": 5.0,
        "auto_slow_quantile": 0.75,
        "auto_fast_quantile": 0.92,

        # fixed 模式阈值，单位为字段单位/分钟。
        "load_slow_rate": 1.0,
        "load_fast_rate": 3.0,
        "inlet_so2_slow_rate": 20.0,
        "inlet_so2_fast_rate": 60.0,

        # auto 标定的最小阈值下限。
        "minimum_load_slow_rate": 0.10,
        "minimum_load_fast_rate": 0.30,
        "minimum_inlet_so2_slow_rate": 1.0,
        "minimum_inlet_so2_fast_rate": 3.0,
    },

    "state": {
        # 离线按实际净烟气 SO2 区间建状态，不使用动态目标。
        "outlet_so2_edges": [0, 5, 10, 15, 20, 25, 30, 35],
        "outlet_so2_trend_slow_rate": 0.20,
        "outlet_so2_trend_fast_rate": 0.80,

        # 每个阀门先按自身 min/max 归一化，再进行开度分档。
        "valve_opening_edges": [0.0, 0.25, 0.50, 0.75, 1.0],

        # 同塔多阀归一化开度极差低于该值视为 BALANCED。
        "valve_balance_threshold": 0.10,
        "include_condition_state_key": True,
        "include_disturbance_mode": True,
    },

    "action_magnitude": {
        "mode": "auto",

        # 超过动作阈值但归一化幅度非常小的事件标记 MICRO。
        "micro_max": 0.006,

        # 初次训练按每个动作族分布标定 SMALL/MEDIUM/STRONG。
        "small_quantile": 0.40,
        "medium_quantile": 0.75,
        "minimum_events_per_family": 8,

        # 历史不足或 fixed 模式下使用的默认边界。
        "default_fixed_bins": {
            "small_max": 0.025,
            "medium_max": 0.060,
        },
        "family_fixed_bins": {},
    },

    "response": {
        # SO2/pH 变化绝对值不超过死区时记为 NEUTRAL。
        "so2_direction_deadband": 0.50,
        "ph_direction_deadband": 0.02,

        # 历史 SO2 作用强度分档，初次标定、增量冻结。
        "effect_strength_mode": "auto",
        "effect_strength_small_quantile": 0.40,
        "effect_strength_medium_quantile": 0.75,
        "effect_strength_fixed_bins": {
            "weak_max": 1.0,
            "small_max": 2.5,
            "medium_max": 5.0,
        },

        # None 表示初次训练按有效片段的 P75 自动标定。
        "stable_so2_range_max": None,
        "oscillation_diff_deadband": 0.30,
        "max_oscillation_sign_changes": 4,
    },

    "validity": {
        "require_condition_valid": True,
        "allow_out_of_range_clipped": True,

        # 配置了 supply_pump_state_columns 后，供浆泵启停/组合变化判 INVALID。
        "invalidate_supply_pump_state_change": True,

        # 结果出现安全越界的事件仍保留，作为风险历史；不能只保留好结果。
        "keep_safety_violation_episodes": True,
    },

    "reliability": {
        "reference_event_count": 20,
        "reference_segment_count": 10,
        "reference_day_count": 10,
        "weights": {
            "support": 0.25,
            "direction_consistency": 0.25,
            "response_stability": 0.20,
            "safety_history": 0.20,
            "time_coverage": 0.10,
        },
        "minimum_supported_events": 5,
        "minimum_supported_segments": 3,
        "minimum_supported_days": 2,
    },

    "version_alignment": {
        # 第二模块版本必须与指定第一模块 condition_snapshot.json 完全一致。
        "follow_condition_snapshot_version": True,

        # 增量训练允许第二模块跨过若干第一模块中间版本，直接按当前
        # grid_id→condition_label 映射重排全部历史 episode。
        "allow_condition_version_jump": True,

        # 训练用 VALID episode 必须 100% 能由 anchor_grid_id 映射。
        "fail_on_unresolved_valid_episode": True,

        # INVALID 片段只用于审计，默认允许少量旧记录无法映射并写报告。
        "fail_on_unresolved_invalid_episode": False,

        # 新增 CSV 必须与指定第一模块快照的版本和映射完全一致。
        "strict_input_mapping_check": True,
    },

    "output": {
        # 第二模块不再生成 pv0001、pv0002；直接使用第一模块的 v001、v002。
        "version_directory_name_from_condition_snapshot": True,

        # 与第一模块当前配置保持一致，最多保留 5 个完整版本。
        "max_versions_to_keep": 5,

        # 无历史响应的工况不伪造本地 PKL；在线时走临近/规则回退。
        "write_pickle_only_when_profiles_exist": True,

        # V1.8B 内部事实源优先使用 pickle，增量读取不再反复解析大型 CSV。
        "write_episode_pickle": True,
        "prefer_episode_pickle_for_incremental_read": True,

        # 完整 episode CSV 仅用于人工审计。保持 True 与 V1.7 产物兼容；
        # 现场追求最高写盘速度时可改为 False，之后用 export_episode_csv.py 导出。
        "write_full_episode_csv": True,

        # context_tail 同时保存 pickle；CSV 可独立关闭。
        "write_context_tail_pickle": True,
        "write_context_tail_csv": True,

        "json_indent": 2,
    },
}


# 第二模块在线推理配置。
#
# 重要边界：
# 1. 离线训练仍然只学习“动作产生什么 SO2/pH 响应”，不绑定具体控制目标；
# 2. 在线默认目标写在这里，运行时 MainControl/DCS 传入的目标优先；
# 3. 0～35 是安全范围，不是控制目标；
# 4. 第二模块只输出动作意图和推荐阀门增量，最终联锁、限幅和下发仍由 MainControl 完成。
ONLINE_POLICY_CONFIG = {
    "model_loading": {
        # 正式运行必须由 active_version.json 指向已验证的 v### 快照。
        "require_active_version_file": True,
        "allow_latest_snapshot_fallback": False,
        "verify_manifest_hashes": True,

        # 独立运行 OnlineSlurryPolicy 时由第二模块自行轮询；当它被第一模块集成
        # 调用时，第一模块的 IntegratedVersionManager 接管轮询，并以
        # external_version_management=True 禁止第二模块抢先单独切换。
        "reload_check_interval_seconds": 30.0,

        # active_version.json 必须同时包含 condition 与 slurry_policy 同版本信息。
        # PolicySnapshotLoader 会校验 integrated/policy/condition/source-condition
        # 四个版本、condition snapshot 哈希和 grid-condition 映射哈希。
        "require_integrated_version_pair": True,
        "preserve_runtime_state_on_external_switch": True,
        "condition_policy_cache_size": 8,
    },

    "so2_control": {
        # 运行时未传入目标时使用。20.0 仅为示例厂级默认值。
        "default_target": 20.0,
        "target_source_mode": "RUNTIME_WITH_CONFIG_FALLBACK",

        # 操作员/DCS 允许设置的目标范围。不得超过排放安全上限。
        "allowed_target_range": [5.0, 30.0],

        # 当前 SO2 位于 effective_target ± deadband 时优先 HOLD。
        "target_deadband": 1.0,

        # None 表示继承 PLANT_CONFIG.outlet_so2_safe_range 的上限。
        "emission_limit": None,
        "emission_warning_margin": 5.0,
        "emission_emergency_margin": 1.0,

        # 目标变化采用有效目标缓变，避免 20→15 时立刻产生强动作。
        "target_transition_enabled": True,
        "maximum_effective_target_change_per_minute": 1.0,
        "hold_cycles_after_target_change": 1,

        # SO2 低于目标时，减浆比增浆更保守。
        "decrease_slurry_more_conservative": True,
        "minimum_low_side_error_for_decrease": 2.0,
    },

    "regular_control": {
        # 误差为 current_so2 - effective_target 的绝对值。
        "small_error_threshold": 2.0,
        "medium_error_threshold": 5.0,

        # 普通稳态按“小动作优先、完成响应后再逐级升级”限制最大幅度。
        "progressive_action": {
            "enabled": True,
            "initial_max_magnitude": "SMALL",
            "medium_after_completed_matching_actions": 1,
            "strong_after_completed_matching_actions": 2,
            "strong_only_warning_or_emergency": True,
        },

        # 不同需求等级允许的最大历史动作幅度。
        "maximum_magnitude_by_level": {
            "TARGET_HOLD": "HOLD",
            "TARGET_SMALL": "SMALL",
            "TARGET_MEDIUM": "MEDIUM",
            "TARGET_LARGE": "STRONG",
            "WARNING": "STRONG",
            "EMERGENCY": "STRONG",
        },
    },

    "profile_acceptance": {
        # 初期上线默认只采用 SUPPORTED 档案；没有时继续向下游回退。
        "local_allowed_status": ["SUPPORTED"],
        "neighbor_allowed_status": ["SUPPORTED"],
        "transient_allowed_status": ["SUPPORTED"],
        "plant_prior_allowed_status": ["SUPPORTED"],

        "minimum_direction_consistency": 0.55,
        "minimum_safety_history_score": 80.0,
        "minimum_reliability_total_score": 45.0,
        "minimum_stable_response_ratio": 0.50,

        # 初期不允许 MIXED/REBALANCE 复杂动作自动进入推荐。
        "allow_mixed_action": False,
        "allow_rebalance_action": False,
    },

    "action_stability": {
        # 推荐动作必须收到 MainControl 的实际执行反馈后才进入等待响应。
        "wait_for_actual_execution_feedback": True,
        "recommendation_feedback_timeout_seconds": 90.0,

        "minimum_action_interval_minutes": 3.0,
        "reverse_action_lock_minutes": 10.0,
        "maximum_actions_per_hour": 6,

        # None 表示直接复用离线 effective_config 中的响应延迟和响应窗口。
        "response_delay_minutes": None,
        "response_window_minutes": None,
        "block_normal_actions_while_waiting_effect": True,

        # 第一模块 stable condition 切换后，普通经济动作暂停的控制周期数。
        "condition_switch_hold_cycles": 1,

        # 新的 active_version 成功热加载后，普通动作暂停一个周期。
        "model_reload_hold_cycles": 1,
    },

    "fast_mode": {
        "minimum_hold_minutes": 4.0,
        "exit_stable_cycles": 4,
        "recovery_hold_minutes": 2.0,
        "block_economic_slurry_decrease": True,

        # FAST 没有匹配 transient 档案时，不回退到普通本地策略；走规则安全基线。
        "allow_regular_policy_fallback": False,
    },

    "execution_limits": {
        # 阀门上下限额外保留的开度裕量。
        "valve_limit_margin": 0.5,

        # 历史代表增量和规则增量最终都受该单阀绝对变化上限约束，单位为开度百分点。
        "maximum_single_valve_delta_by_magnitude": {
            "MICRO": 1.0,
            "SMALL": 2.0,
            "MEDIUM": 4.0,
            "STRONG": 6.0,
        },

        # 全厂先验/规则基线没有精确历史增量时使用的最小动作步长。
        "rule_step_by_magnitude": {
            "MICRO": 0.6,
            "SMALL": 1.2,
            "MEDIUM": 2.5,
            "STRONG": 4.0,
        },

        # 低于该开度变化不下发；实际值还会与各阀 action_threshold 取较大值。
        "minimum_command_delta": 0.5,

        # 可按厂指定规则回退优先动作族；空列表时按 pH 裕量自动选择塔。
        "preferred_increase_action_families": [],
        "preferred_decrease_action_families": [],
    },

    "logging": {
        "enabled": True,
        "decision_log_filename": "online_decisions.jsonl",
        "execution_log_filename": "online_executions.jsonl",
        "runtime_state_filename": "online_runtime_state.json",
    },
}
