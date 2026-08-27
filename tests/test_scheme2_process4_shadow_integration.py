import tempfile
import unittest

from system.model.Process4MapControlMFAC import ProcessForMapConsole
from system.model.config.mfac_plant_contract import ph_arbitration_plant_values
from system.model.config.process4map_config import PROCESS4MAP_CONFIG
from system.model.map_control.condition_model.online_condition_policy_bridge import (
    SlurryPolicyOnlineBridge,
)
from system.model.map_control.mfac_model.flow_trajectory_planner import (
    FlowTrajectoryPlannerConfig,
)
from system.model.map_control.mfac_model.online_adaptation import (
    MFACOnlineAdaptationConfig,
)
from system.model.map_control.mfac_model.pending_dose_guard import PendingDoseGuardConfig
from system.model.map_control.mfac_model.ph_adaptation import PHOnlineAdaptationConfig
from system.model.map_control.mfac_model.ph_arbitration import PHResidualArbitrationConfig
from system.model.map_control.mfac_model.ph_response import PHResponseConfig
from system.model.map_control.mfac_model.process_response import ProcessResponseConfig
from system.model.map_control.mfac_model.residual_control import MFACResidualConfig
from system.model.map_control.mfac_model.runtime_config import MFACRuntimeBuildResult
from system.model.map_control.mfac_model.runtime_coordinator import (
    Scheme2RuntimeCoordinator,
    Scheme2RuntimeCoordinatorConfig,
)
from system.model.map_control.mfac_model.runtime_store import Scheme2RuntimeStore
from system.model.map_control.mfac_model.supply_flow_tracking import (
    SupplyFlowTrackingConfig,
)
from system.model.map_control.mfac_model.trajectory_coordinator import (
    Scheme2TrajectoryShadowCoordinator,
)


class _UnifiedPipeline:
    def __init__(self, bridge):
        self.policy_bridge = bridge

    def process(self, data, **kwargs):
        row = dict(data)
        row.update(
            {
                "condition_snapshot_version": "v001",
                "condition_label": "17",
                "base_condition_id": "17",
                "grid_id": "P1-S1",
                "policy_region_id": "R_P1_S1",
            }
        )
        return self.policy_bridge.process(row, **kwargs)

    def record_execution(self, feedback):
        return self.policy_bridge.record_execution(feedback)


