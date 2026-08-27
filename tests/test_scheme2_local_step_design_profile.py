import unittest

from system.model.map_control.mfac_model.local_step_design_profile import (
    LocalStepIdentificationDesignProfile,
)


class Scheme2LocalStepDesignProfileTest(unittest.TestCase):
    def test_review_candidates_do_not_fill_reviewed_parameters(self):
        profile = LocalStepIdentificationDesignProfile.from_mapping(
            {
                "design_id": "ID-DESIGN",
                "status": "INCOMPLETE_REVIEW_REQUIRED",
                "activation_status": "NOT_ACTIVATABLE",
                "review_candidate_parameters": {
                    "step_up_m3_h": 2.0,
                    "minimum_ph_observation_seconds": 900.0,
                },
                "reviewed_parameters": {
                    "step_up_m3_h": None,
                },
            }
        )
        self.assertFalse(profile.reviewed_complete)
        self.assertFalse(profile.can_build_manual_trial_configs)
        self.assertIn("step_up_m3_h", profile.missing_reviewed_keys)
        with self.assertRaises(ValueError):
            profile.build_manual_trial_configs()

    def test_fully_reviewed_manual_design_builds_manual_configs_only(self):
        # Values are unit-test fixtures only, not site calibration.
        reviewed = {
            "step_up_m3_h": 2.0,
            "ph_lower_margin_inside_operating": 0.05,
            "ph_upper_margin_inside_operating": 0.05,
            "outlet_so2_headroom_to_safe_max": 5.0,
            "min_quiet_seconds": 3600.0,
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
            "min_abs_delta_so2": 0.5,
            "min_abs_delta_ph": 0.02,
            "minimum_so2_observation_seconds": 600.0,
            "minimum_ph_observation_seconds": 900.0,
        }
        profile = LocalStepIdentificationDesignProfile.from_mapping(
            {
                "design_id": "ID-DESIGN-REVIEWED",
                "status": "REVIEWED_MANUAL_ONLY",
                "activation_status": "NOT_ACTIVATABLE",
                "reviewed_parameters": reviewed,
            }
        )
        self.assertTrue(profile.reviewed_complete)
        self.assertTrue(profile.can_build_manual_trial_configs)
        configs = profile.build_manual_trial_configs()
        self.assertEqual(configs.identification.step_up_m3_h, 2.0)
        self.assertEqual(configs.trial.minimum_ph_observation_seconds, 900.0)
        self.assertTrue(configs.manual_only)
        self.assertFalse(configs.dcs_write_enabled)
        self.assertFalse(configs.normal_runtime_activation_allowed)
        with self.assertRaises(ValueError):
            profile.to_runtime_config()

    def test_protected_permission_flags_cannot_be_enabled(self):
        protected = (
            "automatic_execution_allowed",
            "automatic_escalation_allowed",
            "dcs_write_enabled",
            "learning_permission",
        )
        for field in protected:
            with self.subTest(field=field):
                payload = {
                    "design_id": "BAD-PERMISSION",
                    "status": "INCOMPLETE_REVIEW_REQUIRED",
                    "activation_status": "NOT_ACTIVATABLE",
                    field: True,
                }
                with self.assertRaises(ValueError):
                    LocalStepIdentificationDesignProfile.from_mapping(payload)

    def test_activation_status_cannot_be_changed(self):
        with self.assertRaises(ValueError):
            LocalStepIdentificationDesignProfile.from_mapping(
                {
                    "design_id": "BAD-ACTIVATION",
                    "status": "REVIEWED_MANUAL_ONLY",
                    "activation_status": "ACTIVATABLE",
                }
            )


if __name__ == "__main__":
    unittest.main()
