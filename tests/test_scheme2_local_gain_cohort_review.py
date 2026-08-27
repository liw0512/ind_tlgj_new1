import unittest

from system.model.map_control.mfac_model.bootstrap_trainer import (
    MFACReplayConfig,
    build_bootstrap_evidence,
)
from system.model.map_control.mfac_model.local_gain_cohort_review import (
    LocalGainCohortConsistencyConfig,
    LocalGainCohortReviewProfile,
    approve_local_gain_cohort_for_bootstrap,
    evaluate_local_gain_cohort,
)
from system.model.map_control.mfac_model.local_step_trial_matrix import (
    LocalStepTrialLevel,
)
from system.model.map_control.mfac_model.mfac_schema import ActionResponseEvent
from system.model.map_control.mfac_model.ph_bootstrap_trainer import (
    PHReplayConfig,
    build_ph_bootstrap_evidence,
)


class Scheme2LocalGainCohortReviewTest(unittest.TestCase):
    @staticmethod
    def level(*, reviewed=True):
        return LocalStepTrialLevel(
            level_id="PHASE1_STEP_2",
            step_up_m3_h=2.0,
            max_step_up_m3_h=2.0,
            required_valid_trials=3 if reviewed else None,
            required_independent_days=2 if reviewed else None,
            review_status="REVIEWED" if reviewed else "REVIEW_REQUIRED",
        )

    @staticmethod
    def config():
        # Unit-test thresholds only; these are not site calibration values.
        return LocalGainCohortConsistencyConfig(
            max_relative_mad_delta_q=0.10,
            max_relative_mad_phi_so2=0.10,
            max_relative_mad_phi_ph=0.10,
            max_relative_deviation_delta_q=0.20,
            max_relative_deviation_phi_so2=0.20,
            max_relative_deviation_phi_ph=0.20,
        )

    @staticmethod
    def event(index, *, day, delta_q=2.0, phi_so2=-4.0, phi_ph=0.05, context="CTX"):
        return ActionResponseEvent(
            event_id="E%d" % index,
            condition_snapshot_version="v001",
            condition_label="17",
            base_condition_id="17",
            grid_id="P1-S17",
            policy_region_id="R_0017",
            mfac_context_id=context,
            action_start_time="2026-08-%02dT10:00:00+08:00" % day,
            action_source="MANUAL_LOCAL_STEP_IDENTIFICATION_REVIEWED",
            delta_q_actual=delta_q,
            delta_so2=phi_so2 * delta_q,
            delta_ph=phi_ph * delta_q,
            phi_event=phi_so2,
            learning_eligible=False,
            metadata={
                "evidence_role": "LOCAL_GAIN",
                "identification_trial_id": "TRIAL-%d" % index,
                "manual_evidence_review_approved": True,
                "approved_step_up_m3_h": 2.0,
                "phi_ph_event": phi_ph,
                "cohort_bootstrap_review_required": True,
                "cohort_bootstrap_review_approved": False,
                "offline_bootstrap_evidence_allowed": False,
                "automatic_online_adaptation_allowed": False,
            },
        )

    @classmethod
    def consistent_events(cls):
        return [
            cls.event(1, day=27, delta_q=2.0, phi_so2=-4.0, phi_ph=0.050),
            cls.event(2, day=27, delta_q=2.1, phi_so2=-4.2, phi_ph=0.052),
            cls.event(3, day=28, delta_q=1.9, phi_so2=-3.8, phi_ph=0.048),
        ]

    def test_profile_has_no_defaults_and_requires_reviewed_thresholds(self):
        profile = LocalGainCohortReviewProfile.from_mapping(
            {
                "profile_id": "COHORT-DESIGN",
                "status": "INCOMPLETE_REVIEW_REQUIRED",
                "activation_status": "NOT_ACTIVATABLE",
                "reviewed_parameters": {},
            }
        )
        self.assertFalse(profile.can_build_config)
        self.assertEqual(len(profile.missing_reviewed_keys), 6)
        with self.assertRaises(ValueError):
            profile.build_config()

    def test_single_trial_is_insufficient_even_when_individually_reviewed(self):
        review = evaluate_local_gain_cohort(
            [self.event(1, day=27)],
            level=self.level(),
            config=self.config(),
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
        )
        self.assertEqual(review.status, "INSUFFICIENT_EVIDENCE")
        self.assertIn(
            "VALID_TRIAL_COUNT_BELOW_REVIEWED_REQUIREMENT",
            review.reasons,
        )
        self.assertIn(
            "INDEPENDENT_DAYS_BELOW_REVIEWED_REQUIREMENT",
            review.reasons,
        )
        self.assertFalse(review.adequate_for_bootstrap_review)
        self.assertFalse(review.learning_permission)

    def test_required_independent_days_are_owned_by_trial_matrix(self):
        events = [
            self.event(1, day=27),
            self.event(2, day=27, phi_so2=-4.1, phi_ph=0.051),
            self.event(3, day=27, phi_so2=-3.9, phi_ph=0.049),
        ]
        review = evaluate_local_gain_cohort(
            events,
            level=self.level(),
            config=self.config(),
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
        )
        self.assertEqual(review.valid_event_count, 3)
        self.assertEqual(review.independent_days, 1)
        self.assertEqual(review.required_valid_trials, 3)
        self.assertEqual(review.required_independent_days, 2)
        self.assertEqual(review.status, "INSUFFICIENT_EVIDENCE")
        self.assertIn(
            "INDEPENDENT_DAYS_BELOW_REVIEWED_REQUIREMENT",
            review.reasons,
        )

    def test_consistent_multi_day_cohort_becomes_review_candidate_only(self):
        review = evaluate_local_gain_cohort(
            self.consistent_events(),
            level=self.level(),
            config=self.config(),
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
        )
        self.assertEqual(review.status, "ADEQUATE_FOR_BOOTSTRAP_REVIEW")
        self.assertTrue(review.adequate_for_bootstrap_review)
        self.assertFalse(review.bootstrap_review_approved)
        self.assertFalse(review.learning_permission)
        self.assertLessEqual(review.delta_q_distribution["relative_mad"], 0.10)
        self.assertLessEqual(review.phi_so2_distribution["relative_mad"], 0.10)
        self.assertLessEqual(review.phi_ph_distribution["relative_mad"], 0.10)

    def test_single_outlier_is_caught_by_max_relative_deviation(self):
        events = self.consistent_events()
        events[-1] = self.event(
            3,
            day=28,
            delta_q=1.9,
            phi_so2=-8.0,
            phi_ph=0.048,
        )
        review = evaluate_local_gain_cohort(
            events,
            level=self.level(),
            config=self.config(),
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
        )
        self.assertEqual(review.status, "INCONSISTENT_LOCAL_GAIN")
        self.assertIn("PHI_SO2_MAX_RELATIVE_DEVIATION_TOO_LARGE", review.reasons)
        self.assertFalse(review.adequate_for_bootstrap_review)

    def test_mixed_context_is_rejected_not_silently_grouped(self):
        events = self.consistent_events()
        events[-1] = self.event(3, day=28, context="OTHER")
        review = evaluate_local_gain_cohort(
            events,
            level=self.level(),
            config=self.config(),
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
        )
        self.assertEqual(review.status, "REJECTED_INVALID_COHORT")
        self.assertIn("MIXED_MFAC_CONTEXT", review.reasons)

    def test_human_cohort_approval_creates_bootstrap_eligible_copies(self):
        events = self.consistent_events()
        review = evaluate_local_gain_cohort(
            events,
            level=self.level(),
            config=self.config(),
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
        )
        with self.assertRaises(ValueError):
            approve_local_gain_cohort_for_bootstrap(
                review,
                events,
                human_approved=False,
                reviewer_id="cohort-reviewer",
                review_time="2026-08-29T09:00:00+08:00",
            )

        approved = approve_local_gain_cohort_for_bootstrap(
            review,
            events,
            human_approved=True,
            reviewer_id="cohort-reviewer",
            review_time="2026-08-29T09:00:00+08:00",
        )
        self.assertEqual(len(approved), 3)
        self.assertTrue(all(event.learning_eligible for event in approved))
        self.assertTrue(
            all(
                event.metadata["cohort_bootstrap_review_approved"]
                for event in approved
            )
        )
        self.assertTrue(
            all(
                event.metadata["automatic_online_adaptation_allowed"] is False
                for event in approved
            )
        )
        self.assertTrue(all(not event.learning_eligible for event in events))

        so2 = build_bootstrap_evidence(
            approved,
            MFACReplayConfig(eta=0.1, mu=1.0),
        )
        ph = build_ph_bootstrap_evidence(
            approved,
            PHReplayConfig(eta=0.1, mu=1.0),
        )
        self.assertEqual(len(so2), 1)
        self.assertEqual(len(ph), 1)
        self.assertEqual(so2[0].valid_event_count, 3)
        self.assertEqual(ph[0].valid_event_count, 3)
        self.assertEqual(so2[0].independent_days, 2)
        self.assertEqual(ph[0].independent_days, 2)
        self.assertLess(so2[0].phi_seed, 0.0)
        self.assertGreater(ph[0].phi_seed, 0.0)


if __name__ == "__main__":
    unittest.main()
