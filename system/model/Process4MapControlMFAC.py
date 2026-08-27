# -*- coding: utf-8 -*-
"""Production Process4MapControl runtime with one formal Scheme-2 MFAC path.

The historical ``Process4MapControl.ProcessForMapConsole`` remains only as the
shared data/training/thread shell.  This subclass makes MFAC canonical at the
boundary without duplicating Qbase or target calculation.
"""

from __future__ import annotations

from copy import deepcopy
import logging
from typing import Any, Dict, Optional

from system.model.Process4MapControl import ProcessForMapConsole as _LegacyShell
from system.model.config.database_schema import ensure_filter_table
from system.model.config.mfac_core_bridge_config import MFAC_CORE_BRIDGE_CONFIG
from system.model.config.mfac_database_schema import (
    ensure_mfac_model_result_table,
    insert_mfac_model_result_row,
)
from system.model.map_control.mfac_model.context_resolver import MFACContextResolver
from system.model.map_control.mfac_model.mfac_primary_config import (
    MFAC_PRIMARY_ARTIFACT_CONFIG,
)
from system.model.map_control.mfac_model.primary_runtime import MFACUnifiedRuntimePolicy
from system.model.map_control.mfac_model.runtime_config import (
    MFACRuntimeBuildResult,
    build_mfac_runtime,
)
from system.model.map_control.mfac_model.runtime_coordinator import (
    Scheme2RuntimeCoordinator,
)


UNIFIED_PROCESS4_MFAC_VERSION = "PROCESS4_SCHEME2_UNIFIED_RUNTIME_V4_PREVALIDATED"
_DATA_QUALITY_MARKER = "_mfac_data_quality_ok"


