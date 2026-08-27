import tempfile
import unittest

from system.model.map_control.mfac_model.primary_runtime import MFACUnifiedRuntimePolicy
from system.model.map_control.mfac_model.runtime_config import build_mfac_runtime


class Scheme2RuntimeModeTransitionTest(unittest.TestCase):
    @staticmethod
    def calibrated_config(root):
        return {
            "enabled": True,
            "runtime_dir": root,
            "tracking": {
                "target_change_deadband": 0.5,
                "reach_tolerance": 0.1,
                "required_sustain_seconds": 10.0,
                "execution_timeout_seconds": 30.0,
                "max_sample_gap_seconds": 15.0,
            },
            "so2_response": {
                "baseline_window_seconds": 30.0,
                "delay_onset_seconds": 10.0,
                "observation_seconds": 20.0,
                "measurement_window_seconds": 10.0,
                "max_sample_gap_seconds": 15.0,
                "target_change_tolerance": 0.0,
                "min_baseline_samples": 2,
                "min_response_samples": 2,
            },
            "so2_adaptation": {
                "eta": 0.2,
                "mu": 1.0,
                "phi_lower_bound": -10.0,
                "phi_upper_bound": -0.1,
                "max_single_update_abs": 1.0,
            },
            "residual": {
                "rho": 1.0,
                "lambda_regularization": 1.0,
                "max_abs_residual": 5.0,
                "min_confidence": 0.5,
            },
            "ph_response": {
                "baseline_window_seconds": 20.0,
                "delay_onset_seconds": 5.0,
                "observation_seconds": 15.0,
                "measurement_window_seconds": 5.0,
                "max_sample_gap_seconds": 15.0,
                "target_change_tolerance": 0.0,
                "min_baseline_samples": 2,
                "min_response_samples": 2,
            },
            "ph_adaptation": {
                "eta": 0.2,
                "mu": 1.0,
                "phi_lower_bound": 0.01,
                "phi_upper_bound": 1.0,
                "max_single_update_abs": 0.1,
            },
            "ph_arbitration": {
                "operating_min": 5.4,
                "operating_max": 6.2,
                "safe_min": 5.0,
                "safe_max": 6.5,
                "guard_band": 0.1,
            },
        }

    @staticmethod
    def valid_row():
        return {
            "date": "2026-08-27T10:00:00+08:00",
            "condition_snapshot_version": "v001",
            "condition_label": "17",
            "base_condition_id": "17",
            "grid_id": "P1-S1",
            "policy_region_id": "R_P1_S1",
            "yyq_SO2": 2000.0,
            "yyq_LL": 2200000.0,
            "xstshsjy_MD": 1200.0,
            "jyq_SO2": 50.0,
            "xstjy_PH": 6.0,
            "xstshsjy_LL": 40.0,
        }

    def test_last_valid_target_survives_fallback_to_coordinator_transition(self):
        policy = MFACUnifiedRuntimePolicy(
            active_pointer={
                "integrated_version": "v001",
                "condition": {"version": "v001"},
                "mfac": {"version": "v001"},
            }
        )
        first = policy.evaluate(self.valid_row(), target=20.0)
        expected = first["algorithm_target_supply_flow"]
        self.assertIsNotNone(expected)
        self.assertEqual(first["runtime_mode"], "SAFE_PRIMARY_FALLBACK")

        with tempfile.TemporaryDirectory() as root:
            built = build_mfac_runtime(self.calibrated_config(root))
            self.assertTrue(built.configured)
            policy.configure_runtime_coordinator(built.coordinator)

            invalid = self.valid_row()
            invalid["date"] = "2026-08-27T10:00:10+08:00"
            invalid["yyq_SO2"] = None
            second = policy.evaluate(invalid, target=20.0)
            self.assertEqual(second["runtime_mode"], "COORDINATOR_SHADOW")
            self.assertEqual(second["algorithm_target_supply_flow"], expected)
            self.assertEqual(
                second["algorithm_target_status"],
                "HOLD_LAST_INVALID_INPUT",
            )

            policy.clear_runtime_coordinator()
            invalid["date"] = "2026-08-27T10:00:20+08:00"
            third = policy.evaluate(invalid, target=20.0)
            self.assertEqual(third["runtime_mode"], "SAFE_PRIMARY_FALLBACK")
            self.assertEqual(third["algorithm_target_supply_flow"], expected)


if __name__ == "__main__":
    unittest.main()
