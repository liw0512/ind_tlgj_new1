import unittest

from system.model.map_control.mfac_model.local_step_design_profile import (
    LocalStepIdentificationDesignProfile,
)
from system.model.map_control.mfac_model.local_step_observation_profile import (
    LocalStepObservationProfile,
)
from system.model.map_control.mfac_model.local_step_session_readiness import (
    evaluate_local_step_session_readiness,
)
from system.model.map_control.mfac_model.local_step_trial_matrix import (
    LocalStepTrialLevel,
    LocalStepTrialMatrix,
)


class Scheme2LocalStepSessionReadinessTest(unittest.TestCase):
    @staticmethod
    def reviewed_design():
        # Unit-test values only, not site calibration.
        reviewed = {
            "step_up_m3_h": 2.0,
            "ph_lower_margin_inside_operating": 0.05,
            "ph_upper_margin_inside_operating": 0.05,
            "outlet_so2_headroom_to_safe_max": 5.0,
            "min_quiet_seconds": 2700.0,
            "min_candidate_interval_seconds": 3600.0,
            "max_abs_actual_minus_qbase": 1.0,
            "max_actual_flow_baseline_range_m3_h": 1.0,
            "max_abs_qbase_drift_m3_h": 2.0,
            "max_relative_qbase_drift": 0.06,
            "max_abs_inlet_so2_change": 50.0,
            "max_outlet_so2_baseline_range_mg_nm3": 2.0,
            "max_ph_baseline_range": 0.05,
            "max_sample_gap_seconds": 30.0,
            "max_abs_step_error_m3_h": 0.5,
            "min_abs_delta_so2": 2.5,
            "min_abs_delta_ph": 0.04,
            "minimum_so2_observation_seconds": 600.0,
            "minimum_ph_observation_seconds": 900.0,
        }
        return LocalStepIdentificationDesignProfile.from_mapping(
            {
                "design_id": "DESIGN",
                "status": "REVIEWED_MANUAL_ONLY",
                "activation_status": "NOT_ACTIVATABLE",
                "reviewed_parameters": reviewed,
            }
        )

    @staticmethod
    def reviewed_observation():
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
        return LocalStepObservationProfile.from_mapping(
            {
                "profile_id": "OBS",
                "status": "REVIEWED_MANUAL_ONLY",
                "activation_status": "NOT_ACTIVATABLE",
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
        )

    @staticmethod
    def matrix(reviewed=True):
        return LocalStepTrialMatrix(
            matrix_id="MATRIX",
            levels=(
                LocalStepTrialLevel(
                    level_id="PHASE1_STEP_2",
                    step_up_m3_h=2.0,
                    max_step_up_m3_h=2.0,
                    required_valid_trials=3 if reviewed else None,
                    required_independent_days=2 if reviewed else None,
                    review_status="REVIEWED" if reviewed else "REVIEW_REQUIRED",
                ),
            ),
        )

    def test_all_three_gates_are_required(self):
        result = evaluate_local_step_session_readiness(
            self.reviewed_design(),
            self.reviewed_observation(),
            self.matrix(reviewed=True),
            level_id="PHASE1_STEP_2",
        )
        self.assertTrue(result.ready)
        self.assertEqual(result.status, "READY_FOR_SUPERVISED_MANUAL_SESSION")
        self.assertEqual(result.blockers, ())
        self.assertFalse(result.automatic_execution_allowed)
        self.assertFalse(result.dcs_write_enabled)
        self.assertFalse(result.learning_permission)
        self.assertTrue(
            result.metadata["manual_human_approval_still_required_after_readiness"]
        )

    def test_unreviewed_matrix_keeps_session_not_ready(self):
        result = evaluate_local_step_session_readiness(
            self.reviewed_design(),
            self.reviewed_observation(),
            self.matrix(reviewed=False),
            level_id="PHASE1_STEP_2",
        )
        self.assertFalse(result.ready)
        self.assertIn("TRIAL_LEVEL_NOT_REVIEWED", result.blockers)
        self.assertIn("TRIAL_LEVEL_REQUIRED_VALID_TRIALS_UNRESOLVED", result.blockers)
        self.assertIn("TRIAL_LEVEL_REQUIRED_INDEPENDENT_DAYS_UNRESOLVED", result.blockers)

    def test_incomplete_observation_profile_blocks_session(self):
        observation = LocalStepObservationProfile.from_mapping(
            {
                "profile_id": "OBS-INCOMPLETE",
                "status": "INCOMPLETE_REVIEW_REQUIRED",
                "activation_status": "NOT_ACTIVATABLE",
            }
        )
        result = evaluate_local_step_session_readiness(
            self.reviewed_design(),
            observation,
            self.matrix(reviewed=True),
            level_id="PHASE1_STEP_2",
        )
        self.assertFalse(result.ready)
        self.assertIn("OBSERVATION_PROFILE_NOT_REVIEWED", result.blockers)
        self.assertTrue(
            any(item.startswith("OBSERVATION_MISSING:") for item in result.blockers)
        )


if __name__ == "__main__":
    unittest.main()
