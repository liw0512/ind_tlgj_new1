from __future__ import annotations

"""GUI 层的供浆策略中文解释。

算法层继续保留稳定的英文 reason_codes / state / source 标识，便于训练、数据库、
日志和程序判断；本文件只负责把这些机器标识翻译成操作员可读的中文。

首页使用 ``summarize_reason_codes`` 生成一句简洁结论；供浆控制详情页使用
``translate_reason_codes`` 展开逐条中文说明。未知代码不会直接作为主界面正文显示，
但原始代码仍可在工程诊断区域保留，便于追溯。
"""

from typing import Any, Iterable, List


EXPERIENCE_SOURCE_TEXT = {
    "NONE": "无可用经验",
    "LOCAL": "本工况历史经验",
    "LOCAL_CONDITION": "本工况历史经验",
    "NEIGHBOR": "邻近工况经验",
    "NEIGHBOR_STATE": "邻近工况经验",
    "PLANT_PRIOR": "全厂历史先验",
    "PLANT_ACTION_PRIOR": "全厂历史先验",
    "RULE": "规则基线",
    "RULE_BASELINE": "规则基线",
    "FAST_RULE_BASELINE": "快速扰动安全规则",
    "TRANSIENT_EXACT": "同类快速扰动经验",
    "TRANSIENT_DIRECTION_POOL": "同方向快速扰动经验",
}

MAGNITUDE_TEXT = {
    "HOLD": "保持",
    "WEAK": "微弱",
    "MICRO": "微调",
    "SMALL": "小幅",
    "MEDIUM": "中幅",
    "STRONG": "大幅",
}

DIRECTION_TEXT = {
    "HOLD": "保持",
    "INCREASE": "增加供浆",
    "DECREASE": "减少供浆",
    "MIXED": "供浆重分配",
    "UNKNOWN": "未知",
}

DECISION_STATE_TEXT = {
    "READY": "就绪",
    "WAITING": "等待",
    "INITIALIZING": "初始化中",
    "RECOMMENDED": "已推荐",
    "ACTION_RECOMMENDED": "等待执行反馈",
    "HOLD": "保持",
    "BLOCKED": "阻断",
    "WAITING_EFFECT": "等待动作效果",
    "EVALUATING_EFFECT": "评估动作效果",
}

CONTROL_MODE_TEXT = {
    "NORMAL": "常规控制",
    "REGULAR": "常规控制",
    "WAITING": "等待",
    "INITIALIZING": "初始化中",
    "BLOCKED": "控制阻断",
    "FAST_CHANGE": "快速扰动保护",
    "FAST_RECOVERY": "快速扰动恢复",
    "MODEL_TRANSITION": "模型切换保护",
    "TARGET_TRANSITION": "目标切换保护",
    "CONDITION_TRANSITION": "工况切换保护",
    "WAITING_EFFECT": "等待动作效果",
    "EVALUATING_EFFECT": "评估动作效果",
}

