# -*- coding: utf-8 -*-
"""Production Process4MapControl runtime with one Scheme-2 MFAC path.

The historical ``Process4MapControl.ProcessForMapConsole`` implementation is
kept as the shared data/training/database shell.  This subclass removes the
remaining duplicate MFAC sidecar semantics without rewriting that large shell:

- the condition pipeline's ``MFACUnifiedRuntimePolicy`` owns Qbase + target;
- a calibrated ``Scheme2RuntimeCoordinator`` is injected into that policy;
- the inherited ``_run_scheme2_shadow`` call is overridden to *only map fields*
  from the already computed ``mfac_*`` output, never recalculate Qbase/target;
- current production permissions remain LEARN=0 / Residual=0 / DCS write=off.

``scheme2_shadow_*`` fields are temporarily retained as compatibility aliases
for UI/database consumers.  Their source is the single MFAC runtime decision.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from system.model.Process4MapControl import ProcessForMapConsole as _LegacyShell
from system.model.map_control.mfac_model.context_resolver import MFACContextResolver
from system.model.map_control.mfac_model.runtime_coordinator import (
    Scheme2RuntimeCoordinator,
)


UNIFIED_PROCESS4_MFAC_VERSION = "PROCESS4_SCHEME2_UNIFIED_RUNTIME_V1"
_DATA_QUALITY_MARKER = "_mfac_data_quality_ok"


class ProcessForMapConsole(_LegacyShell):
    """Formal P4PC class for the MFAC second-module branch."""

    def __init__(self, GLOBAL_DATA):
        # Set these before super().__init__ because the legacy shell may call
        # reload_models() while restoring an active version during startup.
        self._mfac_primary_runtime_coordinator: Optional[
            Scheme2RuntimeCoordinator
        ] = None
        self._mfac_primary_context_resolver: Optional[MFACContextResolver] = None
        super().__init__(GLOBAL_DATA)

    def _attach_primary_runtime_if_configured(self) -> bool:
        coordinator = self._mfac_primary_runtime_coordinator
        if coordinator is None:
            return True
        pipeline = getattr(self, "_slurry_pipeline", None)
        if pipeline is None:
            return False
        bridge = getattr(pipeline, "policy_bridge", None)
        if bridge is None:
            raise RuntimeError("online pipeline does not expose policy_bridge")
        configure = getattr(bridge, "configure_runtime_coordinator", None)
        if not callable(configure):
            raise RuntimeError(
                "MFAC bridge does not expose configure_runtime_coordinator"
            )
        configure(
            coordinator,
            context_resolver=self._mfac_primary_context_resolver,
        )
        return True

    def reload_models(self):
        """Reload integrated versions and keep the same MFAC coordinator bound."""
        loaded = super().reload_models()
        if not loaded:
            return False
        try:
            return self._attach_primary_runtime_if_configured()
        except Exception as exc:
            self._slurry_pipeline_error = str(exc)
            return False

    def configure_scheme2_shadow(self, coordinator, context_resolver=None):
        """Compatibility name: bind Coordinator into the *primary* MFAC path.

        The old implementation stored a separate sidecar coordinator and then
        ran it after primary policy evaluation.  This override instead injects
        the coordinator into ``MFACUnifiedRuntimePolicy`` so only one target is
        calculated per cycle.
        """
        if not isinstance(coordinator, Scheme2RuntimeCoordinator):
            raise TypeError("coordinator must be Scheme2RuntimeCoordinator")
        if coordinator.config.learning_enabled:
            raise ValueError("Process4MapControl Scheme2 LEARN must remain 0")
        if coordinator.config.residual_control_enabled:
            raise ValueError("Process4MapControl Scheme2 Residual must remain 0")
        if coordinator.dcs_write_enabled:
            raise ValueError("Process4MapControl Scheme2 DCS write must remain off")
        if context_resolver is not None and not isinstance(
            context_resolver, MFACContextResolver
        ):
            raise TypeError("context_resolver must be MFACContextResolver")

        self._mfac_primary_runtime_coordinator = coordinator
        self._mfac_primary_context_resolver = context_resolver

        # Keep legacy attributes only for introspection/older tests; they are no
        # longer executed as a second runtime path.
        self._scheme2_runtime_coordinator = coordinator
        self._scheme2_context_resolver = context_resolver
        self._scheme2_qbase_calculator = None

        if not self._ensure_slurry_pipeline():
            raise RuntimeError(
                self._slurry_pipeline_error or "integrated MFAC pipeline unavailable"
            )
        if not self._attach_primary_runtime_if_configured():
            raise RuntimeError("failed to bind coordinator to primary MFAC runtime")
        return True

    @staticmethod
    def _runtime_execution_context(data):
        context = _LegacyShell._runtime_execution_context(data)
        context["data_quality_ok"] = bool(data.get(_DATA_QUALITY_MARKER, True))
        return context

    @staticmethod
    def _compat_scheme2_payload(result: Dict[str, Any]) -> Dict[str, Any]:
        """Map existing mfac_* decision fields to old scheme2_shadow_* aliases."""
        mode = str(result.get("mfac_runtime_mode") or "").strip()
        cycle = result.get("mfac_runtime_cycle")
        coordinator_active = mode == "COORDINATOR_SHADOW" and isinstance(cycle, dict)
        reason = (
            "UNIFIED_PRIMARY_COORDINATOR_SHADOW"
            if coordinator_active
            else "UNIFIED_PRIMARY_SAFE_FALLBACK"
        )
        return {
            "scheme2_shadow_status": "ACTIVE" if coordinator_active else "DISABLED",
            "scheme2_shadow_reason": reason,
            "scheme2_learn_enabled": bool(result.get("mfac_learn_enabled", False)),
            "scheme2_residual_enabled": bool(
                result.get("mfac_residual_enabled", False)
            ),
            "scheme2_dcs_write_enabled": False,
            "scheme2_residual_mfac_hold": float(
                result.get("mfac_residual_mfac_hold", 0.0) or 0.0
            ),
            "scheme2_algorithm_target_supply_flow": result.get(
                "mfac_algorithm_target_supply_flow"
            ),
            "scheme2_qbase_source": result.get("mfac_qbase_source"),
            "scheme2_qbase_valid": bool(result.get("mfac_qbase_valid", False)),
            "scheme2_qbase_raw": result.get("mfac_qbase_raw"),
            "scheme2_qbase_effective": result.get("mfac_qbase_effective"),
            "scheme2_qbase": result.get("mfac_qbase"),
            "scheme2_shadow": cycle,
            "scheme2_runtime_source": "PRIMARY_MFAC_RUNTIME",
            "scheme2_duplicate_runtime_path": False,
            "scheme2_process4_runtime_version": UNIFIED_PROCESS4_MFAC_VERSION,
        }

    def _run_scheme2_shadow(
        self,
        data,
        result,
        target_so2,
        *,
        data_quality_ok,
    ):
        """Compatibility hook only; never execute a second MFAC calculation."""
        del data, target_so2, data_quality_ok
        if not isinstance(result, dict) or not result:
            return {
                "scheme2_shadow_status": "DISABLED",
                "scheme2_shadow_reason": "NO_PRIMARY_MFAC_RESULT",
                "scheme2_learn_enabled": False,
                "scheme2_residual_enabled": False,
                "scheme2_dcs_write_enabled": False,
                "scheme2_residual_mfac_hold": 0.0,
                "scheme2_runtime_source": "PRIMARY_MFAC_RUNTIME",
                "scheme2_duplicate_runtime_path": False,
                "scheme2_process4_runtime_version": UNIFIED_PROCESS4_MFAC_VERSION,
            }
        return self._compat_scheme2_payload(result)

    def insert_Mod(self, data, target_so2, store_to_db=True):
        """Pass snapshot data-quality evidence into the single MFAC runtime."""
        runtime_data = dict(data)
        runtime_data[_DATA_QUALITY_MARKER] = bool(store_to_db)
        result = super().insert_Mod(
            runtime_data,
            target_so2,
            store_to_db=store_to_db,
        )
        if isinstance(result, dict):
            result.pop(_DATA_QUALITY_MARKER, None)
        return result


__all__ = ["ProcessForMapConsole", "UNIFIED_PROCESS4_MFAC_VERSION"]
