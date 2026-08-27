import tempfile
import unittest

from system.model.Process4MapControlMFAC import ProcessForMapConsole
from system.model.map_control.mfac_model.online_adaptation import (
    MFACOnlineAdaptationConfig,
)
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


class Scheme2P4PCRuntimePrevalidationTest(unittest.TestCase):
    @staticmethod
    def single_response_coordinator(root):
        config = Scheme2RuntimeCoordinatorConfig(
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
            learning_enabled=False,
            residual_control_enabled=False,
        )
        return Scheme2RuntimeCoordinator(config, Scheme2RuntimeStore(root))

    def test_rejected_single_response_does_not_mutate_p4pc_runtime_state(self):
        console = ProcessForMapConsole.__new__(ProcessForMapConsole)
        console._mfac_primary_runtime_coordinator = None
        console._mfac_primary_context_resolver = None
        console._scheme2_runtime_coordinator = None
        console._scheme2_context_resolver = None
        console._mfac_runtime_build_result = MFACRuntimeBuildResult(
            configured=False,
            status="DISABLED_UNCALIBRATED",
        )

        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                console.configure_mfac_runtime(
                    self.single_response_coordinator(root)
                )

        self.assertIsNone(console._mfac_primary_runtime_coordinator)
        self.assertIsNone(console._scheme2_runtime_coordinator)
        self.assertFalse(console._mfac_runtime_build_result.configured)
        self.assertEqual(
            console._mfac_runtime_build_result.status,
            "DISABLED_UNCALIBRATED",
        )


if __name__ == "__main__":
    unittest.main()