REASON_TEXT = {
    # FAST_CHANGE 趋势与状态机
    "FAST_TREND_DETECTED": "检测到入口工况快速变化，已达到快速扰动判定条件",
    "SLOW_TREND_DETECTED": "入口工况存在缓慢趋势变化，但尚未达到快速扰动阈值",
    "NO_CONDITION_AXIS_AVAILABLE": "当前缺少可用于快速扰动判断的工况轴数据",
    "FAST_TREND_WARMING_UP": "快速扰动趋势窗口正在积累数据",
    "FAST_CHANGE_INPUT_DEGRADED": "快速扰动判断所需的部分输入字段缺失",
    "EFFECT_RISK_DOES_NOT_TRIGGER_FAST_CHANGE": "当前存在出口效果风险，但该风险不会单独触发快速扰动模式",
    "FAST_CHANGE_ENTERED": "系统已进入快速扰动保护模式",
    "FAST_MINIMUM_HOLD_ACTIVE": "快速扰动最小保持时间尚未结束",
    "FAST_RECOVERY_ENTERED": "快速扰动已结束，系统进入恢复观察阶段",
    "FAST_RECOVERY_HOLD_ACTIVE": "系统仍处于快速扰动恢复观察阶段",
    "FAST_RECOVERY_COMPLETED": "快速扰动恢复阶段已结束，系统已回到常规控制",

    # FAST_CHANGE 出口效果判断
    "OUTLET_SO2_MISSING": "净烟气 SO₂ 数据缺失",
    "OUTLET_SO2_EMERGENCY": "净烟气 SO₂ 已进入排放紧急区",
    "OUTLET_SO2_WARNING": "净烟气 SO₂ 已进入排放预警区",
    "OUTLET_SO2_FAR_ABOVE_TARGET": "净烟气 SO₂ 明显高于当前目标",
    "OUTLET_SO2_ABOVE_TARGET": "净烟气 SO₂ 高于当前目标",
    "OUTLET_SO2_FAR_BELOW_TARGET": "净烟气 SO₂ 明显低于当前目标",
    "OUTLET_SO2_BELOW_TARGET": "净烟气 SO₂ 低于当前目标",
    "OUTLET_SO2_INSIDE_TARGET_BAND": "净烟气 SO₂ 位于目标范围内",
    "OUTLET_SO2_FAST_RISE_EFFECT_RISK": "净烟气 SO₂ 正在快速上升，当前效果风险增加",
    "OUTLET_SO2_FAST_DROP_EFFECT_RISK": "净烟气 SO₂ 正在快速下降，当前效果风险增加",

    # 第二模块目标需求判断
    "SO2_EMERGENCY_ZONE": "净烟气 SO₂ 已进入紧急区，优先保障排放安全",
    "SO2_WARNING_ZONE": "净烟气 SO₂ 已进入预警区，控制策略优先保障排放安全",
    "SO2_INSIDE_TARGET_DEADBAND": "净烟气 SO₂ 已进入目标死区，当前无需主动调整供浆",
    "SO2_ABOVE_TARGET": "净烟气 SO₂ 高于目标，需要增强脱硫能力",
    "SO2_BELOW_TARGET": "净烟气 SO₂ 低于目标，具备保守减少供浆的空间",
    "SO2_LOW_BUT_DECREASE_GUARD_ACTIVE": "净烟气 SO₂ 虽低于目标，但减浆保护条件尚未满足",
    "TARGET_COMMAND_CHANGED": "运行目标刚刚发生变化",

    # FAST 动作包络
    "FAST_ENVELOPE_EMISSION_GUARD": "排放安全保护已限制快速扰动阶段的可选动作",
    "FAST_RISE_BLOCKS_ECONOMIC_DECREASE": "入口快速上升期间暂不允许以经济性为目的减少供浆",
    "FAST_RISE_PREEMPTIVE_INCREASE_PREFERRED": "入口快速上升且出口存在上升趋势，优先考虑提前增加供浆",
    "FAST_RISE_LOW_SO2_PROTECTIVE_OPTION": "虽然当前 SO₂ 较低，但入口快速上升时保留保护性加浆选项",
    "FAST_DROP_HOLDS_ECONOMIC_DECREASE": "入口快速下降期间先保持供浆，暂缓经济性减浆",
    "FAST_MIXED_CONSERVATIVE": "检测到混合方向扰动，采用保守控制策略",
    "FAST_DROP_RECOVERY_ECONOMIC_DECREASE_ALLOWED": "快速下降恢复阶段已允许小幅经济性减浆",
    "FAST_RECOVERY_CONSERVATIVE": "快速扰动恢复阶段采用保守控制策略",

    # 在线状态机 / 版本 / 工况
    "MODEL_VERSION_RELOADED": "供浆策略模型已切换到新版本",
    "CONDITION_INVALID": "当前工况识别无效，暂不执行供浆调整",
    "CONDITION_POLICY_VERSION_MISMATCH": "工况模型与供浆策略模型版本不一致",
    "REALTIME_INPUT_INVALID": "实时输入数据不完整或存在无效值",
    "CONDITION_NOT_STABLE": "当前工况尚未稳定，暂不执行常规供浆调整",
    "FAST_PROTECTION_DURING_CONDITION_WARMUP": "工况稳定窗口尚未完成，但快速扰动安全保护继续生效",
    "MODEL_RELOAD_HOLD": "模型刚完成切换，本周期保持供浆以避免切换瞬间误动作",
    "TARGET_TRANSITION_HOLD": "目标值刚发生变化，本周期先保持供浆",
    "TARGET_TRANSITION_HOLD_BYPASSED_BY_FAST": "目标切换保持规则被快速扰动安全保护临时绕过",
    "CONDITION_JUST_SWITCHED": "当前工况刚发生切换，本周期先保持供浆",
    "CONDITION_TRANSITION_HOLD_BYPASSED_BY_FAST": "工况切换保持规则被快速扰动安全保护临时绕过",
    "WAITING_EXECUTION_FEEDBACK": "正在等待上一条推荐的实际执行反馈",
    "WAITING_PREVIOUS_ACTION_EFFECT": "正在等待上一轮供浆动作产生效果，暂不重复调整",
    "MINIMUM_ACTION_INTERVAL_ACTIVE": "距离上一次实际动作时间过短，最小动作间隔保护生效",
    "MAXIMUM_ACTIONS_PER_HOUR_REACHED": "最近一小时动作次数已达到上限",
    "EXECUTION_FEEDBACK_TIMEOUT": "上一条推荐未在规定时间内收到执行反馈",
    "PROGRESSIVE_LIMIT_HOLD": "渐进控制限制要求当前保持供浆",
    "NO_EXECUTABLE_CANDIDATE": "当前没有满足安全和执行约束的可执行候选动作",

    # pH / 执行器
    "HOLD_ACTION": "本次决策为保持当前供浆",
    "RULE_DELTA": "当前动作幅度采用规则步长生成",
    "TOWER_EQUIVALENT_HISTORICAL_DELTA": "当前动作幅度采用本塔历史等效动作经验",
    "LEGACY_HISTORICAL_DELTA": "当前动作幅度采用历史分阀动作经验",
    "SUPPLY_PUMP_VALVE_AVAILABILITY_APPLIED": "已根据供浆泵可用状态限制可执行阀门",
    "SUPPLY_PUMP_CURRENT_INVALID_FAILSAFE": "供浆泵状态数据无效，已按故障安全方式限制动作",
    "VALVE_LIMITS_APPLIED": "已应用阀门边界和单次动作幅度限制",
}


