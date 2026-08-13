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
    # 入口趋势风险。
    # 实际监测字段直接读取 plant_config.condition_axes，支持 1 或 2 个任意数值轴。
    # 先做因果 DEMA 平滑，再在时间窗口内同时检查：
    #   1) 变化率是否越过 slow/fast 阈值；
    #   2) 同方向变化点占比是否足够高。
    # 这样可以减少单点毛刺触发 FAST_CHANGE。
    # ------------------------------------------------------------------
    "trend": {
        # 趋势窗口，单位：分钟。
        "window_minutes": 5.0,
        # DEMA 半衰期，单位：秒。越大越平滑，但响应也越慢。
        "dema_halflife_seconds": 150.0,
        # 至少多少个有效点后才正式判趋势。
        "minimum_points": 4,
        # 上升/下降方向一致性阈值，0~1。0.70 表示至少 70% 有效差分同方向。
        "direction_ratio_threshold": 0.70,
        # 小于 grid step 的该比例时，认为只是微小噪声，不计入方向占比。
        "direction_deadband_step_ratio": 0.01,

        # 通用变化率阈值按每个工况轴自身 grid step 自动换算，单位为“轴单位/分钟”。
        # 当前默认：slow = 0.10 * step/min，fast = 0.30 * step/min。
        # 例如 jzfh step=10 时约为 1 / 3 MW/min；
        # yyq_SO2 step=200 时约为 20 / 60 mg/Nm3/min。
        "slow_step_ratio_per_minute": 0.10,
        "fast_step_ratio_per_minute": 0.30,

        # 可按字段名覆盖自动阈值。未配置的轴仍按 grid step 自动换算。
        # 示例：
        # "axis_overrides": {
        #     "jzfh": {"slow_rate": 1.0, "fast_rate": 3.0},
        #     "yyq_SO2": {"slow_rate": 20.0, "fast_rate": 60.0},
        # },
        "axis_overrides": {},
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
    # ------------------------------------------------------------------
    "state_machine": {
        # 一旦进入 FAST_CHANGE，至少保持多久，单位：分钟。
        "minimum_fast_hold_minutes": 4.0,
        # 原始趋势不再是 FAST 后，连续多少个周期稳定才进入 FAST_RECOVERY。
        "exit_stable_cycles": 4,
        # FAST_RECOVERY 最少持续时间，单位：分钟。
        "recovery_hold_minutes": 2.0,
    },
}
