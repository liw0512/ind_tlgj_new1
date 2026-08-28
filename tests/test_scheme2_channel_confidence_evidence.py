import unittest

from system.model.map_control.mfac_model.channel_calibration_review import ObservedResponseTimingEvidence
from system.model.map_control.mfac_model.channel_confidence_evidence import build_channel_confidence_evidence
from system.model.map_control.mfac_model.local_gain_cohort_review import (
    LocalGainCohortConsistencyConfig,
    LocalGainCohortReview,
)
from system.model.map_control.mfac_model.mfac_schema import ActionResponseEvent, DelayProfile


class Scheme2ChannelConfidenceEvidenceTest(unittest.TestCase):
    EVENT_IDS = ("E1", "E2", "E3", "E4")

    @staticmethod
    def consistency_config():
        return LocalGainCohortConsistencyConfig(
            max_relative_mad_delta_q=0.10,
            max_relative_mad_phi_so2=0.10,
            max_relative_mad_phi_ph=0.12,
            max_relative_deviation_delta_q=0.20,
            max_relative_deviation_phi_so2=0.20,
            max_relative_deviation_phi_ph=0.25,
        )

    @classmethod
    def cohort_review(cls):
        return LocalGainCohortReview(
            review_id="COHORT-1",
            status="ADEQUATE_FOR_BOOTSTRAP_REVIEW",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            level_id="PHASE1_STEP_2",
            event_ids=cls.EVENT_IDS,
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

    @classmethod
    def approved_events(cls):
        events = []
        for index, event_id in enumerate(cls.EVENT_IDS, start=1):
            events.append(
                ActionResponseEvent(
                    event_id=event_id,
                    condition_snapshot_version="v001",
                    condition_label="17",
                    base_condition_id="17",
                    grid_id="P1-S17",
                    policy_region_id="R_0017",
                    mfac_context_id="CTX",
                    action_start_time="2026-08-%02dT10:00:00+08:00" % (26 + min(index, 3)),
                    action_source="MANUAL_LOCAL_STEP_IDENTIFICATION_REVIEWED",
                    delta_q_actual=2.0,
                    delta_so2=-8.0,
                    delta_ph=0.10,
                    phi_event=-4.0,
                    learning_eligible=True,
                    metadata={
                        "evidence_role": "LOCAL_GAIN",
                        "manual_evidence_review_approved": True,
                        "cohort_bootstrap_review_approved": True,
                        "offline_bootstrap_evidence_allowed": True,
                        "automatic_online_adaptation_allowed": False,
                        "cohort_review_id": "COHORT-1",
                        "cohort_review_reviewer_id": "cohort-reviewer",
                        "cohort_review_time": "2026-08-28T08:30:00+08:00",
                    },
                )
            )
        return events

    @staticmethod
    def timing_metadata():
        return {
            "timing_extraction_profile_id": "TIMING-DESIGN-1",
            "timing_extraction_profile_semantics": "SCHEME2_OBSERVED_TIMING_EXTRACTION_DESIGN_V2_REVIEW_SEALED",
            "timing_extraction_profile_reviewed": True,
            "timing_extraction_reviewer_id": "timing-reviewer",
            "timing_extraction_review_time": "2026-08-28T08:00:00+08:00",
            "calibration_review_eligible": True,
            "reviewed_extraction_parameters": {"onset_abs_threshold": 0.05},
            "candidate_parameters_used_for_extraction": False,
        }

    @classmethod
    def timing(cls, channel, event_ids=("E1", "E2", "E3"), *, sealed=True):
        return ObservedResponseTimingEvidence(
            evidence_id="TIMING-%s" % channel,
            channel=channel,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            delay_profile=DelayProfile(100.0, 130.0, 500.0, 650.0),
            event_ids=event_ids,
            observed_event_count=len(event_ids),
            independent_days=2,
            metadata=cls.timing_metadata() if sealed else {},
        )

    def build(self, channel, timing, evidence_id):
        return build_channel_confidence_evidence(
            self.cohort_review(),
            approved_events=self.approved_events(),
            channel=channel,
            consistency_config=self.consistency_config(),
            timing_evidence=timing,
            evidence_id=evidence_id,
        )

    def test_confidence_candidate_is_review_only_not_probability(self):
        evidence = self.build("SO2", self.timing("SO2"), "CONF-SO2-1")
        self.assertEqual(evidence.status, "READY_FOR_CONFIDENCE_REVIEW")
        self.assertTrue(evidence.cohort_bootstrap_review_approved)
        self.assertTrue(evidence.human_review_required)
        self.assertFalse(evidence.confidence_candidate_is_probability)
        self.assertFalse(evidence.learning_enabled)
        self.assertFalse(evidence.residual_control_enabled)
        self.assertFalse(evidence.dcs_write_enabled)
        self.assertAlmostEqual(evidence.timing_coverage_ratio, 0.75)
        self.assertLessEqual(evidence.conservative_confidence_candidate, 0.75)
        self.assertGreater(evidence.conservative_confidence_candidate, 0.0)
        self.assertTrue(evidence.metadata["timing_extraction_profile_reviewed"])

    def test_lower_timing_coverage_lowers_candidate(self):
        full = self.build("PH", self.timing("PH", self.EVENT_IDS), "CONF-PH-FULL")
        partial = self.build("PH", self.timing("PH", ("E1", "E2")), "CONF-PH-PARTIAL")
        self.assertGreater(full.timing_coverage_ratio, partial.timing_coverage_ratio)
        self.assertGreaterEqual(full.conservative_confidence_candidate, partial.conservative_confidence_candidate)

    def test_timing_outside_cohort_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build("SO2", self.timing("SO2", ("E1", "OTHER")), "CONF-BAD")

    def test_unreviewed_timing_provenance_is_rejected(self):
        with self.assertRaises(ValueError):
            self.build("SO2", self.timing("SO2", sealed=False), "CONF-UNSEALED-TIMING")

    def test_unapproved_cohort_copy_is_rejected(self):
        events = self.approved_events()
        events[0].learning_eligible = False
        with self.assertRaises(ValueError):
            build_channel_confidence_evidence(
                self.cohort_review(),
                approved_events=events,
                channel="SO2",
                consistency_config=self.consistency_config(),
                timing_evidence=self.timing("SO2"),
                evidence_id="CONF-UNAPPROVED",
            )

    def test_inadequate_cohort_cannot_build_confidence_evidence(self):
        review = self.cohort_review()
        object.__setattr__(review, "status", "INSUFFICIENT_EVIDENCE")
        object.__setattr__(review, "adequate_for_bootstrap_review", False)
        with self.assertRaises(ValueError):
            build_channel_confidence_evidence(
                review,
                approved_events=self.approved_events(),
                channel="SO2",
                consistency_config=self.consistency_config(),
                timing_evidence=self.timing("SO2"),
                evidence_id="CONF-BAD-COHORT",
            )


if __name__ == "__main__":
    unittest.main()
