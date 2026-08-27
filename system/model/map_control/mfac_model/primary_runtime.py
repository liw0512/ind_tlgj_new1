# -*- coding: utf-8 -*-
"""Unified primary runtime policy for Scheme 2 MFAC.

This is the single second-module runtime facade after ``condition_model``. It
calculates Dynamic Qbase exactly once per 10-second decision frame and executes
one, and only one, target path:

- SAFE_PRIMARY_FALLBACK: ``clip(Qbase + 0, plant_min, plant_max)``;
- COORDINATOR_SHADOW: the already-computed Qbase enters the dual-response
  ``Scheme2RuntimeCoordinator``, which becomes the sole target owner.

Formal primary runtime requires both SO2 and pH response channels. Plant-owned
supply-flow bounds, actual-flow feedback field, primary tower and pH field are
resolved from ``PLANT_CONFIG`` through the canonical MFAC plant contract.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Dict, Mapping, Optional

from system.model.config.mfac_plant_contract import (
    primary_tower_contract,
    target_supply_flow_contract,
    validate_runtime_plant_contract,
)
from system.model.config.standard_fields import OUTLET_SO2_COLUMN

from .continuous_target import ONLINE_SHADOW, ContinuousTargetPublisher
from .context_resolver import MFACContextResolver
from .qbase import DynamicQbaseCalculator
from .runtime_coordinator import Scheme2RuntimeCoordinator


MFAC_PRIMARY_RUNTIME_VERSION = "SCHEME2_MFAC_PRIMARY_RUNTIME_V6_PLANT_VALIDATED"


def _active_version(pointer: Optional[Mapping[str, Any]], fallback: str = "") -> str:
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


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {
        "1", "true", "yes", "y", "on", "是"
    }


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


class MFACUnifiedRuntimePolicy:
    """Production-facing second-module policy with one MFAC runtime path."""

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

        self._target_contract = target_supply_flow_contract()
        self._tower_contract = primary_tower_contract()
        self.qbase_calculator = DynamicQbaseCalculator(
            str(self._tower_contract["tower_id"])
        )
        self._fallback_target_publisher = ContinuousTargetPublisher()
        self._runtime_coordinator: Optional[Scheme2RuntimeCoordinator] = None
        self._configured_context_resolver: Optional[MFACContextResolver] = None
        self._dynamic_context_resolver: Optional[MFACContextResolver] = None

        self._last_decision: Dict[str, Any] = {}
        self._reload_count = 0
        self._qbase_calculation_count = 0
        self._coordinator_cycle_count = 0
        self._fallback_cycle_count = 0

        runtime = dict(initial_runtime_state or {})
        restored_target = runtime.get("last_valid_algorithm_target")
        if restored_target is not None:
            try:
                self._fallback_target_publisher.restore_last_valid_algorithm_target(
                    float(restored_target)
                )
            except (TypeError, ValueError):
                pass

    @property
    def runtime_coordinator(self) -> Optional[Scheme2RuntimeCoordinator]:
        return self._runtime_coordinator

    @property
    def runtime_mode(self) -> str:
        return (
            "COORDINATOR_SHADOW"
            if self._runtime_coordinator is not None
            else "SAFE_PRIMARY_FALLBACK"
        )

    @staticmethod
    def _validate_formal_coordinator(coordinator: Scheme2RuntimeCoordinator) -> None:
        if coordinator.config.learning_enabled:
            raise ValueError("primary MFAC runtime LEARN must remain 0")
        if coordinator.config.residual_control_enabled:
            raise ValueError("primary MFAC runtime Residual must remain 0")
        if coordinator.dcs_write_enabled:
            raise ValueError("primary MFAC runtime DCS write must remain off")
        if coordinator.config.ph_response is None:
            raise ValueError("formal MFAC runtime requires independent pH response")
        if coordinator.config.ph_online_adaptation is None:
            raise ValueError("formal MFAC runtime requires independent phi_ph adaptation")
        if coordinator.config.ph_arbitration is None:
            raise ValueError("formal MFAC runtime requires pH residual arbitration")
        # Manual coordinator injection cannot bypass the plant-owned hard bounds
        # or pH safety envelope enforced by the formal builder.
        validate_runtime_plant_contract(
            coordinator.config.continuous_target,
            coordinator.config.ph_arbitration,
        )

    def configure_runtime_coordinator(
        self,
        coordinator: Scheme2RuntimeCoordinator,
        *,
        context_resolver: Optional[MFACContextResolver] = None,
    ) -> None:
        """Install a calibrated dual-response coordinator as unique target owner."""
        if not isinstance(coordinator, Scheme2RuntimeCoordinator):
            raise TypeError("coordinator must be Scheme2RuntimeCoordinator")
        self._validate_formal_coordinator(coordinator)
        if context_resolver is not None and not isinstance(
            context_resolver, MFACContextResolver
        ):
            raise TypeError("context_resolver must be MFACContextResolver")

        previous_target = self._fallback_target_publisher.last_valid_algorithm_target
        if (
            previous_target is not None
            and coordinator.target_publisher.last_valid_algorithm_target is None
        ):
            coordinator.target_publisher.restore_last_valid_algorithm_target(
                previous_target
            )
        self._runtime_coordinator = coordinator
        self._configured_context_resolver = context_resolver

    def clear_runtime_coordinator(self) -> None:
        """Return to safe fallback without losing the last algorithm target."""
        coordinator = self._runtime_coordinator
        if coordinator is not None:
            previous_target = coordinator.target_publisher.last_valid_algorithm_target
            if previous_target is not None:
                self._fallback_target_publisher.restore_last_valid_algorithm_target(
                    previous_target
                )
        self._runtime_coordinator = None
        self._configured_context_resolver = None

    def _context(self, row: Mapping[str, Any]):
        version = str(row.get("condition_snapshot_version") or "").strip()
        if not version:
            raise ValueError("condition_snapshot_version is required for MFAC runtime")
        resolver = self._configured_context_resolver
        if resolver is not None and resolver.condition_snapshot_version == version:
            return resolver.resolve(row)
        if (
            self._dynamic_context_resolver is None
            or self._dynamic_context_resolver.condition_snapshot_version != version
        ):
            self._dynamic_context_resolver = MFACContextResolver(version)
        return self._dynamic_context_resolver.resolve(row)

    def _run_target(
        self,
        row: Dict[str, Any],
        *,
        target: Any,
        execution_context: Dict[str, Any],
        qbase: Any,
    ):
        timestamp = row.get("date", row.get("timestamp", ""))
        coordinator = self._runtime_coordinator
        if coordinator is None:
            self._fallback_cycle_count += 1
            algorithm = self._fallback_target_publisher.publish(
                qbase.qbase_effective,
                0.0,
                inputs_valid=bool(qbase.valid),
                timestamp=str(timestamp or ""),
                replay_semantics=ONLINE_SHADOW,
            )
            return algorithm, 0.0, None, "SAFE_PRIMARY_FALLBACK"

        context = self._context(row)
        self._coordinator_cycle_count += 1
        ph_column = str(self._tower_contract["ph_column"])
        feedback_column = str(self._target_contract["feedback_column"])
        cycle = coordinator.process_cycle(
            timestamp=timestamp,
            qbase_effective=qbase.qbase_effective,
            qbase_inputs_valid=bool(qbase.valid),
            outlet_so2=row.get(OUTLET_SO2_COLUMN),
            inlet_so2=row.get("yyq_SO2"),
            ph=row.get(ph_column),
            so2_target=target,
            actual_supply_flow_feedback=row.get(feedback_column),
            condition_snapshot_version=context.condition_snapshot_version,
            mfac_context_id=context.mfac_context_id,
            condition_label=context.condition_label,
            base_condition_id=context.base_condition_id,
            grid_id=context.grid_id,
            policy_region_id=context.policy_region_id,
            target_was_applied=False,
            dcs_applied_target_supply_flow=None,
            replay_semantics=ONLINE_SHADOW,
            fast_active=_bool(row.get("fast_change_active"), False),
            data_quality_ok=_bool(
                execution_context.get("data_quality_ok"), True
            ),
            equipment_changed=_bool(
                execution_context.get("equipment_changed"), False
            ),
        )
        return (
            cycle.algorithm_target,
            float(cycle.residual_hold.held_residual),
            cycle,
            "COORDINATOR_SHADOW",
        )

    def evaluate(
        self,
        enriched_row: Dict[str, Any],
        *,
        target: Optional[Any] = None,
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        row = dict(enriched_row)
        execution = dict(execution_context or {})
        timestamp = row.get("date", row.get("timestamp", ""))
        qbase = self.qbase_calculator.calculate(row, target_so2=target)
        self._qbase_calculation_count += 1
        algorithm, residual_hold, cycle, runtime_mode = self._run_target(
            row,
            target=target,
            execution_context=execution,
            qbase=qbase,
        )

        target_value = algorithm.algorithm_target_supply_flow
        reason_codes = list(qbase.reason_codes)
        if algorithm.algorithm_target_status != "CALCULATED":
            reason_codes.append(algorithm.algorithm_target_status)
        if runtime_mode == "SAFE_PRIMARY_FALLBACK":
            reason_codes.append("MFAC_COORDINATOR_NOT_CONFIGURED")
        if not reason_codes:
            reason_codes = ["MFAC_RUNTIME_CALCULATED"]

        decision_status = (
            "VALID"
            if algorithm.algorithm_target_valid and target_value is not None
            else "HOLD"
        )
        coordinator_payload = cycle.to_dict() if cycle is not None else None
        decision = {
            "decision_id": "MFAC-%s" % str(timestamp or ""),
            "timestamp": str(timestamp or ""),
            "model_type": "MFAC",
            "runtime_version": MFAC_PRIMARY_RUNTIME_VERSION,
            "model_version": self.model_version,
            "condition_snapshot_version": row.get(
                "condition_snapshot_version", self.condition_snapshot_version
            ),
            "condition_label": row.get("condition_label"),
            "base_condition_id": row.get("base_condition_id"),
            "grid_id": row.get("grid_id"),
            "policy_region_id": row.get("policy_region_id"),
            "control_mode": runtime_mode,
            "runtime_mode": runtime_mode,
            "disturbance_mode": row.get("fast_change_mode", "NORMAL"),
            "current_so2": row.get(OUTLET_SO2_COLUMN),
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
            "qbase_source": "DYNAMIC_QBASE" if qbase.valid else "DYNAMIC_QBASE_INVALID",
            "qbase_valid": bool(qbase.valid),
            "qbase_raw": qbase.qbase_raw,
            "qbase_effective": qbase.qbase_effective,
            "residual_mfac_hold": residual_hold,
            "algorithm_target_supply_flow": target_value,
            "algorithm_target_valid": algorithm.algorithm_target_valid,
            "algorithm_target_status": algorithm.algorithm_target_status,
            "algorithm_target": algorithm.to_dict(),
            "runtime_cycle": coordinator_payload,
            "learn_enabled": False,
            "residual_enabled": False,
            "dcs_write_enabled": False,
            "target_supply_flow": {
                "mode": "TARGET_SUPPLY_FLOW",
                "available": target_value is not None,
                "value": target_value,
                "valid": algorithm.algorithm_target_valid,
                "status": algorithm.algorithm_target_status,
                "unit": str(self._target_contract["unit"]),
                "reason_codes": reason_codes,
            },
            "control_recommendation": {
                "requested_mode": "TARGET_SUPPLY_FLOW",
                "effective_mode": runtime_mode,
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
                "target_hard_min": float(self._target_contract["minimum"]),
                "target_hard_max": float(self._target_contract["maximum"]),
                "actual_flow_feedback_column": str(
                    self._target_contract["feedback_column"]
                ),
                "primary_tower_id": str(self._tower_contract["tower_id"]),
                "ph_column": str(self._tower_contract["ph_column"]),
                "plant_contract_source": "PLANT_CONFIG",
                "legacy_second_module_executed": False,
                "qbase_calculation_count": self._qbase_calculation_count,
                "coordinator_cycle_count": self._coordinator_cycle_count,
                "fallback_cycle_count": self._fallback_cycle_count,
                "duplicate_runtime_path": False,
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
        coordinator = self._runtime_coordinator
        if coordinator is not None:
            runtime_state = (
                coordinator.runtime_state.to_dict()
                if coordinator.runtime_state is not None
                else None
            )
            return {
                "last_valid_algorithm_target": (
                    coordinator.target_publisher.last_valid_algorithm_target
                ),
                "runtime_state": runtime_state,
                "residual_mfac_hold": coordinator.residual_mfac_hold,
                "runtime_mode": self.runtime_mode,
                "last_decision": deepcopy(self._last_decision),
            }
        return {
            "last_valid_algorithm_target": (
                self._fallback_target_publisher.last_valid_algorithm_target
            ),
            "runtime_state": None,
            "residual_mfac_hold": 0.0,
            "runtime_mode": self.runtime_mode,
            "last_decision": deepcopy(self._last_decision),
        }

    def mark_external_reload(self) -> None:
        self._reload_count += 1

    def status(self) -> Dict[str, Any]:
        coordinator = self._runtime_coordinator
        return {
            "model_type": "MFAC",
            "model_version": self.model_version,
            "condition_snapshot_version": self.condition_snapshot_version,
            "runtime_version": MFAC_PRIMARY_RUNTIME_VERSION,
            "runtime_mode": self.runtime_mode,
            "coordinator_configured": coordinator is not None,
            "dual_response_required": True,
            "learn_enabled": False,
            "residual_enabled": False,
            "dcs_write_enabled": False,
            "target_hard_min": float(self._target_contract["minimum"]),
            "target_hard_max": float(self._target_contract["maximum"]),
            "actual_flow_feedback_column": str(
                self._target_contract["feedback_column"]
            ),
            "primary_tower_id": str(self._tower_contract["tower_id"]),
            "ph_column": str(self._tower_contract["ph_column"]),
            "reload_count": self._reload_count,
            "qbase_calculation_count": self._qbase_calculation_count,
            "coordinator_cycle_count": self._coordinator_cycle_count,
            "fallback_cycle_count": self._fallback_cycle_count,
        }


MFACPrimaryPolicy = MFACUnifiedRuntimePolicy
