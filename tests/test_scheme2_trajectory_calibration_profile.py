import unittest

from system.model.map_control.mfac_model.trajectory_calibration_profile import (
    Scheme2TrajectoryCalibrationProfile,
)


class Scheme2TrajectoryCalibrationProfileTest(unittest.TestCase):
    @staticmethod
    def audit_mapping():
        return {
            "semantics_version": "SCHEME2_TRAJECTORY_CALIBRATION_PROFILE_V1_AUDIT_ONLY",
            "profile_id": "MFAC-TRAJ-AUDIT-TEST",
            "activation_status": "NOT_ACTIVATABLE",
            "review_status": "REVIEW_REQUIRED",
            "local_gain_status": "INSUFFICIENT_EVIDENCE",
            "source": {
                "file": "history.csv",
                "sha256": "abc",
                "rows": 1000,
                "start_time": "2026-01-01T00:00:00",
                "end_time": "2026-01-02T00:00:00",
                "median_cadence_seconds": 10.0,
            },
            "extraction": {
                "method": "TEST",
                "pump_segment_count": 20,
                "clean_dynamic_candidate_count": 10,
                "validated_dynamic_event_count": 8,
            },
            "observed_timing_seconds": {
                "actual_flow_reach": {"count": 8, "p50": 20.0, "p90": 40.0},
                "ph_turn_onset": {"count": 8, "p50": 160.0, "p90": 190.0},
                "ph_peak": {"count": 8, "p50": 710.0, "p90": 886.0},
                "so2_turn_onset": {"count": 8, "p50": 280.0, "p90": 310.0},
                "so2_trough": {"count": 8, "p50": 840.0, "p90": 1120.0},
            },
            "pending_dose_candidate": {
                "status": "REVIEW_REQUIRED",
                "response_onset_candidate_seconds": 190.0,
                "response_peak_candidate_seconds": 900.0,
                "response_memory_candidate_seconds": None,
                "response_memory_lower_bound_seconds": 1800.0,
                "memory_observation_window_seconds": 1800.0,
                "memory_event_count": 10,
                "memory_half_decay_observed_count": 2,
                "memory_right_censored_ratio": 0.8,
                "reason": "FULL_MEMORY_NOT_IDENTIFIED",
            },
            "trajectory_planner_candidate": {
                "status": "INSUFFICIENT_LOCAL_STEP_EVIDENCE",
                "min_hold_evidence_floor_seconds": 310.0,
                "min_hold_candidate_seconds": 360.0,
                "max_step_up_candidate": None,
                "max_step_down_candidate": None,
                "demand_deadband_candidate": None,
                "reason": "LOCAL_STEP_NOT_IDENTIFIED",
            },
            "safety": {
                "operating_ph_max": 6.4,
                "safe_ph_max": 6.8,
                "validated_event_count": 8,
                "p_ph_gt_6_4": 0.7,
                "dose_risk_examples": [],
            },
            "permissions": {
                "learning_enabled": False,
                "residual_control_enabled": False,
                "dcs_write_enabled": False,
            },
        }

    def test_audit_mapping_parses_but_cannot_build_runtime(self):
        profile = Scheme2TrajectoryCalibrationProfile.from_audit_mapping(
            self.audit_mapping()
        )
        self.assertEqual(profile.pending_dose.ph_onset_seconds.p90, 190.0)
        self.assertEqual(profile.pending_dose.ph_peak_seconds.p90, 886.0)
        self.assertIsNone(profile.pending_dose.response_memory_candidate_seconds)
        self.assertEqual(
            profile.pending_dose.response_memory_lower_bound_seconds,
            1800.0,
        )
        self.assertEqual(profile.trajectory_planner.min_hold_candidate_seconds, 360.0)
        self.assertIsNone(profile.trajectory_planner.max_step_up_candidate)
        self.assertFalse(profile.can_build_runtime_config)
        with self.assertRaises(ValueError):
            profile.to_runtime_config()

    def test_activation_tampering_is_rejected(self):
        value = self.audit_mapping()
        value["activation_status"] = "APPROVED"
        with self.assertRaises(ValueError):
            Scheme2TrajectoryCalibrationProfile.from_audit_mapping(value)

    def test_quantile_regression_is_rejected(self):
        value = self.audit_mapping()
        value["observed_timing_seconds"]["ph_turn_onset"] = {
            "count": 8,
            "p50": 200.0,
            "p90": 190.0,
        }
        with self.assertRaises(ValueError):
            Scheme2TrajectoryCalibrationProfile.from_audit_mapping(value)


if __name__ == "__main__":
    unittest.main()
