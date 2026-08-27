import tempfile
import unittest

from system.model.config.mfac_plant_contract import (
    ph_arbitration_plant_values,
    target_supply_flow_contract,
)
from system.model.map_control.mfac_model.runtime_config import (
    DEFAULT_MFAC_RUNTIME_CONFIG,
    build_mfac_runtime,
)
from system.model.map_control.mfac_model.trajectory_coordinator import (
    Scheme2TrajectoryShadowCoordinator,
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
                "min_confidence": 0.5,
            },
            # Test-only synthetic timings; production repository keeps these
            # sections empty until historical/field calibration is reviewed.
            "pending_dose": {
                "flow_change_deadband": 1.0,
                "response_onset_seconds": 10.0,
                "response_peak_seconds": 30.0,
                "response_memory_seconds": 60.0,
                "max_sample_gap_seconds": 15.0,
                "min_confidence": 0.5,
            },
            "trajectory_planner": {
                "max_step_up": 2.0,
                "max_step_down": 3.0,
                "min_hold_seconds": 20.0,
                "demand_deadband": 0.1,
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
            "pending_dose",
            "trajectory_planner",
        ):
            self.assertEqual(DEFAULT_MFAC_RUNTIME_CONFIG[section], {})
        result = build_mfac_runtime(DEFAULT_MFAC_RUNTIME_CONFIG)
        self.assertFalse(result.configured)
        self.assertIsNone(result.coordinator)
        self.assertEqual(result.status, "DISABLED_UNCALIBRATED")

    def test_enabled_incomplete_config_does_not_guess_calibration(self):
        result = build_mfac_runtime({"enabled": True})
        self.assertFalse(result.configured)
        self.assertEqual(result.status, "INVALID_INCOMPLETE_CALIBRATION")
        self.assertIn("tracking.target_change_deadband", result.missing_fields)
        self.assertIn("so2_response.delay_onset_seconds", result.missing_fields)
        self.assertIn("ph_response.delay_onset_seconds", result.missing_fields)
        self.assertIn("pending_dose.response_peak_seconds", result.missing_fields)
        self.assertIn("trajectory_planner.max_step_up", result.missing_fields)
        self.assertNotIn("ph_arbitration.safe_min", result.missing_fields)

    def test_current_activation_stage_rejects_unsafe_permissions(self):
        for field in (
            "learning_enabled",
            "residual_control_enabled",
            "dcs_write_enabled",
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    build_mfac_runtime({"enabled": True, field: True})

    def test_complete_config_derives_plant_owned_limits(self):
        with tempfile.TemporaryDirectory() as root:
            result = build_mfac_runtime(self.complete_config(root))
            self.assertTrue(result.configured)
            self.assertEqual(result.status, "CONFIGURED_TRAJECTORY_SHADOW")
            self.assertIsInstance(result.coordinator, Scheme2TrajectoryShadowCoordinator)
            coordinator = result.coordinator
            target = target_supply_flow_contract()
            ph = ph_arbitration_plant_values()
            self.assertEqual(
                coordinator.config.continuous_target.hard_min_supply_flow,
                target["minimum"],
            )
            self.assertEqual(
                coordinator.config.continuous_target.hard_max_supply_flow,
                target["maximum"],
            )
            self.assertEqual(coordinator.config.ph_arbitration.safe_min, ph["safe_min"])
            self.assertEqual(coordinator.config.ph_arbitration.safe_max, ph["safe_max"])
            self.assertEqual(
                coordinator.config.ph_arbitration.operating_min,
                ph["operating_min"],
            )
            self.assertEqual(
                coordinator.config.ph_arbitration.operating_max,
                ph["operating_max"],
            )
            self.assertEqual(
                coordinator.config.ph_arbitration.guard_band,
                ph["guard_band"],
            )
            self.assertEqual(coordinator.config.ph_arbitration.min_confidence, 0.5)
            self.assertFalse(coordinator.config.learning_enabled)
            self.assertFalse(coordinator.config.residual_control_enabled)
            self.assertFalse(coordinator.dcs_write_enabled)
            self.assertIsNotNone(coordinator.ph_response_monitor)
            self.assertIsNotNone(coordinator.ph_online_adapter)
            self.assertIsNotNone(coordinator.ph_arbiter)
            self.assertIsNotNone(coordinator.pending_dose_guard)
            self.assertIsNotNone(coordinator.trajectory_planner)

    def test_runtime_cannot_override_plant_owned_target_bounds(self):
        with tempfile.TemporaryDirectory() as root:
            config = self.complete_config(root)
            config["continuous_target"] = {"hard_max_supply_flow": 65.0}
            result = build_mfac_runtime(config)
            self.assertFalse(result.configured)
            self.assertEqual(result.status, "INVALID_CALIBRATION_CONFIG")
            self.assertIn("cannot override plant-owned fields", result.error)

    def test_runtime_cannot_override_plant_owned_ph_envelope(self):
        with tempfile.TemporaryDirectory() as root:
            config = self.complete_config(root)
            config["ph_arbitration"]["safe_max"] = 6.5
            result = build_mfac_runtime(config)
            self.assertFalse(result.configured)
            self.assertEqual(result.status, "INVALID_CALIBRATION_CONFIG")
            self.assertIn("cannot override plant-owned fields", result.error)


if __name__ == "__main__":
    unittest.main()
