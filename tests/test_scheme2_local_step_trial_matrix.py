import unittest

from system.model.map_control.mfac_model.local_step_trial_matrix import (
    LocalStepTrialLevel,
    LocalStepTrialMatrix,
)


class Scheme2LocalStepTrialMatrixTest(unittest.TestCase):
    def test_incomplete_review_level_cannot_start_manual_session(self):
        level = LocalStepTrialLevel(
            level_id="PHASE1_STEP_2",
            step_up_m3_h=2.0,
            max_step_up_m3_h=2.0,
            required_valid_trials=None,
            required_independent_days=None,
            review_status="REVIEW_REQUIRED",
        )
        matrix = LocalStepTrialMatrix(
            matrix_id="MATRIX-AUDIT",
            levels=(level,),
        )
        self.assertFalse(level.ready_for_manual_session)
        self.assertEqual(matrix.ready_level_ids, ())
        self.assertFalse(matrix.automatic_execution_allowed)
        self.assertFalse(matrix.automatic_escalation_allowed)

    def test_reviewed_level_requires_explicit_evidence_counts(self):
        incomplete = LocalStepTrialLevel(
            level_id="STEP2",
            step_up_m3_h=2.0,
            max_step_up_m3_h=2.0,
            review_status="REVIEWED",
        )
        self.assertFalse(incomplete.ready_for_manual_session)
        complete = LocalStepTrialLevel(
            level_id="STEP2",
            step_up_m3_h=2.0,
            max_step_up_m3_h=2.0,
            required_valid_trials=6,
            required_independent_days=3,
            review_status="REVIEWED",
        )
        self.assertTrue(complete.ready_for_manual_session)

    def test_next_level_never_escalates_without_human_review(self):
        first = LocalStepTrialLevel(
            level_id="STEP2",
            step_up_m3_h=2.0,
            max_step_up_m3_h=2.0,
            required_valid_trials=6,
            required_independent_days=3,
            review_status="REVIEWED",
        )
        second = LocalStepTrialLevel(
            level_id="STEP4",
            step_up_m3_h=4.0,
            max_step_up_m3_h=4.0,
            required_valid_trials=6,
            required_independent_days=3,
            review_status="REVIEW_REQUIRED",
        )
        matrix = LocalStepTrialMatrix(
            matrix_id="MATRIX-REVIEW",
            levels=(first, second),
        )
        self.assertFalse(
            matrix.can_consider_next_level(
                "STEP2",
                valid_trial_count=6,
                independent_days=3,
                human_review_approved=False,
            )
        )
        self.assertTrue(
            matrix.can_consider_next_level(
                "STEP2",
                valid_trial_count=6,
                independent_days=3,
                human_review_approved=True,
            )
        )

    def test_automatic_escalation_is_rejected_by_schema(self):
        with self.assertRaises(ValueError):
            LocalStepTrialLevel(
                level_id="BAD",
                step_up_m3_h=2.0,
                max_step_up_m3_h=2.0,
                automatic_escalation_allowed=True,
            )


if __name__ == "__main__":
    unittest.main()
