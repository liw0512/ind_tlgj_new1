# -*- coding: utf-8 -*-
"""第一模块在线输出与第二模块在线策略的桥接层。

职责边界：
1. 保留第一模块收到的全部原始输入字段；
2. 接收第一模块追加后的完整工况字段；
3. 将“原始输入 + 第一模块全部输出”原样传给第二模块 OnlineSlurryPolicy；
4. 将第二模块返回的全部字段以前缀形式追加到最终输出，避免覆盖第一模块字段；
5. 第二模块加载或推理失败时采用安全 HOLD/BLOCKED 输出，不破坏第一模块结果。

本文件不修改第一模块离线工况划分，也不修改第二模块离线训练或在线推理算法。
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Dict, List, Optional

import pandas as pd


_POLICY_BLOCKED_TEMPLATE = {
    "decision_id": None,
    "timestamp": None,
    "model_version": None,
    "condition_snapshot_version": None,
    "condition_label": None,
    "raw_grid_id": None,
    "control_mode": "BLOCKED",
    "disturbance_mode": "UNKNOWN",
    "current_so2": None,
    "commanded_target": None,
    "effective_target": None,
    "desired_so2_response": "UNKNOWN",
    "experience_source": "NONE",
    "action_id": "HOLD",
    "action_family": "HOLD",
    "action_direction": "HOLD",
    "action_magnitude": "HOLD",
    "recommended_valve_deltas": {},
    "projected_valve_openings": {},
    "historical_reliability": None,
    "historical_safety_score": None,
    "historical_direction_consistency": None,
    "decision_status": "BLOCKED",
    "reason_codes": ["SLURRY_POLICY_INTEGRATION_ERROR"],
    "debug": {},
}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float):
        return not math.isfinite(value)
    try:
        missing = pd.isna(value)
    except Exception:
        return False
    return bool(missing) if isinstance(missing, bool) else False


def _as_bool(value: Any, default: bool = False) -> bool:
    if _is_missing(value):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "t",
        "on",
        "是",
    }


def _as_list(value: Any) -> List[str]:
    if _is_missing(value):
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if not _is_missing(item)]
    text = str(value).strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except Exception:
        decoded = None
    if isinstance(decoded, list):
        return [str(item) for item in decoded if not _is_missing(item)]
    return [item.strip() for item in text.split(",") if item.strip()]


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def compact_json(value: Any) -> str:
    """Return deterministic UTF-8 JSON for CSV cells and audit output."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def csv_safe_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize nested objects without altering scalar columns."""

    output: Dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (dict, list, tuple, set)):
            output[key] = compact_json(value)
        else:
            output[key] = value
    return output


