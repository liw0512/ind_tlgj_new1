# -*- coding: utf-8 -*-
"""First-module output -> unified Scheme-2 MFAC runtime bridge.

``SlurryPolicyOnlineBridge`` is retained only as a temporary compatibility API
for the existing condition pipeline and UI/DB field names.  It does not own an
algorithm and never imports the removed ``slurry_policy_model`` package.

Runtime chain::

    condition_model output
    -> SlurryPolicyOnlineBridge (compatibility only)
    -> MFACUnifiedRuntimePolicy
       -> Dynamic Qbase (exactly once)
       -> either SAFE_PRIMARY_FALLBACK or Scheme2RuntimeCoordinator
       -> one algorithm_target_supply_flow

When a calibrated coordinator is installed, the bridge keeps that same
coordinator attached across integrated version hot reloads.  The production
permission boundary remains fail closed: LEARN=0, Residual=0, DCS write=off.
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from system.model.map_control.mfac_model.context_resolver import MFACContextResolver
from system.model.map_control.mfac_model.primary_runtime import (
    MFACPrimaryPolicy,
    MFACUnifiedRuntimePolicy,
)
from system.model.map_control.mfac_model.runtime_coordinator import (
    Scheme2RuntimeCoordinator,
)


MFAC_PRIMARY_BRIDGE_VERSION = "SCHEME2_MFAC_PRIMARY_BRIDGE_V2_UNIFIED_RUNTIME"
CANONICAL_MFAC_OUTPUT_PREFIX = "mfac_"
LEGACY_SLURRY_POLICY_OUTPUT_PREFIX = "slurry_policy_"


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


class SlurryPolicyOnlineBridge:
    """Deprecated name for the thin unified-MFAC runtime bridge."""

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

        # Historical condition_config.py explicitly used output_prefix=
        # "slurry_policy_".  Treat that as a legacy alias request rather than
        # allowing it to replace the canonical MFAC namespace.  This keeps old
        # standalone configs working while guaranteeing that every current
        # runtime emits mfac_* as the primary contract.
        requested_output_prefix = str(
            self.config.get("output_prefix", CANONICAL_MFAC_OUTPUT_PREFIX)
        )
        configured_legacy_prefix = str(
            self.config.get(
                "legacy_output_prefix", LEGACY_SLURRY_POLICY_OUTPUT_PREFIX
            )
        )
        if requested_output_prefix == configured_legacy_prefix:
            self.output_prefix = CANONICAL_MFAC_OUTPUT_PREFIX
            self.legacy_output_prefix = configured_legacy_prefix
        else:
            self.output_prefix = requested_output_prefix
            self.legacy_output_prefix = configured_legacy_prefix

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
        self._runtime_coordinator: Optional[Scheme2RuntimeCoordinator] = None
        self._context_resolver: Optional[MFACContextResolver] = None
        self._initialization_error: Optional[str] = None

        if self.enabled and bool(self.config.get("initialize_on_start", True)):
            self._ensure_policy()

    @property
    def policy(self) -> Optional[Any]:
        return self._policy

    @property
    def initialization_error(self) -> Optional[str]:
        return self._initialization_error

    @property
    def runtime_coordinator(self) -> Optional[Scheme2RuntimeCoordinator]:
        return self._runtime_coordinator

    def _default_factory(
        self,
        config_spec: Optional[str],
        **kwargs: Any,
    ) -> Any:
        return MFACUnifiedRuntimePolicy(config_spec=config_spec, **kwargs)

    def _attach_runtime(self, policy: Any) -> Any:
        if self._runtime_coordinator is None:
            return policy
        configure = getattr(policy, "configure_runtime_coordinator", None)
        if not callable(configure):
            raise TypeError(
                "MFAC policy does not expose configure_runtime_coordinator"
            )
        configure(
            self._runtime_coordinator,
            context_resolver=self._context_resolver,
        )
        return policy

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
            policy = factory(self.config_spec, **kwargs)
        except TypeError:
            policy = factory(self.config_spec)
        return self._attach_runtime(policy)

    def _ensure_policy(self) -> Optional[Any]:
        if not self.enabled:
            return None
        if self._policy is not None:
            try:
                self._attach_runtime(self._policy)
            except Exception as exc:
                self._initialization_error = str(exc)
                if self.failure_mode == "RAISE":
                    raise
                return None
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

    def configure_runtime_coordinator(
        self,
        coordinator: Scheme2RuntimeCoordinator,
        *,
        context_resolver: Optional[MFACContextResolver] = None,
    ) -> None:
        """Attach the coordinator to the active and all future hot-reload policies."""
        if not isinstance(coordinator, Scheme2RuntimeCoordinator):
            raise TypeError("coordinator must be Scheme2RuntimeCoordinator")
        if coordinator.config.learning_enabled:
            raise ValueError("primary MFAC runtime LEARN must remain 0")
        if coordinator.config.residual_control_enabled:
            raise ValueError("primary MFAC runtime Residual must remain 0")
        if coordinator.dcs_write_enabled:
            raise ValueError("primary MFAC runtime DCS write must remain off")
        if context_resolver is not None and not isinstance(
            context_resolver, MFACContextResolver
        ):
            raise TypeError("context_resolver must be MFACContextResolver")
        self._runtime_coordinator = coordinator
        self._context_resolver = context_resolver
        policy = self._ensure_policy()
        if policy is None:
            raise RuntimeError(
                self._initialization_error or "MFAC primary policy unavailable"
            )
        self._attach_runtime(policy)

    def clear_runtime_coordinator(self) -> None:
        policy = self._policy
        if policy is not None:
            clear = getattr(policy, "clear_runtime_coordinator", None)
            if callable(clear):
                clear()
        self._runtime_coordinator = None
        self._context_resolver = None

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
        policy = self._attach_runtime(policy)
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
            "data_quality_ok",
            "equipment_changed",
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
        context["data_quality_ok"] = _as_bool(
            context.get("data_quality_ok"), True
        )
        context["equipment_changed"] = _as_bool(
            context.get("equipment_changed"), False
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
            "runtime_version": None,
            "model_version": None,
            "condition_snapshot_version": enriched_row.get(
                "condition_snapshot_version"
            ),
            "condition_label": enriched_row.get("condition_label"),
            "control_mode": "BLOCKED",
            "runtime_mode": "BLOCKED",
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
            "debug": {
                "integration_error": str(error),
                "duplicate_runtime_path": False,
            },
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
            decision = dict(
                policy.evaluate(
                    dict(enriched_row),
                    target=resolved_target,
                    execution_context=resolved_execution,
                )
            )
            decision["bridge_version"] = MFAC_PRIMARY_BRIDGE_VERSION
            return decision
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
            self._append_prefixed(output, self.legacy_output_prefix, decision)
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
        output["second_module_runtime_mode"] = decision.get("runtime_mode")
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
                "bridge_version": MFAC_PRIMARY_BRIDGE_VERSION,
                "canonical_output_prefix": self.output_prefix,
                "legacy_output_prefix": self.legacy_output_prefix,
                "initialization_error": self._initialization_error,
            }
        value = dict(policy.status())
        value.update(
            {
                "enabled": self.enabled,
                "ready": True,
                "backend": "MFAC",
                "bridge_version": MFAC_PRIMARY_BRIDGE_VERSION,
                "canonical_output_prefix": self.output_prefix,
                "legacy_output_prefix": self.legacy_output_prefix,
                "external_version_management": self.external_version_management,
                "legacy_name_only": True,
            }
        )
        return value


__all__ = [
    "MFACPrimaryPolicy",
    "MFACUnifiedRuntimePolicy",
    "SlurryPolicyOnlineBridge",
    "MFAC_PRIMARY_BRIDGE_VERSION",
    "CANONICAL_MFAC_OUTPUT_PREFIX",
    "LEGACY_SLURRY_POLICY_OUTPUT_PREFIX",
    "compact_json",
    "csv_safe_row",
]
