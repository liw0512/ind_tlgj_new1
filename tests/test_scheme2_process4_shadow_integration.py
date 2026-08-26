import tempfile
import unittest

from system.model.Process4MapControl import ProcessForMapConsole
from system.model.config.process4map_config import PROCESS4MAP_CONFIG
from system.model.map_control.mfac_model.online_adaptation import (
    MFACOnlineAdaptationConfig,
)
from system.model.map_control.mfac_model.process_response import (
    ProcessResponseConfig,
)
from system.model.map_control.mfac_model.residual_control import (
    MFACResidualConfig,
)
from system.model.map_control.mfac_model.runtime_coordinator import (
    Scheme2RuntimeCoordinator,
    Scheme2RuntimeCoordinatorConfig,
)
from system.model.map_control.mfac_model.runtime_store import Scheme2RuntimeStore
from system.model.map_control.mfac_model.supply_flow_tracking import (
    SupplyFlowTrackingConfig,
)


class _Pipeline:
    def process(self, data, **kwargs):
        result = dict(data)
        result.update(
            {
                "condition_snapshot_version": "v001",
                "condition_label": "17",
                "base_condition_id": "17",
                "grid_id": "P1-S1",
                "policy_region_id": "R_P1_S1",
            }
        )
        return result


class Scheme2Process4ShadowIntegrationTest(unittest.TestCase):
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

    @staticmethod
    def bare_console():
        console = ProcessForMapConsole.__new__(ProcessForMapConsole)
        console._scheme2_runtime_coordinator = None
        console._scheme2_context_resolver = None
        return console

    def coordinator(self, root, **config):
        return Scheme2RuntimeCoordinator(
            self.config(**config),
            Scheme2RuntimeStore(root),
        )

    def test_unconfigured_main_loop_is_explicitly_safe(self):
        console = self.bare_console()
        payload = console._run_scheme2_shadow(
            {},
            {},
            None,
            data_quality_ok=True,
        )
        self.assertEqual(payload["scheme2_shadow_status"], "DISABLED")
        self.assertFalse(payload["scheme2_learn_enabled"])
        self.assertFalse(payload["scheme2_residual_enabled"])
        self.assertFalse(payload["scheme2_dcs_write_enabled"])
        self.assertEqual(payload["scheme2_residual_mfac_hold"], 0.0)

    def test_main_loop_rejects_unsafe_coordinator_activation(self):
        with tempfile.TemporaryDirectory() as root:
            console = self.bare_console()
            with self.assertRaises(ValueError):
                console.configure_scheme2_shadow(
                    self.coordinator(root + "/learn", learning=True)
                )
            with self.assertRaises(ValueError):
                console.configure_scheme2_shadow(
                    self.coordinator(root + "/residual", residual=True)
                )

    def test_actual_flow_and_scheme1_target_are_not_qbase_fallbacks(self):
        console = self.bare_console()
        qbase, source = console._scheme2_qbase(
            {
                "xstshsjy_LL": 69.0,
                "current_flow": 69.0,
            },
            {
                "slurry_policy_target_final_flow": 45.0,
                "target_final_flow": 45.0,
            },
        )
        self.assertIsNone(qbase)
        self.assertEqual(source, "")

    def test_insert_mod_runs_shadow_without_claiming_dcs_application(self):
        with tempfile.TemporaryDirectory() as root:
            console = self.bare_console()
            console.configure_scheme2_shadow(self.coordinator(root))
            console.system_state = console.SystemState.NORMAL_OPERATION
            console._slurry_pipeline = _Pipeline()
            console._slurry_pipeline_error = None
            console._ensure_slurry_pipeline = lambda: True
            console.process_config = PROCESS4MAP_CONFIG
            console.slurry_core_config = {"target_column": "outlet_so2_target"}
            console._publish_map_control = lambda payload: None
            console.send = lambda: None

            result = console.insert_Mod(
                {
                    "date": "2026-08-26T10:00:00+08:00",
                    "xst_base_flow": 31.0,
                    "xstshsjy_LL": 69.0,
                    "jyq_SO2": 50.0,
                    "yyq_SO2": 1000.0,
                    "xst_PH": 6.2,
                    "outlet_so2_target": 35.0,
                    "target_was_applied": True,
                    "dcs_applied_target_supply_flow": 31.0,
                },
                35.0,
                store_to_db=False,
            )

            self.assertEqual(result["scheme2_shadow_status"], "ACTIVE")
            self.assertEqual(result["scheme2_qbase_source"], "xst_base_flow")
            self.assertEqual(
                result["scheme2_algorithm_target_supply_flow"],
                31.0,
            )
            self.assertFalse(result["scheme2_learn_enabled"])
            self.assertFalse(result["scheme2_residual_enabled"])
            self.assertFalse(result["scheme2_dcs_write_enabled"])
            tracking = result["scheme2_shadow"]["tracking_events"]
            self.assertEqual(tracking[0]["status"], "NOT_APPLIED")
            self.assertFalse(tracking[0]["target_was_applied"])


if __name__ == "__main__":
    unittest.main()
