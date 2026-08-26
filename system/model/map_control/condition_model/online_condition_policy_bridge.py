# -*- coding: utf-8 -*-
"""First-module output -> MFAC primary second-module bridge.

The historical class name ``SlurryPolicyOnlineBridge`` is retained only as a
short-lived compatibility API for ``OnlineConditionPolicyPipeline`` and
``Process4MapControl``.  It no longer imports or executes the removed
``slurry_policy_model`` implementation.

Primary runtime chain::

    condition_model output
    -> DynamicQbaseCalculator
    -> ContinuousTargetPublisher
    -> MFAC algorithm target

The bridge deliberately keeps production permissions closed.  Actual slurry
flow may be exposed as audit evidence by downstream tracking, but it is never a
fallback source for the algorithm target.  Full SO2/pH response learning and
residual arbitration remain owned by ``mfac_model.Scheme2RuntimeCoordinator``.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from system.model.map_control.mfac_model.continuous_target import (
    ONLINE_SHADOW,
    ContinuousTargetPublisher,
)
from system.model.map_control.mfac_model.qbase import DynamicQbaseCalculator


MFAC_PRIMARY_BRIDGE_VERSION = "SCHEME2_MFAC_PRIMARY_BRIDGE_V1"


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
        "1", "true", "yes", "y", "t", "on", "是"
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
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def csv_safe_row(row: Dict[str, Any]) -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (dict, list, tuple, set)):
            output[key] = compact_json(value)
        else:
            output[key] = value
    return output


def _active_version(pointer: Optional[Dict[str, Any]], fallback: str = "") -> str:
    value = dict(pointer or {})
    condition = value.get("condition")
    if not isinstance(condition, dict):
        condition = {}
    mfac = value.get("mfac")
    if not isinstance(mfac, dict):
        mfac = {}
    legacy = value.get("slurry_policy")
    if not isinstance(legacy, dict):
        legacy = {}
    return str(
        value.get("integrated_version")
        or mfac.get("version")
        or condition.get("version")
        or legacy.get("version")
        or fallback
        or ""
    ).strip()


class MFACPrimaryPolicy:
    """Compatibility policy object backed only by Scheme-2 MFAC primitives.

    At the current activation stage the residual is intentionally fixed at
    zero.  Therefore the primary second-module target is Dynamic Qbase.  The
    full coordinator may later provide a non-zero held residual after formal
    calibration/activation without changing this bridge contract.
    """

    def __init__(
        self,
        config_spec: Optional[str] = None,
        *,
        external_version_management: bool = False,
        active_pointer: Optional[Dict[str, Any]] = None,
        initial_runtime_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        del config_spec
        self.external_version_management = bool(external_version_management)
        self.active_pointer = dict(active_pointer or {})
        self.model_version = _active_version(self.active_pointer)
        self.condition_snapshot_version = self.model_version
        self.qbase_calculator = DynamicQbaseCalculator("xst")
        self.target_publisher = ContinuousTargetPublisher()
        self._last_decision: Dict[str, Any] = {}
        self._reload_count = 0
        runtime = dict(initial_runtime_state or {})
        restored_target = runtime.get("last_valid_algorithm_target")
        if restored_target is not None:
            try:
                self.target_publisher.restore_last_valid_algorithm_target(
                    float(restored_target)
                )
            except (TypeError, ValueError):
                pass

    def evaluate(
        self,
        enriched_row: Dict[str, Any],
        *,
        target: Optional[Any] = None,
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        del execution_context
        timestamp = enriched_row.get("date", enriched_row.get("timestamp", ""))
        qbase = self.qbase_calculator.calculate(
            enriched_row,
            target_so2=target,
        )
        algorithm = self.target_publisher.publish(
            qbase.qbase_effective,
            0.0,
            inputs_valid=bool(qbase.valid),
            timestamp=str(timestamp or ""),
            replay_semantics=ONLINE_SHADOW,
        )
        target_value = algorithm.algorithm_target_supply_flow
        reason_codes = list(qbase.reason_codes)
        if algorithm.algorithm_target_status != "CALCULATED":
            reason_codes.append(algorithm.algorithm_target_status)
        if not reason_codes:
            reason_codes = ["MFAC_PRIMARY_CALCULATED"]

        decision_status = (
            "VALID"
            if algorithm.algorithm_target_valid and target_value is not None
            else "HOLD"
        )
        decision = {
            "decision_id": "MFAC-%s" % str(timestamp or ""),
            "timestamp": str(timestamp or ""),
            "model_type": "MFAC",
            "bridge_version": MFAC_PRIMARY_BRIDGE_VERSION,
            "model_version": self.model_version,
            "condition_snapshot_version": enriched_row.get(
                "condition_snapshot_version",
                self.condition_snapshot_version,
            ),
            "condition_label": enriched_row.get("condition_label"),
            "base_condition_id": enriched_row.get("base_condition_id"),
            "grid_id": enriched_row.get("grid_id"),
            "policy_region_id": enriched_row.get("policy_region_id"),
            "control_mode": "MFAC_PRIMARY_SHADOW",
            "disturbance_mode": enriched_row.get(
                "fast_change_mode", "NORMAL"
            ),
            "current_so2": enriched_row.get("jyq_SO2"),
            "commanded_target": target,
            "effective_target": target,
            "experience_source": "MFAC_RUNTIME",
            "action_id": "MFAC_TARGET",
            "action_family": "MFAC_TARGET",
            "action_direction": "CONTINUOUS_TARGET",
            "action_magnitude": "CONTINUOUS",
            "decision_status": decision_status,
            "reason_codes": reason_codes,
            "qbase": qbase.to_dict(),
            "qbase_raw": qbase.qbase_raw,
            "qbase_effective": qbase.qbase_effective,
            "residual_mfac_hold": 0.0,
            "algorithm_target_supply_flow": target_value,
            "algorithm_target_valid": algorithm.algorithm_target_valid,
            "algorithm_target_status": algorithm.algorithm_target_status,
            "algorithm_target": algorithm.to_dict(),
            "learn_enabled": False,
            "residual_enabled": False,
            "dcs_write_enabled": False,
            "target_supply_flow": {
                "mode": "TARGET_SUPPLY_FLOW",
                "available": target_value is not None,
                "value": target_value,
                "valid": algorithm.algorithm_target_valid,
                "status": algorithm.algorithm_target_status,
                "unit": "m3/h",
                "reason_codes": reason_codes,
            },
            "control_recommendation": {
                "requested_mode": "TARGET_SUPPLY_FLOW",
                "effective_mode": "MFAC_PRIMARY_SHADOW",
                "primary": {
                    "recommendation_type": "MFAC_TARGET_SUPPLY_FLOW",
                    "actionable": False,
                    "target_supply_flow": target_value,
                },
                "automatic_mode_switch": False,
                "legacy_compatibility_fields_preserved": True,
            },
            "target_flow_execution_preview": {
                "adapter_mode": "DRY_RUN",
                "status": "SHADOW_ONLY",
                "command_issued": False,
                "dcs_write_attempted": False,
                "reason_codes": ["MFAC_DCS_WRITE_DISABLED"],
                "phases": [],
            },
            "debug": {
                "actual_flow_used_as_algorithm_target": False,
                "target_formula": "clip(qbase_effective + residual_mfac_hold)",
                "legacy_second_module_executed": False,
            },
        }
        self._last_decision = deepcopy(decision)
        return decision

    def record_execution(self, feedback: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "accepted": False,
            "status": "MFAC_FORMAL_DCS_ADAPTER_NOT_ENABLED",
            "feedback": dict(feedback or {}),
            "dcs_write_attempted": False,
        }

    def export_runtime_state(self) -> Dict[str, Any]:
        return {
            "last_valid_algorithm_target": (
                self.target_publisher.last_valid_algorithm_target
            ),
            "last_decision": deepcopy(self._last_decision),
        }

    def mark_external_reload(self) -> None:
        self._reload_count += 1

    def status(self) -> Dict[str, Any]:
        return {
            "model_type": "MFAC",
            "model_version": self.model_version,
            "condition_snapshot_version": self.condition_snapshot_version,
            "bridge_version": MFAC_PRIMARY_BRIDGE_VERSION,
            "learn_enabled": False,
            "residual_enabled": False,
            "dcs_write_enabled": False,
            "reload_count": self._reload_count,
        }


class SlurryPolicyOnlineBridge:
    """Deprecated name for the MFAC primary second-module bridge.

    The object intentionally preserves the methods expected by the existing
    condition pipeline so the algorithm can be replaced before UI/DB field
    names are fully migrated.
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
        self.output_prefix = str(self.config.get("output_prefix", "mfac_"))
        self.legacy_output_prefix = str(
            self.config.get("legacy_output_prefix", "slurry_policy_")
        )
        self.emit_legacy_compatibility = bool(
            self.config.get("emit_legacy_compatibility", True)
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
        return MFACPrimaryPolicy(config_spec=config_spec, **kwargs)

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
        self._policy = None
        return self._ensure_policy() is not None

    def export_runtime_state(self) -> Dict[str, Any]:
        policy = self._ensure_policy()
        if policy is None:
            return {}
        exporter = getattr(policy, "export_runtime_state", None)
        return dict(exporter()) if callable(exporter) else {}

    def create_candidate(
        self,
        active_pointer: Dict[str, Any],
        *,
        initial_runtime_state: Optional[Dict[str, Any]] = None,
    ) -> Any:
        return self._build_policy(
            active_pointer=dict(active_pointer),
            initial_runtime_state=initial_runtime_state,
        )

    def replace_policy(self, policy: Any, *, mark_reloaded: bool = True) -> None:
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
            context.get("automatic_control_allowed"), False
        )
        context["supply_pump_state_changing"] = _as_bool(
            context.get("supply_pump_state_changing"), False
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
        return {
            "decision_id": None,
            "timestamp": enriched_row.get("date", enriched_row.get("timestamp")),
            "model_type": "MFAC",
            "model_version": None,
            "condition_snapshot_version": enriched_row.get(
                "condition_snapshot_version"
            ),
            "condition_label": enriched_row.get("condition_label"),
            "control_mode": "BLOCKED",
            "commanded_target": target,
            "effective_target": target,
            "action_id": "HOLD",
            "action_family": "HOLD",
            "action_direction": "HOLD",
            "action_magnitude": "HOLD",
            "decision_status": "BLOCKED",
            "reason_codes": ["MFAC_PRIMARY_INTEGRATION_ERROR", str(error)],
            "learn_enabled": False,
            "residual_enabled": False,
            "dcs_write_enabled": False,
            "target_supply_flow": {
                "mode": "TARGET_SUPPLY_FLOW",
                "available": False,
                "reason_codes": ["MFAC_PRIMARY_INTEGRATION_ERROR"],
            },
            "control_recommendation": {
                "requested_mode": "TARGET_SUPPLY_FLOW",
                "effective_mode": "BLOCKED",
                "primary": {
                    "recommendation_type": "HOLD",
                    "actionable": False,
                },
                "automatic_mode_switch": False,
            },
            "target_flow_execution_preview": {
                "adapter_mode": "DRY_RUN",
                "status": "BLOCKED",
                "command_issued": False,
                "dcs_write_attempted": False,
                "reason_codes": ["MFAC_PRIMARY_INTEGRATION_ERROR"],
                "phases": [],
            },
            "debug": {"integration_error": str(error)},
        }

    def evaluate(
        self,
        enriched_row: Dict[str, Any],
        *,
        target: Optional[Any] = None,
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resolved_target = self.resolve_target(enriched_row, target)
        resolved_execution = self.resolve_execution_context(
            enriched_row, execution_context
        )
        if not self.enabled:
            return self._blocked_decision(
                enriched_row,
                "MFAC_PRIMARY_INTEGRATION_DISABLED",
                resolved_target,
            )
        policy = self._ensure_policy()
        if policy is None:
            return self._blocked_decision(
                enriched_row,
                self._initialization_error or "MFAC_PRIMARY_NOT_INITIALIZED",
                resolved_target,
            )
        try:
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
                "MFAC_PRIMARY_EVALUATE_FAILED:%s" % exc,
                resolved_target,
            )

    @staticmethod
    def _append_prefixed(
        output: Dict[str, Any],
        prefix: str,
        decision: Dict[str, Any],
    ) -> None:
        for key, value in decision.items():
            output_key = "%s%s" % (prefix, key)
            if output_key in output:
                preserved_key = "input_original__%s" % output_key
                suffix = 2
                while preserved_key in output:
                    preserved_key = "input_original__%s__%d" % (
                        output_key, suffix
                    )
                    suffix += 1
                output[preserved_key] = output[output_key]
            output[output_key] = value

    def append_to_output(
        self,
        base_row: Dict[str, Any],
        decision: Dict[str, Any],
    ) -> Dict[str, Any]:
        output = dict(base_row)
        self._append_prefixed(output, self.output_prefix, decision)
        output["%sintegration_valid" % self.output_prefix] = (
            decision.get("decision_status") != "BLOCKED"
        )
        debug = decision.get("debug")
        error = debug.get("integration_error") if isinstance(debug, dict) else None
        output["%sintegration_error" % self.output_prefix] = error or ""
        output["%soutput_json" % self.output_prefix] = compact_json(decision)

        if self.emit_legacy_compatibility and self.legacy_output_prefix:
            self._append_prefixed(
                output,
                self.legacy_output_prefix,
                decision,
            )
            output["%sintegration_valid" % self.legacy_output_prefix] = (
                decision.get("decision_status") != "BLOCKED"
            )
            output["%sintegration_error" % self.legacy_output_prefix] = error or ""
            output["%soutput_json" % self.legacy_output_prefix] = compact_json(
                decision
            )
            output["slurry_policy_deprecated_compat"] = True
            output["slurry_policy_backend"] = "MFAC"

        output["second_module_type"] = "MFAC"
        output["second_module_algorithm_target_supply_flow"] = decision.get(
            "algorithm_target_supply_flow"
        )
        output["second_module_dcs_write_enabled"] = False
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
                self._initialization_error or "MFAC primary policy unavailable"
            )
        return dict(policy.record_execution(dict(feedback)))

    def status(self) -> Dict[str, Any]:
        policy = self._ensure_policy()
        if policy is None:
            return {
                "enabled": self.enabled,
                "ready": False,
                "backend": "MFAC",
                "initialization_error": self._initialization_error,
            }
        value = dict(policy.status())
        value.update(
            {
                "enabled": self.enabled,
                "ready": True,
                "backend": "MFAC",
                "external_version_management": self.external_version_management,
                "legacy_name_only": True,
            }
        )
        return value