class Scheme2Process4UnifiedRuntimeIntegrationTest(unittest.TestCase):
    @staticmethod
    def config(*, learning=False, residual=False, dual=True):
        kwargs = {}
        if dual:
            kwargs.update(
                ph_response=PHResponseConfig(
                    baseline_window_seconds=20.0,
                    delay_onset_seconds=5.0,
                    observation_seconds=15.0,
                    measurement_window_seconds=5.0,
                    max_sample_gap_seconds=15.0,
                    target_change_tolerance=0.0,
                    min_baseline_samples=2,
                    min_response_samples=2,
                ),
                ph_online_adaptation=PHOnlineAdaptationConfig(
                    eta=0.2,
                    mu=1.0,
                    phi_lower_bound=0.01,
                    phi_upper_bound=1.0,
                    max_single_update_abs=0.1,
                ),
                ph_arbitration=PHResidualArbitrationConfig(
                    **ph_arbitration_plant_values()
                ),
            )
        return Scheme2RuntimeCoordinatorConfig(
            tracking=SupplyFlowTrackingConfig(
                target_change_deadband=0.5,
                reach_tolerance=0.1,
                required_sustain_seconds=10.0,
                execution_timeout_seconds=30.0,
                max_sample_gap_seconds=15.0,
            ),
            response=ProcessResponseConfig(
                baseline_window_seconds=30.0,
                delay_onset_seconds=10.0,
                observation_seconds=20.0,
                measurement_window_seconds=10.0,
                max_sample_gap_seconds=15.0,
                target_change_tolerance=0.0,
                min_baseline_samples=2,
                min_response_samples=2,
            ),
            online_adaptation=MFACOnlineAdaptationConfig(
                eta=0.2,
                mu=1.0,
                phi_lower_bound=-10.0,
                phi_upper_bound=-0.1,
                max_single_update_abs=1.0,
            ),
            residual=MFACResidualConfig(
                rho=1.0,
                lambda_regularization=1.0,
                max_abs_residual=5.0,
                min_confidence=0.5,
            ),
            learning_enabled=learning,
            residual_control_enabled=residual,
            **kwargs,
        )

    @staticmethod
    def pending_config():
        return PendingDoseGuardConfig(
            flow_change_deadband=1.0,
            response_onset_seconds=10.0,
            response_peak_seconds=30.0,
            response_memory_seconds=60.0,
            max_sample_gap_seconds=15.0,
            min_confidence=0.5,
        )

    @staticmethod
    def planner_config():
        return FlowTrajectoryPlannerConfig(
            max_step_up=2.0,
            max_step_down=3.0,
            min_hold_seconds=20.0,
            demand_deadband=0.1,
        )

    @staticmethod
    def bridge():
        return SlurryPolicyOnlineBridge(
            {
                "enabled": True,
                "initialize_on_start": True,
                "output_prefix": "mfac_",
                "legacy_output_prefix": "slurry_policy_",
                "emit_legacy_compatibility": True,
                "target_column": "outlet_so2_target",
                "failure_mode": "RAISE",
            },
            initial_active_pointer={"integrated_version": "v001"},
        )

    @classmethod
    def bare_console(cls):
        console = ProcessForMapConsole.__new__(ProcessForMapConsole)
        console._mfac_runtime_build_result = MFACRuntimeBuildResult(
            configured=False,
            status="DISABLED_UNCALIBRATED",
        )
        console._mfac_primary_runtime_coordinator = None
        console._mfac_primary_context_resolver = None
        console._scheme2_runtime_coordinator = None
        console._scheme2_context_resolver = None
        console._scheme2_qbase_calculator = None
        console.slurry_core_config = {"target_column": "outlet_so2_target"}
        console.process_config = PROCESS4MAP_CONFIG
        console._slurry_pipeline_error = None
        console._slurry_pipeline = _UnifiedPipeline(cls.bridge())
        console._ensure_slurry_pipeline = lambda: True
        console._publish_map_control = lambda payload: None
        console.send = lambda: None
        console.system_state = console.SystemState.NORMAL_OPERATION
        return console

    def base_coordinator(self, root, **config):
        return Scheme2RuntimeCoordinator(
            self.config(**config),
            Scheme2RuntimeStore(root),
        )

    def coordinator(self, root, **config):
        return Scheme2TrajectoryShadowCoordinator(
            self.config(**config),
            Scheme2RuntimeStore(root),
            pending_dose_config=self.pending_config(),
            trajectory_planner_config=self.planner_config(),
        )

    def test_unconfigured_primary_runtime_is_explicit_safe_fallback(self):
        console = self.bare_console()
        result = console.insert_Mod(
            {
                "date": "2026-08-27T09:00:00+08:00",
                "yyq_SO2": 2000.0,
                "yyq_LL": 2200000.0,
                "xstshsjy_MD": 1200.0,
                "xstshsjy_LL": 69.0,
                "xstjy_PH": 6.2,
                "jyq_SO2": 50.0,
                "outlet_so2_target": 20.0,
            },
            20.0,
            store_to_db=True,
        )
        self.assertEqual(result["mfac_runtime_mode"], "SAFE_PRIMARY_FALLBACK")
        self.assertEqual(result["scheme2_shadow_status"], "DISABLED")
        self.assertEqual(result["mfac_runtime_config_status"], "DISABLED_UNCALIBRATED")
        self.assertFalse(result["mfac_runtime_configured"])
        self.assertEqual(result["scheme2_runtime_source"], "PRIMARY_MFAC_RUNTIME")
        self.assertFalse(result["scheme2_duplicate_runtime_path"])
        self.assertFalse(result["mfac_learn_enabled"])
        self.assertFalse(result["mfac_residual_enabled"])
        self.assertFalse(result["mfac_dcs_write_enabled"])
        self.assertEqual(result["mfac_residual_mfac_hold"], 0.0)
        self.assertEqual(result["mfac_debug"]["qbase_calculation_count"], 1)
        self.assertEqual(result["mfac_debug"]["coordinator_cycle_count"], 0)
        self.assertEqual(result["mfac_debug"]["fallback_cycle_count"], 1)
        self.assertEqual(result["mfac_debug"]["plant_contract_source"], "PLANT_CONFIG")
        self.assertAlmostEqual(
            result["mfac_algorithm_target_supply_flow"],
            41.20592948717949,
        )

    def test_process4_rejects_unsafe_single_response_or_legacy_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            console = self.bare_console()
            with self.assertRaises(ValueError):
                console.configure_mfac_runtime(
                    self.coordinator(root + "/learn", learning=True)
                )
            with self.assertRaises(ValueError):
                console.configure_mfac_runtime(
                    self.coordinator(root + "/residual", residual=True)
                )
            with self.assertRaises(ValueError):
                console.configure_mfac_runtime(
                    self.base_coordinator(root + "/single", dual=False)
                )
            with self.assertRaises(ValueError):
                console.configure_mfac_runtime(
                    self.base_coordinator(root + "/legacy-dual", dual=True)
                )

    def test_trajectory_coordinator_is_injected_into_primary_policy_not_sidecar(self):
        with tempfile.TemporaryDirectory() as root:
            console = self.bare_console()
            coordinator = self.coordinator(root)
            self.assertTrue(console.configure_mfac_runtime(coordinator))
            policy = console._slurry_pipeline.policy_bridge.policy
            self.assertIs(policy.runtime_coordinator, coordinator)
            self.assertEqual(policy.runtime_mode, "COORDINATOR_SHADOW")
            self.assertEqual(
                console._mfac_runtime_build_result.status,
                "CONFIGURED_TRAJECTORY_SHADOW",
            )

    def test_insert_mod_uses_one_target_path_and_keeps_trajectory_advisory(self):
        with tempfile.TemporaryDirectory() as root:
            console = self.bare_console()
            console.configure_mfac_runtime(self.coordinator(root))
            result = console.insert_Mod(
                {
                    "date": "2026-08-27T09:10:00+08:00",
                    "xst_base_flow": 31.0,
                    "xstshsjy_LL": 69.0,
                    "xstshsjy_MD": 1200.0,
                    "jyq_SO2": 50.0,
                    "yyq_SO2": 2000.0,
                    "yyq_LL": 2200000.0,
                    "xstjy_PH": 6.2,
                    "outlet_so2_target": 20.0,
                    "target_was_applied": True,
                    "dcs_applied_target_supply_flow": 31.0,
                },
                20.0,
                store_to_db=False,
            )
            self.assertEqual(result["mfac_runtime_mode"], "COORDINATOR_SHADOW")
            self.assertEqual(result["scheme2_shadow_status"], "ACTIVE")
            self.assertEqual(result["scheme2_runtime_source"], "PRIMARY_MFAC_RUNTIME")
            self.assertFalse(result["scheme2_duplicate_runtime_path"])
            self.assertEqual(result["mfac_debug"]["qbase_calculation_count"], 1)
            self.assertEqual(result["mfac_debug"]["coordinator_cycle_count"], 1)
            self.assertEqual(result["mfac_debug"]["fallback_cycle_count"], 0)
            self.assertTrue(result["mfac_qbase_valid"])
            self.assertAlmostEqual(
                result["mfac_qbase_effective"],
                41.20592948717949,
            )
            self.assertAlmostEqual(
                result["mfac_algorithm_target_supply_flow"],
                41.20592948717949,
            )
            self.assertEqual(
                result["scheme2_algorithm_target_supply_flow"],
                result["mfac_algorithm_target_supply_flow"],
            )
            self.assertIs(result["scheme2_shadow"], result["mfac_runtime_cycle"])
            self.assertFalse(result["mfac_learn_enabled"])
            self.assertFalse(result["mfac_residual_enabled"])
            self.assertFalse(result["mfac_dcs_write_enabled"])
            tracking = result["mfac_runtime_cycle"]["tracking_events"]
            self.assertEqual(tracking[0]["status"], "NOT_APPLIED")
            self.assertFalse(tracking[0]["target_was_applied"])
            metadata = result["mfac_runtime_cycle"]["metadata"]
            self.assertTrue(metadata["trajectory_shadow_enabled"])
            self.assertFalse(metadata["algorithm_target_replaced_by_trajectory_planner"])
            self.assertTrue(metadata["trajectory_plan"]["shadow_only"])
            self.assertFalse(metadata["trajectory_planner_dcs_write_enabled"])

    def test_shadow_compat_hook_only_maps_precomputed_fields(self):
        console = self.bare_console()
        payload = console._run_scheme2_shadow(
            {"yyq_SO2": "SHOULD_NOT_BE_READ"},
            {
                "mfac_runtime_mode": "SAFE_PRIMARY_FALLBACK",
                "mfac_learn_enabled": False,
                "mfac_residual_enabled": False,
                "mfac_dcs_write_enabled": False,
                "mfac_residual_mfac_hold": 0.0,
                "mfac_algorithm_target_supply_flow": 42.5,
                "mfac_qbase_source": "DYNAMIC_QBASE",
                "mfac_qbase_valid": True,
                "mfac_qbase_raw": 42.5,
                "mfac_qbase_effective": 42.5,
                "mfac_qbase": {"sentinel": "PRECOMPUTED"},
                "mfac_runtime_cycle": None,
            },
            999.0,
            data_quality_ok=False,
        )
        self.assertEqual(payload["scheme2_algorithm_target_supply_flow"], 42.5)
        self.assertEqual(payload["scheme2_qbase"], {"sentinel": "PRECOMPUTED"})
        self.assertFalse(payload["scheme2_duplicate_runtime_path"])


if __name__ == "__main__":
    unittest.main()
