import unittest

from system.model.map_control.mfac_model.channel_calibration_review import (
    ObservedResponseTimingEvidence,
    approve_channel_calibration,
)
from system.model.map_control.mfac_model.channel_confidence_evidence import ChannelConfidenceEvidence
from system.model.map_control.mfac_model.dual_response_calibration_profile import (
    CHANNEL_LOCAL_GAIN_READY,
    DualResponseCalibrationProfile,
    DualResponseChannelCalibration,
)
from system.model.map_control.mfac_model.evidence_provenance_bundle import (
    DualResponseEvidenceProvenanceBundle,
    build_evidence_provenance_bundle,
    verify_evidence_provenance_bundle,
)
from system.model.map_control.mfac_model.local_step_raw_trace import LocalStepRawTraceBundle
from system.model.map_control.mfac_model.mfac_schema import ActionResponseEvent, DelayProfile
from system.model.map_control.mfac_model.observed_timing_extractor import (
    ObservedProcessTrace,
    ObservedTraceSample,
)


class Scheme2EvidenceProvenanceBundleTest(unittest.TestCase):
    EVENT_IDS = ("E1", "E2", "E3")

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
    def timing(cls, channel):
        return ObservedResponseTimingEvidence(
            evidence_id="TIMING-%s" % channel,
            channel=channel,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            delay_profile=DelayProfile(100.0, 150.0, 500.0, 700.0),
            event_ids=("E1", "E2"),
            observed_event_count=2,
            independent_days=2,
            metadata=cls.timing_metadata(),
        )

    @classmethod
    def confidence(cls, channel, timing):
        return ChannelConfidenceEvidence(
            evidence_id="CONF-%s" % channel,
            channel=channel,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            cohort_review_id="COHORT-1",
            cohort_bootstrap_review_approved=True,
            cohort_review_reviewer_id="cohort-reviewer",
            cohort_review_time="2026-08-28T08:30:00+08:00",
            timing_evidence_id=timing.evidence_id,
            cohort_event_ids=cls.EVENT_IDS,
            timing_event_ids=timing.event_ids,
            valid_event_count=3,
            required_valid_trials=3,
            independent_days=2,
            required_independent_days=2,
            event_count_sufficiency=1.0,
            independent_day_sufficiency=1.0,
            timing_coverage_ratio=2.0 / 3.0,
            phi_relative_mad=0.05,
            reviewed_phi_relative_mad_limit=0.10,
            phi_max_relative_deviation=0.10,
            reviewed_phi_max_relative_deviation_limit=0.20,
            phi_mad_consistency_score=2.0 / 3.0,
            phi_max_deviation_consistency_score=2.0 / 3.0,
            conservative_confidence_candidate=2.0 / 3.0,
        )

    @staticmethod
    def response_config():
        return {
            "baseline_window_seconds": 300.0,
            "delay_onset_seconds": 100.0,
            "observation_seconds": 900.0,
            "measurement_window_seconds": 60.0,
            "max_sample_gap_seconds": 30.0,
            "target_change_tolerance": 0.0,
            "min_baseline_samples": 12,
            "min_response_samples": 6,
        }

    @classmethod
    def calibrated_profile(cls, timings, confidences):
        profile = DualResponseCalibrationProfile(
            profile_id="CAL-PROVENANCE",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=DualResponseChannelCalibration(
                channel="SO2",
                status=CHANNEL_LOCAL_GAIN_READY,
                phi_prior=-4.0,
                phi_live0=-4.1,
                valid_event_count=3,
                independent_days=2,
                evidence_event_ids=cls.EVENT_IDS,
            ),
            ph=DualResponseChannelCalibration(
                channel="PH",
                status=CHANNEL_LOCAL_GAIN_READY,
                phi_prior=0.05,
                phi_live0=0.051,
                valid_event_count=3,
                independent_days=2,
                evidence_event_ids=cls.EVENT_IDS,
            ),
        )
        for channel in ("SO2", "PH"):
            profile = approve_channel_calibration(
                profile,
                channel=channel,
                timing_evidence=timings[channel],
                confidence_evidence=confidences[channel],
                response_config=cls.response_config(),
                confidence=0.8,
                human_approved=True,
                reviewer_id="calibration-reviewer",
                review_time="2026-08-28T09:00:00+08:00",
            ).profile
        return profile

    @staticmethod
    def raw_bundle(event_id, *, context="CTX"):
        samples = (
            ObservedTraceSample("2026-08-28T08:59:50+08:00", 10.0, True),
            ObservedTraceSample("2026-08-28T09:00:00+08:00", 10.0, True),
            ObservedTraceSample("2026-08-28T09:00:10+08:00", 9.8, True),
        )
        ph_samples = (
            ObservedTraceSample("2026-08-28T08:59:50+08:00", 6.20, True),
            ObservedTraceSample("2026-08-28T09:00:00+08:00", 6.20, True),
            ObservedTraceSample("2026-08-28T09:00:10+08:00", 6.22, True),
        )
        so2_trace = ObservedProcessTrace(
            trace_id="RAW-SO2-%s" % event_id,
            event_id=event_id,
            trial_id="TRIAL-%s" % event_id,
            channel="SO2",
            condition_snapshot_version="v001",
            mfac_context_id=context,
            actual_flow_reached_time="2026-08-28T09:00:00+08:00",
            samples=samples,
        )
        ph_trace = ObservedProcessTrace(
            trace_id="RAW-PH-%s" % event_id,
            event_id=event_id,
            trial_id="TRIAL-%s" % event_id,
            channel="PH",
            condition_snapshot_version="v001",
            mfac_context_id=context,
            actual_flow_reached_time="2026-08-28T09:00:00+08:00",
            samples=ph_samples,
        )
        return LocalStepRawTraceBundle(
            trial_id="TRIAL-%s" % event_id,
            event_id=event_id,
            tracking_event_id="TRACK-%s" % event_id,
            condition_snapshot_version="v001",
            mfac_context_id=context,
            actual_flow_reached_time="2026-08-28T09:00:00+08:00",
            so2_trace=so2_trace,
            ph_trace=ph_trace,
            status="TRACE_REVIEW_CANDIDATE",
            sample_count=3,
        )

    @classmethod
    def evidence_chain(cls):
        timings = {channel: cls.timing(channel) for channel in ("SO2", "PH")}
        confidences = {
            channel: cls.confidence(channel, timings[channel])
            for channel in ("SO2", "PH")
        }
        return (
            cls.approved_events(),
            [cls.raw_bundle("E1"), cls.raw_bundle("E2")],
            timings,
            confidences,
            cls.calibrated_profile(timings, confidences),
        )

    def test_complete_review_chain_is_content_addressed_but_not_activating(self):
        events, raw, timings, confidences, profile = self.evidence_chain()
        bundle = build_evidence_provenance_bundle(
            bundle_id="PROV-1",
            cohort_approved_events=events,
            raw_trace_bundles=raw,
            timing_evidence=timings,
            confidence_evidence=confidences,
            calibration_profile=profile,
        )
        self.assertEqual(bundle.status, "COMPLETE_REVIEW_CHAIN")
        self.assertTrue(bundle.is_complete_review_chain)
        self.assertEqual(bundle.blockers, ())
        self.assertFalse(bundle.learning_enabled)
        self.assertFalse(bundle.residual_control_enabled)
        self.assertFalse(bundle.dcs_write_enabled)
        with self.assertRaises(ValueError):
            bundle.to_runtime_config()

        restored = DualResponseEvidenceProvenanceBundle.from_dict(bundle.to_dict())
        self.assertEqual(restored.to_dict(), bundle.to_dict())
        verification = verify_evidence_provenance_bundle(
            restored,
            cohort_approved_events=events,
            raw_trace_bundles=raw,
            timing_evidence=timings,
            confidence_evidence=confidences,
            calibration_profile=profile,
        )
        self.assertTrue(verification.valid)
        self.assertEqual(verification.status, "VERIFIED")

    def test_source_content_change_breaks_digest_verification(self):
        events, raw, timings, confidences, profile = self.evidence_chain()
        bundle = build_evidence_provenance_bundle(
            bundle_id="PROV-2",
            cohort_approved_events=events,
            raw_trace_bundles=raw,
            timing_evidence=timings,
            confidence_evidence=confidences,
            calibration_profile=profile,
        )
        events[0].delta_so2 = -9.0
        verification = verify_evidence_provenance_bundle(
            bundle,
            cohort_approved_events=events,
            raw_trace_bundles=raw,
            timing_evidence=timings,
            confidence_evidence=confidences,
            calibration_profile=profile,
        )
        self.assertFalse(verification.valid)
        self.assertEqual(verification.status, "MISMATCH")

    def test_timing_requires_bound_raw_trace(self):
        events, raw, timings, confidences, profile = self.evidence_chain()
        with self.assertRaises(ValueError):
            build_evidence_provenance_bundle(
                bundle_id="PROV-3",
                cohort_approved_events=events,
                raw_trace_bundles=[raw[0]],
                timing_evidence=timings,
                confidence_evidence=confidences,
                calibration_profile=profile,
            )

    def test_mixed_context_raw_trace_is_rejected(self):
        events, raw, timings, confidences, profile = self.evidence_chain()
        raw[1] = self.raw_bundle("E2", context="OTHER")
        with self.assertRaises(ValueError):
            build_evidence_provenance_bundle(
                bundle_id="PROV-4",
                cohort_approved_events=events,
                raw_trace_bundles=raw,
                timing_evidence=timings,
                confidence_evidence=confidences,
                calibration_profile=profile,
            )


if __name__ == "__main__":
    unittest.main()
