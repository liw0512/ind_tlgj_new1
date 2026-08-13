from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAST_CONFIG = ROOT / 'system/model/map_control/fast_change_mode/fast_change_config.py'
FAST_MANAGER = ROOT / 'system/model/map_control/fast_change_mode/fast_change_history_manager.py'
ONLINE_CONDITION = ROOT / 'system/model/map_control/condition_model/online_condition_classifier.py'
FAST_ADAPTER = ROOT / 'system/model/map_control/slurry_policy_model/slurry_policy_online/fast_context_adapter.py'


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8-sig')


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding='utf-8')


# 1) FAST config: generic upstream input guard, starting with jym calibration code.
text = read(FAST_CONFIG)
needle = '''    # ------------------------------------------------------------------\n    # 入口趋势风险。\n'''
insert = '''    # ------------------------------------------------------------------\n    # 在线输入保护。\n    # 这些字段只决定“当前帧能不能推进 FAST / condition 在线状态”，不参与趋势计算。\n    # 命中任一 invalid_field_values 后：FAST DEMA/窗口/状态机不推进，第一模块 majority\n    # 窗口也不推进，第二模块收到 condition_valid=False 后安全 HOLD。\n    # ------------------------------------------------------------------\n    "input_guard": {\n        "enabled": True,\n        "invalid_field_values": {\n            # jym=100 为现场测点校验态；数值字符串 100/100.0 也按同一值处理。\n            "jym": [100],\n        },\n        # 字段缺失时不因为本 guard 单独阻断；缺失/坏点仍由各模块自己的必需字段校验处理。\n        "missing_field_is_valid": True,\n    },\n\n'''
if '"input_guard": {' not in text:
    if needle not in text:
        raise SystemExit('FAST config insertion marker not found')
    text = text.replace(needle, insert + needle, 1)
write(FAST_CONFIG, text)