def _code(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _code(value).upper()


def translate_experience_source(value: Any) -> str:
    code = _upper(value)
    return EXPERIENCE_SOURCE_TEXT.get(code, "其他经验来源" if code else "无可用经验")


def translate_magnitude(value: Any) -> str:
    code = _upper(value)
    return MAGNITUDE_TEXT.get(code, "未知" if not code else "其他")


def translate_direction(value: Any) -> str:
    code = _upper(value)
    return DIRECTION_TEXT.get(code, "未知")


def translate_decision_state(value: Any) -> str:
    code = _upper(value)
    return DECISION_STATE_TEXT.get(code, "未知状态" if not code else "其他状态")


def translate_control_mode(value: Any) -> str:
    code = _upper(value)
    return CONTROL_MODE_TEXT.get(code, "未知模式" if not code else "其他模式")


def _tower_name(tower_id: str) -> str:
    normalized = str(tower_id or "").strip().lower()
    if normalized == "xst":
        return "吸收塔"
    if normalized == "apt":
        return "二级塔"
    return f"塔 {tower_id}" if tower_id else "吸收塔"


def _looks_like_machine_code(text: str) -> bool:
    if not text:
        return False
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:-.")
    return text.upper() == text and all(ch in allowed for ch in text)


def translate_reason_code(value: Any) -> str:
    text = _code(value)
    if not text:
        return ""

    # 算法异常细节有时本身就是中文自然语言，直接保留。
    if not _looks_like_machine_code(text):
        return text

    upper = text.upper()
    if upper in REASON_TEXT:
        return REASON_TEXT[upper]

    if upper.startswith("FAST_DIRECTION:"):
        direction = upper.split(":", 1)[1]
        names = {"RISE": "上升", "DROP": "下降", "MIXED": "混合"}
        return f"快速扰动方向为{names.get(direction, '未知方向')}"

    if upper.startswith("EXPERIENCE_SOURCE:"):
        source = upper.split(":", 1)[1]
        return f"本次决策采用{translate_experience_source(source)}"

    if upper.startswith("PH_BELOW_SAFE_RANGE:"):
        tower = text.split(":", 1)[1]
        return f"{_tower_name(tower)}浆液 pH 低于安全范围"

    if upper.startswith("PH_ABOVE_SAFE_RANGE:"):
        tower = text.split(":", 1)[1]
        return f"{_tower_name(tower)}浆液 pH 高于安全范围"

    if upper.startswith("PROGRESSIVE_ACTION_CAP:"):
        magnitude = upper.split(":", 1)[1]
        return f"渐进控制将本次最大动作限制为{translate_magnitude(magnitude)}"

    if upper.startswith("ACTION_RESOLUTION_FAILED:"):
        detail = text.split(":", 1)[1]
        return f"执行器动作解析失败：{detail}"

    # 未知机器码不直接作为操作员正文显示；原始 code 会在详情页诊断区保留。
    return "其他算法诊断信息"


def translate_reason_codes(values: Iterable[Any]) -> List[str]:
    translated: List[str] = []
    for value in values or []:
        text = translate_reason_code(value)
        if text and text not in translated:
            translated.append(text)
    return translated


def _action_phrase(action: Any, magnitude: Any) -> str:
    action_text = _code(action)
    if not action_text:
        return "保持当前供浆"
    magnitude_text = translate_magnitude(magnitude)
    if "保持" in action_text or _upper(magnitude) == "HOLD":
        return "保持当前供浆"
    if magnitude_text in {"未知", "其他", "保持"}:
        return action_text
    return f"{magnitude_text}{action_text}"


def summarize_reason_codes(
    values: Iterable[Any],
    *,
    action: Any = "",
    magnitude: Any = "",
    decision_state: Any = "",
    control_mode: Any = "",
) -> str:
    """生成首页使用的一句中文决策摘要，不直接暴露英文 reason_code。"""

    reasons = [_code(value) for value in (values or []) if _code(value)]
    upper = [_upper(value) for value in reasons]
    reason_set = set(upper)
    decision = _upper(decision_state)
    mode = _upper(control_mode)

    natural_details = [
        value
        for value in reasons
        if value and not _looks_like_machine_code(value)
    ]

    if decision == "BLOCKED" or mode == "BLOCKED":
        if "REALTIME_INPUT_INVALID" in reason_set:
            if natural_details:
                return f"实时输入异常，当前控制已阻断：{natural_details[0]}"
            return "实时输入数据不完整或存在无效值，当前控制已阻断。"
        if "CONDITION_POLICY_VERSION_MISMATCH" in reason_set:
            return "工况模型与供浆策略模型版本不一致，当前控制已阻断。"
        if "CONDITION_INVALID" in reason_set:
            return "当前工况识别无效，暂不执行供浆调整。"
        return "当前存在控制安全条件不满足，供浆策略已进入阻断状态。"

    if "WAITING_PREVIOUS_ACTION_EFFECT" in reason_set:
        return "正在等待上一轮供浆动作产生效果，当前暂不重复调整。"
    if "WAITING_EXECUTION_FEEDBACK" in reason_set:
        return "上一条供浆建议正在等待实际执行反馈，当前暂不重复推荐。"
    if "MINIMUM_ACTION_INTERVAL_ACTIVE" in reason_set:
        return "距离上一次供浆动作时间过短，最小动作间隔保护正在生效。"
    if "CONDITION_NOT_STABLE" in reason_set:
        return "当前工况尚未稳定，暂不执行常规供浆调整。"

    if "SO2_EMERGENCY_ZONE" in reason_set or "OUTLET_SO2_EMERGENCY" in reason_set:
        return f"净烟气 SO₂ 已进入紧急区，优先保障排放安全，当前建议{_action_phrase(action, magnitude)}。"
    if "SO2_WARNING_ZONE" in reason_set or "OUTLET_SO2_WARNING" in reason_set:
        return f"净烟气 SO₂ 已进入预警区，控制优先保障排放安全，当前建议{_action_phrase(action, magnitude)}。"

    if "FAST_TREND_DETECTED" in reason_set or mode == "FAST_CHANGE":
        return f"检测到入口工况快速变化，系统处于快速扰动保护，当前建议{_action_phrase(action, magnitude)}。"

    below_far = "OUTLET_SO2_FAR_BELOW_TARGET" in reason_set
    below = below_far or "OUTLET_SO2_BELOW_TARGET" in reason_set or "SO2_BELOW_TARGET" in reason_set
    above_far = "OUTLET_SO2_FAR_ABOVE_TARGET" in reason_set
    above = above_far or "OUTLET_SO2_ABOVE_TARGET" in reason_set or "SO2_ABOVE_TARGET" in reason_set
    recovered = "FAST_RECOVERY_COMPLETED" in reason_set

    if below:
        prefix = "净烟气 SO₂ 明显低于目标" if below_far else "净烟气 SO₂ 低于目标"
        recovery = "，快速扰动恢复已完成" if recovered else ""
        return f"{prefix}{recovery}，当前建议{_action_phrase(action, magnitude)}。"

    if above:
        prefix = "净烟气 SO₂ 明显高于目标" if above_far else "净烟气 SO₂ 高于目标"
        recovery = "，快速扰动恢复已完成" if recovered else ""
        return f"{prefix}{recovery}，当前建议{_action_phrase(action, magnitude)}。"

    if (
        "SO2_INSIDE_TARGET_DEADBAND" in reason_set
        or "OUTLET_SO2_INSIDE_TARGET_BAND" in reason_set
    ):
        return "净烟气 SO₂ 已进入目标范围，当前建议保持供浆并继续观察过程响应。"

    if "NO_EXECUTABLE_CANDIDATE" in reason_set:
        return "当前没有满足安全和执行约束的可执行动作，系统保持现有供浆。"

    translated = translate_reason_codes(reasons)
    useful = [item for item in translated if item != "其他算法诊断信息"]
    if useful:
        return "；".join(useful[:2]) + "。"
    return "当前暂无需要向操作员提示的特殊决策原因。"
