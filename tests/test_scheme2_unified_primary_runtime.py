import tempfile
import unittest

from system.model.map_control.condition_model.online_condition_policy_bridge import (
    SlurryPolicyOnlineBridge,
)
from system.model.map_control.mfac_model.online_adaptation import (
    MFACOnlineAdaptationConfig,
)
from system.model.map_control.mfac_model.primary_runtime import (
    MFACUnifiedRuntimePolicy,
)
from system.model.map_control.mfac_model.process_response import (
    ProcessResponseConfig,
)
from system.model.map_control.mfac_model.residual_control import MFACResidualConfig
from system.model.map_control.mfac_model.runtime_coordinator import (
    Scheme2RuntimeCoordinator,
    Scheme2RuntimeCoordinatorConfig,
)
from system.model.map_control.mfac_model.runtime_store import Scheme2RuntimeStore
from system.model.map_control.mfac_model.supply_flow_tracking import (
    SupplyFlowTrackingConfig,
)


class Scheme2UnifiedPrimaryRuntimeTest(unittest.TestCase):
    @staticmethod
    def row(version="v001"):
        return {
            "date": "2026-08-27T09:30:00+08:00",
            "condition_snapshot_version": version,
            "condition_label": "17",
            "base_condition_id": "17",
            "grid_id": "P1-S1",
            "policy_region_id": "R_P1_S1",
            "yyq_SO2": 2000.0,
            "yyq_LL": 2200000.0,
            "xstshsjy_MD": 1200.0,
            "xstshsjy_LL": 69.0,
            "xstjy_PH": 6.2,
            "jyq_SO2": 50.0,
            "outlet_so2_target": 20.0,
            "fast_change_active": False,
        }

    @staticmethod
    def config(*, learning=False, residual=False):
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
        )

    def coordinator(self, root, **flags):
        return Scheme2RuntimeCoordinator(
            self.config(**flags),
            Scheme2RuntimeStore(root),
        )

    def test_safe_fallback_calculates_qbase_once(self):
        policy = MFACUnifiedRuntimePolicy(
            active_pointer={"integrated_version": "v001"}
        )
        decision = policy.evaluate(self.row(), target=20.0)

        self.assertEqual(decision["runtime_mode"], "SAFE_PRIMARY_FALLBACK")
        self.assertIsNone(decision["runtime_cycle"])
        self.assertTrue(decision["qbase_valid"])
        self.assertAlmostEqual(
            decision["algorithm_target_supply_flow"],
            41.20592948717949,
        )
        self.assertEqual(decision["debug"]["qbase_calculation_count"], 1)
        self.assertEqual(decision["debug"]["coordinator_cycle_count"], 0)
        self.assertEqual(decision["debug"]["fallback_cycle_count"], 1)
        self.assertFalse(decision["debug"]["duplicate_runtime_path"])
        self.assertEqual(decision["residual_mfac_hold"], 0.0)

    def test_coordinator_becomes_unique_target_owner(self):
        with tempfile.TemporaryDirectory() as root:
            coordinator = self.coordinator(root)
            policy = MFACUnifiedRuntimePolicy(
                active_pointer={"integrated_version": "v001"}
            )
            policy.configure_runtime_coordinator(coordinator)

            decision = policy.evaluate(
                self.row(),
                target=20.0,
                execution_context={"data_quality_ok": False},
            )

            self.assertEqual(decision["runtime_mode"], "COORDINATOR_SHADOW")
            self.assertIsNotNone(decision["runtime_cycle"])
            self.assertAlmostEqual(
                decision["algorithm_target_supply_flow"],
                41.20592948717949,
            )
            self.assertEqual(decision["debug"]["qbase_calculation_count"], 1)
            self.assertEqual(decision["debug"]["coordinator_cycle_count"], 1)
            self.assertEqual(decision["debug"]["fallback_cycle_count"], 0)
            self.assertFalse(decision["learn_enabled"])
            self.assertFalse(decision["residual_enabled"])
            self.assertFalse(decision["dcs_write_enabled"])
            tracking = decision["runtime_cycle"]["tracking_events"]
            self.assertEqual(tracking[0]["status"], "NOT_APPLIED")
            self.assertFalse(tracking[0]["target_was_applied"])

    def test_unsafe_coordinator_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            policy = MFACUnifiedRuntimePolicy(
                active_pointer={"integrated_version": "v001"}
            )
            with self.assertRaises(ValueError):
                policy.configure_runtime_coordinator(
                    self.coordinator(root + "/learn", learning=True)
                )
            with self.assertRaises(ValueError):
                policy.configure_runtime_coordinator(
                    self.coordinator(root + "/residual", residual=True)
                )

    def test_bridge_reattaches_coordinator_to_hot_reload_candidate(self):
        with tempfile.TemporaryDirectory() as root:
            coordinator = self.coordinator(root)
            bridge = SlurryPolicyOnlineBridge(
                {
                    "enabled": True,
                    "initialize_on_start": True,
                    "output_prefix": "mfac_",
                    "legacy_output_prefix": "slurry_policy_",
                    "emit_legacy_compatibility": True,
                },
                initial_active_pointer={"integrated_version": "v001"},
            )
            bridge.configure_runtime_coordinator(coordinator)
            self.assertIs(bridge.policy.runtime_coordinator, coordinator)

            candidate = bridge.create_candidate(
                {"integrated_version": "v002"},
                initial_runtime_state=bridge.export_runtime_state(),
            )
            self.assertIs(candidate.runtime_coordinator, coordinator)
            bridge.replace_policy(candidate)
            self.assertIs(bridge.policy.runtime_coordinator, coordinator)
            self.assertEqual(bridge.status()["runtime_mode"], "COORDINATOR_SHADOW")

    def test_bridge_outputs_mfac_and_legacy_compatibility_from_same_decision(self):
        bridge = SlurryPolicyOnlineBridge(
            {
                "enabled": True,
                "initialize_on_start": True,
                "output_prefix": "mfac_",
                "legacy_output_prefix": "slurry_policy_",
                "emit_legacy_compatibility": True,
            },
            initial_active_pointer={"integrated_version": "v001"},
        )
        output = bridge.process(self.row(), target=20.0)
        self.assertEqual(output["second_module_type"], "MFAC")
        self.assertEqual(output["mfac_runtime_mode"], "SAFE_PRIMARY_FALLBACK")
        self.assertEqual(
            output["mfac_algorithm_target_supply_flow"],
            output["slurry_policy_algorithm_target_supply_flow"],
        )
        self.assertTrue(output["slurry_policy_deprecated_compat"])
        self.assertEqual(output["slurry_policy_backend"], "MFAC")


if __name__ == "__main__":
    unittest.main()