class ProcessForMapConsole(_LegacyShell):
    """Formal P4PC class for condition_model -> MFAC production routing."""

    _LEGACY_CORE_KEY_MAP = {
        "slurry_policy_initial_script": "mfac_initial_script",
        "slurry_policy_incremental_script": "mfac_incremental_script",
        "slurry_policy_activate_script": "mfac_activate_script",
        "slurry_policy_config": "mfac_config",
        "slurry_policy_output_root": "mfac_output_root",
    }

    def __init__(self, GLOBAL_DATA):
        try:
            runtime_build = build_mfac_runtime(
                MFAC_PRIMARY_ARTIFACT_CONFIG.get("runtime") or {}
            )
        except Exception as exc:
            runtime_build = MFACRuntimeBuildResult(
                configured=False,
                status="INVALID_RUNTIME_CONFIG",
                error=str(exc),
            )
        self._mfac_runtime_build_result = runtime_build
        self._mfac_primary_runtime_coordinator: Optional[
            Scheme2RuntimeCoordinator
        ] = runtime_build.coordinator
        self._mfac_primary_context_resolver: Optional[MFACContextResolver] = None

        super().__init__(GLOBAL_DATA)
        self.slurry_core_config = deepcopy(MFAC_CORE_BRIDGE_CONFIG)
        # Migration-only aliases for old introspection/tests. The inherited
        # sidecar method is overridden below and never executes this object.
        self._scheme2_runtime_coordinator = self._mfac_primary_runtime_coordinator
        self._scheme2_context_resolver = self._mfac_primary_context_resolver
        self._scheme2_qbase_calculator = None

    def _core_path(self, key):
        canonical = self._LEGACY_CORE_KEY_MAP.get(str(key), str(key))
        if canonical not in MFAC_CORE_BRIDGE_CONFIG:
            raise KeyError("unknown MFAC core config key: %s" % key)
        return str(MFAC_CORE_BRIDGE_CONFIG[canonical])

    def _integration_config(self):
        """Build production integration without legacy condition-config authority."""
        return {
            "enabled": True,
            "config_spec": self._core_path("mfac_config"),
            "external_version_management": True,
            "integrated_version": {
                "enabled": True,
                "active_version_file": self._core_path("active_version_file"),
                "hot_reload_enabled": True,
                "reload_check_interval_seconds": max(1.0, float(self.snapshot_interval)),
                "verify_condition_snapshot_hash": True,
                "require_atomic_pair_switch": True,
                "reset_condition_stability_window": True,
                "preserve_runtime_control_state": True,
                "keep_current_version_on_failure": True,
            },
            "initialize_on_start": True,
            "failure_mode": "BLOCKED_OUTPUT",
            "output_prefix": "mfac_",
            "legacy_output_prefix": "slurry_policy_",
            "emit_legacy_compatibility": True,
            "target_column": MFAC_CORE_BRIDGE_CONFIG["target_column"],
            "fixed_target": None,
            "default_execution_context": {
                "automatic_control_allowed": False,
                "manual_valves": [],
                "faulted_valves": [],
                "supply_pump_state_changing": False,
            },
            "execution_context_columns": {
                "automatic_control_allowed": "automatic_control_allowed",
                "manual_valves": "manual_valves",
                "faulted_valves": "faulted_valves",
                "supply_pump_state_changing": "supply_pump_state_changing",
            },
        }

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

    def configure_mfac_runtime(self, coordinator, context_resolver=None):
        """Bind a calibrated dual-response Coordinator into the primary path.

        Validation is completed before any P4PC runtime field is changed, so a
        rejected coordinator cannot leave a half-configured production object.
        """
        if not isinstance(coordinator, Scheme2RuntimeCoordinator):
            raise TypeError("coordinator must be Scheme2RuntimeCoordinator")
        MFACUnifiedRuntimePolicy._validate_formal_coordinator(coordinator)
        if context_resolver is not None and not isinstance(
            context_resolver, MFACContextResolver
        ):
            raise TypeError("context_resolver must be MFACContextResolver")

        # Attach to the active policy first.  Only after that succeeds do we
        # publish this coordinator as the P4PC runtime fact.
        pipeline = getattr(self, "_slurry_pipeline", None)
        if pipeline is None:
            if not self._ensure_slurry_pipeline():
                raise RuntimeError(
                    self._slurry_pipeline_error or "integrated MFAC pipeline unavailable"
                )
            pipeline = self._slurry_pipeline
        bridge = getattr(pipeline, "policy_bridge", None)
        if bridge is None:
            raise RuntimeError("online pipeline does not expose policy_bridge")
        configure = getattr(bridge, "configure_runtime_coordinator", None)
        if not callable(configure):
            raise RuntimeError(
                "MFAC bridge does not expose configure_runtime_coordinator"
            )
        configure(coordinator, context_resolver=context_resolver)

        self._mfac_primary_runtime_coordinator = coordinator
        self._mfac_primary_context_resolver = context_resolver
        self._mfac_runtime_build_result = MFACRuntimeBuildResult(
            configured=True,
            status="CONFIGURED_SHADOW",
            coordinator=coordinator,
        )
        self._scheme2_runtime_coordinator = coordinator
        self._scheme2_context_resolver = context_resolver
        self._scheme2_qbase_calculator = None
        return True

    def configure_scheme2_shadow(self, coordinator, context_resolver=None):
        """Deprecated compatibility alias for ``configure_mfac_runtime``."""
        return self.configure_mfac_runtime(
            coordinator,
            context_resolver=context_resolver,
        )

    @staticmethod
    def _runtime_execution_context(data):
        context = _LegacyShell._runtime_execution_context(data)
        context["data_quality_ok"] = bool(data.get(_DATA_QUALITY_MARKER, True))
        return context

    def _runtime_config_payload(self) -> Dict[str, Any]:
        result = self._mfac_runtime_build_result
        return {
            "mfac_runtime_config_status": result.status,
            "mfac_runtime_config_error": result.error,
            "mfac_runtime_configured": bool(result.configured),
            "mfac_runtime_config_version": result.config_version,
            "mfac_runtime_config_missing_fields": list(result.missing_fields),
        }

    def _compat_scheme2_payload(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Map the already-computed mfac_* decision to migration aliases only."""
        mode = str(result.get("mfac_runtime_mode") or "").strip()
        cycle = result.get("mfac_runtime_cycle")
        coordinator_active = mode == "COORDINATOR_SHADOW" and isinstance(cycle, dict)
        reason = (
            "UNIFIED_PRIMARY_COORDINATOR_SHADOW"
            if coordinator_active
            else "UNIFIED_PRIMARY_SAFE_FALLBACK"
        )
        payload = {
            "mfac_loaded_version": (
                result.get("mfac_loaded_version")
                or result.get("slurry_policy_loaded_version")
                or result.get("mfac_model_version")
            ),
            "scheme2_shadow_status": "ACTIVE" if coordinator_active else "DISABLED",
            "scheme2_shadow_reason": reason,
            "scheme2_learn_enabled": bool(result.get("mfac_learn_enabled", False)),
            "scheme2_residual_enabled": bool(result.get("mfac_residual_enabled", False)),
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
        payload.update(self._runtime_config_payload())
        return payload

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
            payload = {
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
            payload.update(self._runtime_config_payload())
            return payload
        return self._compat_scheme2_payload(result)

    def _build_write_key(self, data, write_target):
        """Use canonical MFAC action identity for model-result deduplication."""
        try:
            return "|".join(
                [
                    str(write_target),
                    str(data.get("_snapshot_seq", "")),
                    str(data.get("date", "")),
                    str(data.get(self.process_config.unit_stop.field, "")),
                    str(data.get("yyq_SO2", "")),
                    str(data.get("jyq_SO2", "")),
                    str(data.get("condition_label", "")),
                    str(
                        data.get("mfac_action_id")
                        or data.get("slurry_policy_action_id")
                        or ""
                    ),
                ]
            )
        except Exception:
            return None

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

    def getNewDataTableName(self):
        """Create/extend current monthly tables with canonical MFAC columns."""
        persistence = self.process_config.persistence
        self.filter_table_name = (
            self.filter_table_name if hasattr(self, "filter_table_name") else ""
        )
        self.mod_pre_table_name = (
            self.mod_pre_table_name if hasattr(self, "mod_pre_table_name") else ""
        )
        filter_ok = False
        model_ok = False
        try:
            self.filter_table_name = ensure_filter_table(
                self.engine,
                persistence.filter_table_prefix,
            )
            filter_ok = True
            self._record_persistence_schema(
                "filter", True, table=self.filter_table_name
            )
        except Exception as exc:
            self._record_persistence_schema(
                "filter", False, table=self.filter_table_name, error=exc
            )
            logging.warning("启动时过滤数据月表初始化失败: %s", exc)
        try:
            self.mod_pre_table_name = ensure_mfac_model_result_table(
                self.engine,
                persistence.model_result_table_prefix,
            )
            model_ok = True
            self._record_persistence_schema(
                "model_result", True, table=self.mod_pre_table_name
            )
        except Exception as exc:
            self._record_persistence_schema(
                "model_result", False, table=self.mod_pre_table_name, error=exc
            )
            logging.warning("启动时MFAC模型结果月表初始化失败: %s", exc)
        return bool(filter_ok and model_ok)

    def add_data_to_databases(self, data):
        """Persist base + condition + canonical MFAC + legacy aliases."""
        try:
            row = dict(data[0]) if isinstance(data, (list, tuple)) else dict(data)
            self.mod_pre_table_name = ensure_mfac_model_result_table(
                self.engine,
                self.process_config.persistence.model_result_table_prefix,
            )
            insert_mfac_model_result_row(
                self.engine,
                self.mod_pre_table_name,
                row,
            )
            self._record_persistence_write(
                "model_result", True, table=self.mod_pre_table_name
            )
        except Exception as exc:
            self._record_persistence_write(
                "model_result",
                False,
                table=getattr(self, "mod_pre_table_name", ""),
                error=exc,
            )
            logging.exception("写入MFAC模型结果表失败: %s", exc)


__all__ = ["ProcessForMapConsole", "UNIFIED_PROCESS4_MFAC_VERSION"]
