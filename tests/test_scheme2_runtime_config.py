import tempfile
import unittest

from system.model.map_control.mfac_model.runtime_config import (
    DEFAULT_MFAC_RUNTIME_CONFIG,
    build_mfac_runtime,
)


class Scheme2RuntimeConfigTest(unittest.TestCase):
    @staticmethod
    def complete_config(runtime_dir):
        return {
            "enabled": True,
            "learning_enabled": False,
            "residual_control_enabled": False,
            "dcs_write_enabled": False,
            "persist_runtime": True,
            "runtime_dir": runtime_dir,
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
                "min_confidence": 0.5,
            },
        }

    def test_repository_default_is_explicitly_uncalibrated(self):
        self.assertFalse(DEFAULT_MFAC_RUNTIME_CONFIG["enabled"])
        self.assertEqual(
            DEFAULT_MFAC_RUNTIME_CONFIG["status"],
            "DISABLED_UNCALIBRATED",
        )
        for section in (
            "tracking",
            "so2_response",
            "so2_adaptation",
            "residual",
            "ph_response",
            "ph_adaptation",
            "ph_arbitration",
        ):
            self.assertEqual(DEFAULT_MFAC_RUNTIME_CONFIG[section], {})
        result = build_mfac_runtime(DEFAULT_MFAC_RUNTIME_CONFIG)
        self.assertFalse(result.configured)
        self.assertIsNone(result.coordinator)
        self.assertEqual(result.status, "DISABLED_UNCALIBRATED")

    def test_enabled_incomplete_config_does_not_guess_parameters(self):
        result = build_mfac_runtime({"enabled": True})
        self.assertFalse(result.configured)
        self.assertEqual(result.status, "INVALID_INCOMPLETE_CALIBRATION")
        self.assertIn("tracking.target_change_deadband", result.missing_fields)
        self.assertIn("so2_response.delay_onset_seconds", result.missing_fields)
        self.assertIn("ph_response.delay_onset_seconds", result.missing_fields)
        self.assertIn("ph_arbitration.safe_min", result.missing_fields)

    def test_current_activation_stage_rejects_unsafe_permissions(self):
        for field in (
            "learning_enabled",
            "residual_control_enabled",
            "dcs_write_enabled",
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    build_mfac_runtime({"enabled": True, field: True})

    def test_complete_explicit_config_builds_dual_response_shadow(self):
        with tempfile.TemporaryDirectory() as root:
            result = build_mfac_runtime(self.complete_config(root))
            self.assertTrue(result.configured)
            self.assertEqual(result.status, "CONFIGURED_SHADOW")
            self.assertIsNotNone(result.coordinator)
            coordinator = result.coordinator
            self.assertFalse(coordinator.config.learning_enabled)
            self.assertFalse(coordinator.config.residual_control_enabled)
            self.assertFalse(coordinator.dcs_write_enabled)
            self.assertIsNotNone(coordinator.ph_response_monitor)
            self.assertIsNotNone(coordinator.ph_online_adapter)
            self.assertIsNotNone(coordinator.ph_arbiter)


if __name__ == "__main__":
    unittest.main()
