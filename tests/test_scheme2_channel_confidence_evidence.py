import unittest

from system.model.map_control.mfac_model.channel_calibration_review import (
    ObservedResponseTimingEvidence,
)
from system.model.map_control.mfac_model.channel_confidence_evidence import (
    build_channel_confidence_evidence,
)
from system.model.map_control.mfac_model.local_gain_cohort_review import (
    LocalGainCohortConsistencyConfig,
    LocalGainCohortReview,
)
from system.model.map_control.mfac_model.mfac_schema import DelayProfile


class Scheme2ChannelConfidenceEvidenceTest(unittest.TestCase):
    @staticmethod
    def consistency_config():
        # Unit-test values only; not plant calibration.
        return LocalGainCohortConsistencyConfig(
            max_relative_mad_delta_q=0.10,
            max_relative_mad_phi_so2=0.10,
            max_relative_mad_phi_ph=0.12,
            max_relative_deviation_delta_q=0.20,
            max_relative_deviation_phi_so2=0.20,
            max_relative_deviation_phi_ph=0.25,
        )

    @staticmethod
    def cohort_review():
        return LocalGainCohortReview(
            review_id="COHORT-1",
            status="ADEQUATE_FOR_BOOTSTRAP_REVIEW",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            level_id="PHASE1_STEP_2",
            event_ids=("E1", "E2", "E3", "E4"),
            valid_event_count=4,
            independent_days=3,
            required_valid_trials=3,
            required_independent_days=2,
            delta_q_distribution={"relative_mad": 0.03, "max_relative_deviation": 0.08},
            phi_so2_distribution={"relative_mad": 0.05, "max_relative_deviation": 0.10},
            phi_ph_distribution={"relative_mad": 0.06, "max_relative_deviation": 0.12},
            reasons=(),
            adequate_for_bootstrap_review=True,
            bootstrap_review_approved=False,
            learning_permission=False,
            automatic_online_adaptation_allowed=False,
            dcs_write_enabled=False,
        )

    @staticmethod
    def timing(channel, event_ids=("E1", "E2", "E3")):
        return ObservedResponseTimingEvidence(
            evidence_id="TIMING-%s" % channel,
            channel=channel,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            delay_profile=DelayProfile(
                onset_p50_seconds=100.0,
                onset_p90_seconds=130.0,
                response_p50_seconds=500.0,
                response_p90_seconds=650.0,
            ),
            event_ids=event_ids,
            observed_event_count=len(event_ids),
            independent_days=2,
        )

    def test_confidence_candidate_is_review_only_not_probability(self):
        evidence = build_channel_confidence_evidence(
            self.cohort_review(),
            channel="SO2",
            consistency_config=self.consistency_config(),
            timing_evidence=self.timing("SO2"),
            evidence_id="CONF-SO2-1",
        )
        self.assertEqual(evidence.status, "READY_FOR_CONFIDENCE_REVIEW")
        self.assertTrue(evidence.human_review_required)
        self.assertFalse(evidence.confidence_candidate_is_probability)
        self.assertFalse(evidence.learning_enabled)
        self.assertFalse(evidence.residual_control_enabled)
        self.assertFalse(evidence.dcs_write_enabled)
        self.assertAlmostEqual(evidence.timing_coverage_ratio, 0.75)
        self.assertLessEqual(evidence.conservative_confidence_candidate, 0.75)
        self.assertGreater(evidence.conservative_confidence_candidate, 0.0)

    def test_lower_timing_coverage_lowers_candidate(self):
        full = build_channel_confidence_evidence(
            self.cohort_review(),
            channel="PH",
            consistency_config=self.consistency_config(),
            timing_evidence=self.timing("PH", ("E1", "E2", "E3", "E4")),
            evidence_id="CONF-PH-FULL",
        )
        partial = build_channel_confidence_evidence(
            self.cohort_review(),
            channel="PH",
            consistency_config=self.consistency_config(),
            timing_evidence=self.timing("PH", ("E1", "E2")),
            evidence_id="CONF-PH-PARTIAL",
        )
        self.assertGreater(full.timing_coverage_ratio, partial.timing_coverage_ratio)
        self.assertGreaterEqual(
            full.conservative_confidence_candidate,
            partial.conservative_confidence_candidate,
        )

    def test_timing_outside_cohort_is_rejected(self):
        with self.assertRaises(ValueError):
            build_channel_confidence_evidence(
                self.cohort_review(),
                channel="SO2",
                consistency_config=self.consistency_config(),
                timing_evidence=self.timing("SO2", ("E1", "OTHER")),
                evidence_id="CONF-BAD",
            )

    def test_inadequate_cohort_cannot_build_confidence_evidence(self):
        review = self.cohort_review()
        object.__setattr__(review, "status", "INSUFFICIENT_EVIDENCE")
        object.__setattr__(review, "adequate_for_bootstrap_review", False)
        with self.assertRaises(ValueError):
            build_channel_confidence_evidence(
                review,
                channel="SO2",
                consistency_config=self.consistency_config(),
                timing_evidence=self.timing("SO2"),
                evidence_id="CONF-BAD-COHORT",
            )


if __name__ == "__main__":
    unittest.main()
