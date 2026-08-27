import unittest

from system.model.map_control.mfac_model.dual_response_activation_review import (
    DualResponseActivationPrerequisites,
    evaluate_dual_response_activation_readiness,
)
from system.model.map_control.mfac_model.dual_response_calibration_profile import (
    CHANNEL_CALIBRATED,
    CHANNEL_INSUFFICIENT_EVIDENCE,
    DualResponseCalibrationProfile,
    DualResponseChannelCalibration,
)


class Scheme2DualResponseActivationReviewTest(unittest.TestCase):
    @staticmethod
    def response_config():
        # Unit-test values only, not site calibration.
        return {
            "baseline_window_seconds": 300.0,
            "delay_onset_seconds": 100.0,
            "observation_seconds": 600.0,
            "measurement_window_seconds": 60.0,
            "max_sample_gap_seconds": 30.0,
            "target_change_tolerance": 0.0,
            "min_baseline_samples": 12,
            "min_response_samples": 6,
        }

    @classmethod
    def channel(cls, name):
        return DualResponseChannelCalibration(
            channel=name,
            status=CHANNEL_CALIBRATED,
            phi_prior=-4.0 if name == "SO2" else 0.05,
            phi_live0=-4.1 if name == "SO2" else 0.051,
            confidence=0.8,
            valid_event_count=3,
            independent_days=2,
            response_config=cls.response_config(),
            evidence_event_ids=("E1", "E2", "E3"),
        )

    @classmethod
    def calibrated_profile(cls):
        return DualResponseCalibrationProfile(
            profile_id="CAL-P1",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=cls.channel("SO2"),
            ph=cls.channel("PH"),
        )

    @staticmethod
    def prerequisites(**overrides):
        values = {
            "expected_condition_snapshot_version": "v001",
            "expected_mfac_context_id": "CTX",
            "plant_contract_match_reviewed": True,
            "runtime_parameter_reviewed": True,
            "shadow_validation_reviewed": True,
            "causal_target_application_reviewed": True,
            "persistence_restore_reviewed": True,
            "rollback_plan_reviewed": True,
        }
        values.update(overrides)
        return DualResponseActivationPrerequisites(**values)

    def test_all_review_facts_only_reach_human_activation_review(self):
        result = evaluate_dual_response_activation_readiness(
            self.calibrated_profile(),
            self.prerequisites(),
        )
        self.assertEqual(result.status, "READY_FOR_HUMAN_ACTIVATION_REVIEW")
        self.assertTrue(result.ready_for_human_activation_review)
        self.assertTrue(result.profile_load_evidence_ready)
        self.assertTrue(result.online_learning_evidence_ready)
        self.assertTrue(result.residual_control_evidence_ready)
        self.assertFalse(result.learning_enabled)
        self.assertFalse(result.residual_control_enabled)
        self.assertFalse(result.dcs_write_enabled)
        self.assertFalse(result.can_enable_learning)
        self.assertFalse(result.can_enable_residual)
        self.assertFalse(result.can_enable_dcs)
        with self.assertRaises(ValueError):
            result.to_runtime_config()

    def test_missing_causal_target_review_blocks_learning_and_residual_readiness(self):
        result = evaluate_dual_response_activation_readiness(
            self.calibrated_profile(),
            self.prerequisites(causal_target_application_reviewed=False),
        )
        self.assertEqual(result.status, "NOT_READY")
        self.assertIn("CAUSAL_TARGET_APPLICATION_NOT_REVIEWED", result.blockers)
        self.assertTrue(result.profile_load_evidence_ready)
        self.assertFalse(result.online_learning_evidence_ready)
        self.assertFalse(result.residual_control_evidence_ready)

    def test_snapshot_mismatch_is_fail_closed(self):
        result = evaluate_dual_response_activation_readiness(
            self.calibrated_profile(),
            self.prerequisites(expected_condition_snapshot_version="v002"),
        )
        self.assertEqual(result.status, "NOT_READY")
        self.assertIn("CONDITION_SNAPSHOT_MISMATCH", result.blockers)
        self.assertFalse(result.profile_load_evidence_ready)

    def test_one_uncalibrated_channel_blocks_activation_review(self):
        profile = DualResponseCalibrationProfile(
            profile_id="CAL-P2",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=self.channel("SO2"),
            ph=DualResponseChannelCalibration(
                channel="PH",
                status=CHANNEL_INSUFFICIENT_EVIDENCE,
                reason_codes=("INSUFFICIENT_EVIDENCE",),
            ),
        )
        result = evaluate_dual_response_activation_readiness(
            profile,
            self.prerequisites(),
        )
        self.assertEqual(result.status, "NOT_READY")
        self.assertIn("PH_CHANNEL_NOT_CALIBRATED", result.blockers)
        self.assertFalse(result.profile_load_evidence_ready)
        self.assertFalse(result.ready_for_human_activation_review)


if __name__ == "__main__":
    unittest.main()