# 2) FAST manager: expose guard and a non-mutating blocked context.
text = read(FAST_MANAGER)
text = text.replace(
    '    "fast_change_input_valid",\n    "fast_change_reason_codes",\n',
    '    "fast_change_input_valid",\n    "fast_change_state_advanced",\n    "fast_change_input_guard_reason",\n    "fast_change_reason_codes",\n',
    1,
)
marker = '''    def annotate_dataframe(\n'''
methods = '''    @staticmethod\n    def _guard_value_matches(value: Any, expected: Any) -> bool:\n        if value is None:\n            return expected is None\n        try:\n            left = float(value)\n            right = float(expected)\n            if pd.notna(left) and pd.notna(right):\n                return left == right\n        except (TypeError, ValueError):\n            pass\n        return str(value).strip() == str(expected).strip()\n\n    def input_guard_reason(self, row: Mapping[str, Any]) -> Optional[str]:\n        \"\"\"返回阻断原因；None 表示该帧允许推进 FAST/condition 在线状态。\"\"\"\n        guard = dict(self.config.get("input_guard") or {})\n        if not bool(guard.get("enabled", True)):\n            return None\n        missing_is_valid = bool(guard.get("missing_field_is_valid", True))\n        for field, invalid_values in dict(guard.get("invalid_field_values") or {}).items():\n            if field not in row or row.get(field) in (None, ""):\n                if missing_is_valid:\n                    continue\n                return f"FAST_INPUT_GUARD_MISSING_FIELD:{field}"\n            value = row.get(field)\n            for invalid in list(invalid_values or []):\n                if self._guard_value_matches(value, invalid):\n                    return f"FAST_INPUT_GUARD_INVALID_VALUE:{field}={value}"\n        return None\n\n    def blocked_online_context(\n        self,\n        row: Mapping[str, Any],\n        *,\n        target: Optional[Any] = None,\n        reason: str,\n    ) -> Dict[str, Any]:\n        \"\"\"返回不推进 detector 的冻结上下文，用于校验/无效实时帧。\"\"\"\n        state = self.detector.get_state()\n        mode = str(state.get("mode", REGULAR))\n        direction = (\n            str(state.get("last_fast_direction", "NONE"))\n            if mode in {FAST_CHANGE, FAST_RECOVERY}\n            else "NONE"\n        )\n        exact = (\n            str(state.get("last_fast_exact_mode", "STEADY"))\n            if mode in {FAST_CHANGE, FAST_RECOVERY}\n            else "STEADY"\n        )\n        axes = [str(axis.get("column", "")) for axis in self.detector.axes]\n        try:\n            emission_limit = float(self.plant.get("outlet_so2_safe_range", [0.0, 35.0])[1])\n        except Exception:\n            emission_limit = 35.0\n        try:\n            target_value = float(target) if target not in (None, "") else None\n        except (TypeError, ValueError):\n            target_value = None\n        return {\n            "fast_change_mode": mode,\n            "fast_change_active": mode == FAST_CHANGE,\n            "fast_change_recovery_active": mode == FAST_RECOVERY,\n            "fast_change_raw_trigger": False,\n            "fast_change_direction": direction,\n            "fast_change_severity": "BLOCKED",\n            "fast_change_exact_trend_mode": exact,\n            "fast_change_raw_exact_trend_mode": "STEADY",\n            "fast_change_trend_risk_level": "UNKNOWN",\n            "fast_change_effect_risk_level": "UNKNOWN",\n            "fast_change_effect_state": "INPUT_BLOCKED",\n            "fast_change_effect_direction": "UNKNOWN",\n            "fast_change_overall_risk_level": "UNKNOWN",\n            "fast_change_axis_columns": axes,\n            "fast_change_axis_rates": {column: None for column in axes},\n            "fast_change_axis_levels": {column: "INPUT_BLOCKED" for column in axes},\n            "fast_change_axis_direction_ratios": {\n                column: {"rise": None, "drop": None} for column in axes\n            },\n            "fast_change_trigger_axes": [],\n            "fast_change_available_axis_count": 0,\n            "fast_change_trend_ready": False,\n            "fast_change_current_so2": None,\n            "fast_change_target_so2": target_value,\n            "fast_change_target_error": None,\n            "fast_change_emission_limit": emission_limit,\n            "fast_change_outlet_so2_rate": None,\n            "fast_change_outlet_so2_trend": "UNKNOWN",\n            "fast_change_input_valid": False,\n            "fast_change_state_advanced": False,\n            "fast_change_input_guard_reason": str(reason),\n            "fast_change_reason_codes": [\n                "FAST_INPUT_GUARD_BLOCKED",\n                str(reason),\n                "FAST_STATE_NOT_ADVANCED",\n            ],\n            "fast_change_state": state,\n            "fast_change_debug": {"input_guard_reason": str(reason)},\n        }\n\n'''
if 'def input_guard_reason' not in text:
    if marker not in text:
        raise SystemExit('FAST manager method insertion marker not found')
    text = text.replace(marker, methods + marker, 1)
old = '''    def evaluate_online(\n        self,\n        row: Mapping[str, Any],\n        *,\n        target: Optional[Any] = None,\n    ) -> Dict[str, Any]:\n        context = self.detector.evaluate(row, target=target)\n        closed = self._observe(context, timestamp=row.get(TIME_COLUMN))\n        self._sample_count += 1\n'''
new = '''    def evaluate_online(\n        self,\n        row: Mapping[str, Any],\n        *,\n        target: Optional[Any] = None,\n    ) -> Dict[str, Any]:\n        guard_reason = self.input_guard_reason(row)\n        if guard_reason is not None:\n            return self.blocked_online_context(\n                row, target=target, reason=guard_reason\n            )\n        context = self.detector.evaluate(row, target=target)\n        context["fast_change_state_advanced"] = True\n        context["fast_change_input_guard_reason"] = ""\n        closed = self._observe(context, timestamp=row.get(TIME_COLUMN))\n        self._sample_count += 1\n'''
if old not in text:
    raise SystemExit('FAST manager evaluate_online block not found')
text = text.replace(old, new, 1)
write(FAST_MANAGER, text)


