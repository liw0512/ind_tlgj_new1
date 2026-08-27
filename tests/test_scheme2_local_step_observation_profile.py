import unittest

from system.model.map_control.mfac_model.local_step_observation_profile import (
    LocalStepObservationProfile,
)
from system.model.map_control.mfac_model.ph_response import PHResponseMonitor
from system.model.map_control.mfac_model.process_response import ProcessResponseMonitor
from system.model.map_control.mfac_model.supply_flow_tracking import SupplyFlowTrackingMonitor


class Scheme2LocalStepObservationProfileTest(unittest.TestCase):
    @staticmethod
    def reviewed_mapping():
        # Unit-test values only; not site calibration.
        response = {
            "baseline_window_seconds": 30.0,
            "delay_onset_seconds": 10.0,
            "observation_seconds": 30.0,
            "measurement_window_seconds": 10.0,
            "max_sample_gap_seconds": 15.0,
            "target_change_tolerance": 0.0,
            "min_baseline_samples": 2,
            "min_response_samples": 2,
        }
        return {
            "semantics_version": "SCHEME2_LOCAL_STEP_OBSERVATION_PROFILE_V1_MANUAL_ONLY",
            "profile_id": "OBS-TEST",
            "status": "REVIEWED_MANUAL_ONLY",
            "activation_status": "NOT_ACTIVATABLE",
            "automatic_execution_allowed": False,
            "dcs_write_enabled": False,
            "reviewed_parameters": {
                "tracking": {
                    "target_change_deadband": 0.5,
                    "reach_tolerance": 0.5,
                    "required_sustain_seconds": 20.0,
                    "execution_timeout_seconds": 300.0,
                    "max_sample_gap_seconds": 30.0,
                },
                "so2_response": dict(response),
                "ph_response": dict(response),
            },
        }

    def test_candidates_never_fill_reviewed_monitoring_fields(self):
        value = {
            "profile_id": "OBS-INCOMPLETE",
            "status": "INCOMPLETE_REVIEW_REQUIRED",
            "activation_status": "NOT_ACTIVATABLE",
            "review_candidate_parameters": {
                "tracking": {
                    "reach_tolerance": 0.5,
                    "execution_timeout_seconds": 300.0,
                },
                "so2_response": {"delay_onset_seconds": 310.0},
                "ph_response": {"delay_onset_seconds": 190.0},
            },
            "reviewed_parameters": {},
        }
        profile = LocalStepObservationProfile.from_mapping(value)
        self.assertFalse(profile.reviewed_complete)
        self.assertFalse(profile.can_build_monitors)
        self.assertIn("tracking.target_change_deadband", profile.missing_reviewed_keys)
        self.assertIn("so2_response.observation_seconds", profile.missing_reviewed_keys)
        self.assertIn("ph_response.measurement_window_seconds", profile.missing_reviewed_keys)
        with self.assertRaises(ValueError):
            profile.build_monitors()

    def test_fully_reviewed_profile_builds_existing_monitors_only(self):
        profile = LocalStepObservationProfile.from_mapping(self.reviewed_mapping())
        self.assertTrue(profile.reviewed_complete)
        self.assertTrue(profile.can_build_monitors)
        monitors = profile.build_monitors()
        self.assertIsInstance(monitors.tracking, SupplyFlowTrackingMonitor)
        self.assertIsInstance(monitors.so2_response, ProcessResponseMonitor)
        self.assertIsInstance(monitors.ph_response, PHResponseMonitor)
        self.assertTrue(monitors.manual_only)
        self.assertFalse(monitors.automatic_execution_allowed)
        self.assertFalse(monitors.dcs_write_enabled)
        self.assertFalse(monitors.normal_runtime_activation_allowed)
        with self.assertRaises(ValueError):
            profile.to_runtime_config()

    def test_permission_tampering_is_rejected(self):
        for field in ("automatic_execution_allowed", "dcs_write_enabled"):
            with self.subTest(field=field):
                value = self.reviewed_mapping()
                value[field] = True
                with self.assertRaises(ValueError):
                    LocalStepObservationProfile.from_mapping(value)


if __name__ == "__main__":
    unittest.main()