class SlurryPolicyOnlineBridge:
    """Lazy adapter around ``OnlineSlurryPolicy``.

    ``policy_instance`` and ``policy_factory`` are dependency-injection hooks
    used by tests and by applications that manage a singleton policy object.
    In production, when neither is supplied, the bridge imports
    ``system.model.map_control.slurry_policy_model.slurry_policy_online``.
    """

    def __init__(
        self,
        integration_config: Optional[Dict[str, Any]] = None,
        *,
        policy_instance: Optional[Any] = None,
        policy_factory: Optional[Callable[..., Any]] = None,
        initial_active_pointer: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.config = dict(integration_config or {})
        self.enabled = bool(self.config.get("enabled", True))
        self.output_prefix = str(
            self.config.get("output_prefix", "slurry_policy_")
        )
        self.failure_mode = str(
            self.config.get("failure_mode", "BLOCKED_OUTPUT")
        ).upper()
        self.config_spec = self.config.get("config_spec")
        self._policy = policy_instance
        self._policy_factory = policy_factory
        self._initial_active_pointer = (
            dict(initial_active_pointer)
            if initial_active_pointer is not None
            else None
        )
        self.external_version_management = bool(
            self.config.get("external_version_management", False)
        )
        self._initialization_error: Optional[str] = None

        if self.enabled and bool(self.config.get("initialize_on_start", True)):
            self._ensure_policy()

    @property
    def policy(self) -> Optional[Any]:
        return self._policy

    @property
    def initialization_error(self) -> Optional[str]:
        return self._initialization_error

    def _default_factory(
        self,
        config_spec: Optional[str],
        **kwargs: Any,
    ) -> Any:
        from system.model.map_control.slurry_policy_model.slurry_policy_online import (
            OnlineSlurryPolicy,
        )

        return OnlineSlurryPolicy(config_spec=config_spec, **kwargs)

    def _build_policy(
        self,
        *,
        active_pointer: Optional[Dict[str, Any]],
        initial_runtime_state: Optional[Dict[str, Any]] = None,
    ) -> Any:
        factory = self._policy_factory or self._default_factory
        kwargs = {
            "external_version_management": self.external_version_management,
            "active_pointer": active_pointer,
            "initial_runtime_state": initial_runtime_state,
        }
        try:
            return factory(self.config_spec, **kwargs)
        except TypeError:
            # 兼容早期测试注入的 factory(config_spec) 形式。正式默认工厂支持上面参数。
            return factory(self.config_spec)

    def _ensure_policy(self) -> Optional[Any]:
        if not self.enabled:
            return None
        if self._policy is not None:
            return self._policy
        try:
            self._policy = self._build_policy(
                active_pointer=self._initial_active_pointer,
            )
            self._initialization_error = None
            return self._policy
        except Exception as exc:
            self._initialization_error = str(exc)
            if self.failure_mode == "RAISE":
                raise
            return None

    def reload(self) -> bool:
        """Retry initialization after an earlier model/version loading failure."""

        self._policy = None
        return self._ensure_policy() is not None

    def export_runtime_state(self) -> Dict[str, Any]:
        policy = self._ensure_policy()
        if policy is None:
            return {}
        exporter = getattr(policy, "export_runtime_state", None)
        if callable(exporter):
            return dict(exporter())
        return {}

    def create_candidate(
        self,
        active_pointer: Dict[str, Any],
        *,
        initial_runtime_state: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """创建并完整验证候选第二模块，不影响当前正式对象。"""

        return self._build_policy(
            active_pointer=dict(active_pointer),
            initial_runtime_state=initial_runtime_state,
        )

    def replace_policy(self, policy: Any, *, mark_reloaded: bool = True) -> None:
        """由集成版本管理器在同一锁内提交候选策略。"""

        self._policy = policy
        self._initial_active_pointer = None
        self._initialization_error = None
        if mark_reloaded:
            notifier = getattr(policy, "mark_external_reload", None)
            if callable(notifier):
                notifier()

    def loaded_versions(self) -> Dict[str, Optional[str]]:
        policy = self._policy
        if policy is None:
            return {"policy_version": None, "condition_version": None}
        status = dict(policy.status())
        return {
            "policy_version": status.get("model_version"),
            "condition_version": status.get("condition_snapshot_version"),
        }

    def resolve_target(
        self,
        enriched_row: Dict[str, Any],
        explicit_target: Optional[Any] = None,
    ) -> Optional[float]:
        if explicit_target is not None and not _is_missing(explicit_target):
            return float(explicit_target)

        target_column = self.config.get("target_column")
        if target_column:
            value = enriched_row.get(str(target_column))
            if not _is_missing(value):
                return float(value)

        fixed_target = self.config.get("fixed_target")
        if fixed_target is not None and not _is_missing(fixed_target):
            return float(fixed_target)

        # None tells OnlineSlurryPolicy to use its own configured default or
        # previously retained runtime target.
        return None

    def resolve_execution_context(
        self,
        enriched_row: Dict[str, Any],
        explicit_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        context = dict(self.config.get("default_execution_context") or {})
        columns = dict(self.config.get("execution_context_columns") or {})

        scalar_bool_fields = {
            "automatic_control_allowed",
            "supply_pump_state_changing",
        }
        list_fields = {"manual_valves", "faulted_valves"}

        for context_key, column_name in columns.items():
            if not column_name or str(column_name) not in enriched_row:
                continue
            raw_value = enriched_row[str(column_name)]
            if context_key in scalar_bool_fields:
                context[context_key] = _as_bool(
                    raw_value,
                    bool(context.get(context_key, False)),
                )
            elif context_key in list_fields:
                context[context_key] = _as_list(raw_value)
            elif not _is_missing(raw_value):
                context[context_key] = raw_value

        if explicit_context:
            context.update(dict(explicit_context))

        context["automatic_control_allowed"] = _as_bool(
            context.get("automatic_control_allowed"),
            False,
        )
        context["supply_pump_state_changing"] = _as_bool(
            context.get("supply_pump_state_changing"),
            False,
        )
        context["manual_valves"] = _as_list(context.get("manual_valves"))
        context["faulted_valves"] = _as_list(context.get("faulted_valves"))
        return context

    def _blocked_decision(
        self,
        enriched_row: Dict[str, Any],
        error: str,
        target: Optional[float],
    ) -> Dict[str, Any]:
        decision = dict(_POLICY_BLOCKED_TEMPLATE)
        decision["timestamp"] = enriched_row.get(
            "timestamp",
            enriched_row.get("date"),
        )
        decision["condition_snapshot_version"] = enriched_row.get(
            "condition_snapshot_version"
        )
        decision["condition_label"] = enriched_row.get("condition_label")
        decision["raw_grid_id"] = enriched_row.get("raw_grid_id")
        decision["current_so2"] = enriched_row.get("jyq_SO2")
        decision["commanded_target"] = target
        decision["effective_target"] = target
        decision["reason_codes"] = [
            "SLURRY_POLICY_INTEGRATION_ERROR",
            str(error),
        ]
        decision["debug"] = {"integration_error": str(error)}
        return decision

    def evaluate(
        self,
        enriched_row: Dict[str, Any],
        *,
        target: Optional[Any] = None,
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Pass the complete first-module output to the second module."""

        resolved_target = self.resolve_target(enriched_row, target)
        resolved_execution = self.resolve_execution_context(
            enriched_row,
            execution_context,
        )

        if not self.enabled:
            return self._blocked_decision(
                enriched_row,
                "SLURRY_POLICY_INTEGRATION_DISABLED",
                resolved_target,
            )

        policy = self._ensure_policy()
        if policy is None:
            return self._blocked_decision(
                enriched_row,
                self._initialization_error or "SLURRY_POLICY_NOT_INITIALIZED",
                resolved_target,
            )

        try:
            # enriched_row already contains every original input field and every
            # condition field; no field subset or reconstruction is performed.
            return dict(
                policy.evaluate(
                    dict(enriched_row),
                    target=resolved_target,
                    execution_context=resolved_execution,
                )
            )
        except Exception as exc:
            if self.failure_mode == "RAISE":
                raise
            return self._blocked_decision(
                enriched_row,
                "SLURRY_POLICY_EVALUATE_FAILED:%s" % exc,
                resolved_target,
            )

    def append_to_output(
        self,
        base_row: Dict[str, Any],
        decision: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Append every second-module field with a collision-safe prefix."""

        output = dict(base_row)
        for key, value in decision.items():
            output_key = "%s%s" % (self.output_prefix, key)
            if output_key in output:
                preserved_key = "input_original__%s" % output_key
                suffix = 2
                while preserved_key in output:
                    preserved_key = "input_original__%s__%d" % (
                        output_key,
                        suffix,
                    )
                    suffix += 1
                output[preserved_key] = output[output_key]
            output[output_key] = value

        output["%sintegration_valid" % self.output_prefix] = (
            decision.get("decision_status") != "BLOCKED"
            or "SLURRY_POLICY_INTEGRATION_ERROR"
            not in list(decision.get("reason_codes") or [])
        )
        error = None
        debug = decision.get("debug")
        if isinstance(debug, dict):
            error = debug.get("integration_error")
        output["%sintegration_error" % self.output_prefix] = error or ""
        output["%soutput_json" % self.output_prefix] = compact_json(decision)
        return output

    def process(
        self,
        enriched_row: Dict[str, Any],
        *,
        target: Optional[Any] = None,
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        decision = self.evaluate(
            enriched_row,
            target=target,
            execution_context=execution_context,
        )
        return self.append_to_output(enriched_row, decision)

    def record_execution(self, feedback: Dict[str, Any]) -> Dict[str, Any]:
        policy = self._ensure_policy()
        if policy is None:
            raise RuntimeError(
                self._initialization_error or "slurry policy is unavailable"
            )
        return dict(policy.record_execution(dict(feedback)))

    def status(self) -> Dict[str, Any]:
        policy = self._ensure_policy()
        if policy is None:
            return {
                "enabled": self.enabled,
                "ready": False,
                "initialization_error": self._initialization_error,
            }
        value = dict(policy.status())
        value.update(
            {
                "enabled": self.enabled,
                "ready": True,
                "external_version_management": self.external_version_management,
            }
        )
        return value