# 3) First-module online pipeline owns the guard. Invalid frame advances neither FAST nor majority window.
text = read(ONLINE_CONDITION)
marker = '''    def classify(self, realtime: Dict[str, Any]) -> OnlineConditionResult:\n'''
blocked = '''    def blocked_result(\n        self,\n        realtime: Dict[str, Any],\n        reason: str,\n    ) -> OnlineConditionResult:\n        \"\"\"Return a safe invalid result without advancing the majority window.\"\"\"\n        return self._invalid_result(\n            build_state_key(realtime),\n            str(reason),\n        )\n\n'''
if 'def blocked_result' not in text:
    if marker not in text:
        raise SystemExit('condition blocked_result insertion marker not found')
    text = text.replace(marker, blocked + marker, 1)
old = '''            original = dict(realtime)\n            # FAST must be evaluated before condition majority stabilization.  It is an\n            # upstream disturbance fact, not a second-module internal classifier.\n            fast_context = self.fast_change_manager.evaluate_online(original, target=target)\n            original = _preserving_update(original, fast_context)\n            condition_result = self.classifier.classify(original)\n'''
new = '''            original = dict(realtime)\n            # FAST is owned by the first-module online pipeline and must run before\n            # condition majority stabilization.  Calibration/invalid guard frames are\n            # frozen here: neither FAST short-window state nor the condition majority\n            # window is allowed to advance.  P4PC does not own or call FAST directly.\n            guard_reason = self.fast_change_manager.input_guard_reason(original)\n            if guard_reason is not None:\n                fast_context = self.fast_change_manager.blocked_online_context(\n                    original,\n                    target=target,\n                    reason=guard_reason,\n                )\n                original = _preserving_update(original, fast_context)\n                condition_result = self.classifier.blocked_result(\n                    original,\n                    "UPSTREAM_INPUT_GUARD_BLOCKED:%s" % guard_reason,\n                )\n            else:\n                fast_context = self.fast_change_manager.evaluate_online(\n                    original, target=target\n                )\n                original = _preserving_update(original, fast_context)\n                condition_result = self.classifier.classify(original)\n'''
if old not in text:
    raise SystemExit('pipeline FAST process block not found')
text = text.replace(old, new, 1)
write(ONLINE_CONDITION, text)


# 4) Second module refuses any upstream FAST context explicitly marked invalid.
text = read(FAST_ADAPTER)
old = '''    required = (\n        "fast_change_mode",\n        "fast_change_direction",\n        "fast_change_exact_trend_mode",\n        "fast_change_effect_risk_level",\n        "fast_change_overall_risk_level",\n        "fast_change_outlet_so2_rate",\n    )\n'''
new = '''    required = (\n        "fast_change_mode",\n        "fast_change_direction",\n        "fast_change_exact_trend_mode",\n        "fast_change_effect_risk_level",\n        "fast_change_overall_risk_level",\n        "fast_change_outlet_so2_rate",\n        "fast_change_input_valid",\n    )\n'''
if old not in text:
    raise SystemExit('fast adapter required block not found')
text = text.replace(old, new, 1)
old = '''    context = {key: value for key, value in process.items() if str(key).startswith("fast_change_")}\n    context["fast_change_axis_rates"] = _mapping(process.get("fast_change_axis_rates"))\n    return context\n'''
new = '''    context = {key: value for key, value in process.items() if str(key).startswith("fast_change_")}\n    valid_value = process.get("fast_change_input_valid")\n    if isinstance(valid_value, str):\n        valid = valid_value.strip().lower() in {"1", "true", "yes", "y", "t"}\n    else:\n        valid = bool(valid_value)\n    if not valid:\n        reason = process.get("fast_change_input_guard_reason") or process.get(\n            "fast_change_reason_codes"\n        )\n        raise FastContextError(\n            "上游 FAST 输入无效，禁止第二模块自动动作: %s" % reason\n        )\n    context["fast_change_axis_rates"] = _mapping(process.get("fast_change_axis_rates"))\n    return context\n'''
if old not in text:
    raise SystemExit('fast adapter return block not found')
text = text.replace(old, new, 1)
write(FAST_ADAPTER, text)
