"""湿法脱硫 FAST_CHANGE 独立风险识别配置。

职责边界：
- FAST_CHANGE 的触发核心来自入口/工况轴快速变化；
- 净烟气 SO2 只作为效果风险、排放风险和后续动作强度依据，不能单独把系统判成 FAST_CHANGE；
- 本模块暂时只输出风险上下文，不负责具体供浆动作，后续再与 slurry_policy_model 衔接。

厂级工况轴与净烟气 SO2 安全上限继续统一来自 ``plant_config.py``，
本文件只配置 FAST_CHANGE 自身的趋势识别、效果风险和状态机参数。
"""
from __future__ import annotations


FAST_CHANGE_CONFIG = {
    "enabled": True,

    # ------------------------------------------------------------------
    # 在线输入保护。
    # 这些字段只决定“当前帧能不能推进 FAST / condition 在线状态”，不参与趋势计算。
    # 命中任一 invalid_field_values 后：FAST DEMA/窗口/状态机不推进，第一模块 majority
    # 窗口也不推进，第二模块收到 condition_valid=False 后安全 HOLD。
    # ------------------------------------------------------------------
    "input_guard": {
        "enabled": True,
        "invalid_field_values": {
            # jym=100 为现场测点校验态；数值字符串 100/100.0 也按同一值处理。
            "jym": [100],
        },
        # 字段缺失时不因为本 guard 单独阻断；缺失/坏点仍由各模块自己的必需字段校验处理。
        "missing_field_is_valid": True,
    },

    # ------------------------------------------------------------------
    # 入口趋势风险。
    # 当前钢厂没有机组负荷，plant_config.condition_axes 仅配置不可控扰动 yyq_SO2。
    # 输入已经是 1 秒数据在 10 秒边界使用最近 8/9/10 秒均值形成的 10 秒快照，
    # 因此 FAST 不再叠加原来的 150 秒重平滑 + 5 分钟长窗口，而使用约 1 分钟
    # 因果窗口快速识别。参数来自 2026-06~08 历史 10 秒数据的首轮分布/未来风险分析：
    # 60 秒变化率达到约 120 mg/Nm3/min 后，未来数分钟继续显著恶化的概率明显上升。
    # ------------------------------------------------------------------
    "trend": {
        # FAST 主趋势窗口，单位：分钟。约 1 分钟，在线只使用当前及过去数据。
        "window_minutes": 1.0,
        # 轻量 DEMA 半衰期，单位：秒。10 秒输入已做三点均值，因此只保留轻平滑。
        "dema_halflife_seconds": 30.0,
        # 6 个有效 10 秒点即可开始判趋势，约 50~60 秒观察量。
        "minimum_points": 6,
        # 上升/下降方向一致性阈值，0~1。至少 70% 有效差分同方向。
        "direction_ratio_threshold": 0.70,
        # 默认微小噪声死区仍按 grid step 比例计算；当前 yyq_SO2 使用 axis_overrides 覆盖。
        "direction_deadband_step_ratio": 0.01,

        # 通用兜底阈值。未显式覆盖的其他工况轴仍按 grid step 自动换算。
        "slow_step_ratio_per_minute": 0.90,
        "fast_step_ratio_per_minute": 1.20,

        # 当前钢厂唯一不可控扰动轴 yyq_SO2 的显式阈值，单位 mg/Nm3/min。
        # SLOW=90 作为 FAST 预警区；FAST=120 进入前馈保护。
        "axis_overrides": {
            "yyq_SO2": {
                "slow_rate": 90.0,
                "fast_rate": 120.0,
                # 10 秒快照已是 8/9/10 秒均值，1 mg/Nm3 的相邻变化以下不计方向。
                "direction_deadband": 1.0,
            },
        },
    },

    # ------------------------------------------------------------------
    # FAST 历史前馈学习语义。
    # 这部分不会直接写死“加多少浆”；离线第二模块会从实际历史供浆动作中统计
    # EXACT -> 同级 POOL -> 全厂安全 BASELINE 三层原型，在线只消费版本化训练产物。
    # FAST 级别以当前 detector 输出的主轴变化率为基础：
    #   L1: >= 120；L2: >= 160；L3: >= 220 mg/Nm3/min（按 fast_rate 倍数表达）。
    # ------------------------------------------------------------------
    "feedforward": {
        "semantics_version": "CAUSAL_FAST_FEEDFORWARD_V1",
        "severity_rate_multipliers": {
            "L1": 1.0,
            "L2": 4.0 / 3.0,
            "L3": 11.0 / 6.0,
        },
        # 全厂安全基线只使用响应期 SO2 安全时间占比不低于该值、且无 pH/排放硬越限的动作。
        "minimum_safe_ratio": 0.85,
        # 不同回退层的最小独立历史动作条数。证据不足时继续向下一层回退。
        "minimum_exact_events": 2,
        "minimum_pool_events": 3,
        "minimum_baseline_events": 5,
        # EXACT 有精确上下文时取历史中位；POOL/BASELINE 使用较保守的 P25 首步动作。
        "exact_action_quantile": 0.50,
        "pool_action_quantile": 0.25,
        "baseline_action_quantile": 0.25,
    },

    # ------------------------------------------------------------------
    # 出口效果风险。
    # 注意：这里只评价“当前结果有多危险/偏差有多大”，不负责触发 FAST_CHANGE。
    # ------------------------------------------------------------------
    "effect": {
        # 运行时没有显式 target，也没有 outlet_so2_target 字段时使用的兜底目标。
        "default_target": 20.0,
        # 目标死区，单位：mg/Nm3。
        "target_deadband": 1.0,
        # 离目标超过该值认为已经属于明显偏差。
        "far_from_target_threshold": 5.0,
        # 距排放上限小于该余量时进入 WARNING。
        "emission_warning_margin": 5.0,
        # 距排放上限小于该余量时进入 EMERGENCY。
        "emission_emergency_margin": 1.0,
        # 净烟气 SO2 趋势变化率分档，单位：mg/Nm3/min。
        "outlet_slow_rate": 0.20,
        "outlet_fast_rate": 0.80,
    },

    # ------------------------------------------------------------------
    # FAST 状态机，防止边界附近反复进出。
    # 状态机时长本轮先保持不变；后续将单独把“SLOW 不能算稳定”的退出语义修正。
    # ------------------------------------------------------------------
    "state_machine": {
        # 一旦进入 FAST_CHANGE，至少保持多久，单位：分钟。
        "minimum_fast_hold_minutes": 4.0,
        # 原始趋势不再是 FAST 后，连续多少个周期稳定才进入 FAST_RECOVERY；12次约2分钟。
        "exit_stable_cycles": 12,
        # FAST_RECOVERY 最少持续时间，单位：分钟。
        "recovery_hold_minutes": 2.0,
    },

    # ------------------------------------------------------------------
    # 生命周期/存储。这里只保存小型 checkpoint、FAST 事件摘要和版本 manifest，
    # 不永久复制整份原始 CSV，避免历史数据越积越大。
    # ------------------------------------------------------------------
    "lifecycle": {
        # 离线 FAST 快照最多保留多少个版本；与第一/第二模块一样滚动清理旧版。
        "max_versions_to_keep": 5,
        # 在线每处理多少条10秒数据覆盖写一次 runtime checkpoint；60条约10分钟。
        "runtime_checkpoint_every_samples": 60,
        # 在线是否持久化闭合 FAST 事件的月度 JSONL 摘要。
        "persist_compact_events": True,
        # 在线 FAST 事件月度 JSONL 最多保留多少个月；<=0 表示不自动清理。
        "runtime_event_months_to_keep": 24,
    },
}
