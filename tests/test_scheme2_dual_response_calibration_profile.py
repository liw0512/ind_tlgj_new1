import unittest

from system.model.map_control.mfac_model.bootstrap_trainer import MFACReplayConfig
from system.model.map_control.mfac_model.channel_calibration_review import (
    ObservedResponseTimingEvidence,
    approve_channel_calibration,
)
from system.model.map_control.mfac_model.channel_confidence_evidence import ChannelConfidenceEvidence
from system.model.map_control.mfac_model.dual_response_bootstrap import build_dual_response_bootstrap_evidence
from system.model.map_control.mfac_model.dual_response_calibration_profile import (
    CHANNEL_CALIBRATED,
    CHANNEL_INSUFFICIENT_EVIDENCE,
    CHANNEL_LOCAL_GAIN_READY,
    DualResponseCalibrationProfile,
    DualResponseChannelCalibration,
    build_calibration_profile_from_dual_bootstrap,
)
from system.model.map_control.mfac_model.mfac_schema import ActionResponseEvent, DelayProfile
from system.model.map_control.mfac_model.ph_bootstrap_trainer import PHReplayConfig


class Scheme2DualResponseCalibrationProfileTest(unittest.TestCase):
    EVENT_IDS = ("E1", "E2", "E3")

    @staticmethod
    def response_config(delay=190.0):
        return {
            "baseline_window_seconds": 300.0,
            "delay_onset_seconds": delay,
            "observation_seconds": 900.0,
            "measurement_window_seconds": 60.0,
            "max_sample_gap_seconds": 30.0,
            "target_change_tolerance": 0.0,
            "min_baseline_samples": 12,
            "min_response_samples": 6,
        }

    @classmethod
    def local_gain(cls, channel, event_ids=None):
        ids = tuple(event_ids or cls.EVENT_IDS)
        return DualResponseChannelCalibration(
            channel=channel,
            status=CHANNEL_LOCAL_GAIN_READY,
            phi_prior=-4.0 if channel == "SO2" else 0.05,
            phi_live0=-4.1 if channel == "SO2" else 0.051,
            valid_event_count=len(ids),
            independent_days=2,
            evidence_event_ids=ids,
        )

    @staticmethod
    def insufficient(channel):
        return DualResponseChannelCalibration(
            channel=channel,
            status=CHANNEL_INSUFFICIENT_EVIDENCE,
            reason_codes=("INSUFFICIENT_EVIDENCE",),
        )

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
    def timing(cls, channel, *, event_ids=("E1", "E2"), configured_boundary=False, sealed=True):
        delay = (
            DelayProfile(250.0, 310.0, 600.0, 780.0)
            if channel == "SO2"
            else DelayProfile(150.0, 190.0, 650.0, 900.0)
        )
        return ObservedResponseTimingEvidence(
            evidence_id="TIMING-%s" % channel,
            channel=channel,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            delay_profile=delay,
            event_ids=event_ids,
            observed_event_count=len(event_ids),
            independent_days=2,
            configured_window_boundary_used_as_observed_timing=configured_boundary,
            metadata=cls.timing_metadata() if sealed else {},
        )

    @classmethod
    def confidence_evidence(cls, channel, timing=None, cohort_event_ids=None):
        timing = timing or cls.timing(channel)
        cohort_ids = tuple(cohort_event_ids or cls.EVENT_IDS)
        coverage = float(len(timing.event_ids)) / float(len(cohort_ids))
        consistency = 2.0 / 3.0
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
            cohort_event_ids=cohort_ids,
            timing_event_ids=tuple(timing.event_ids),
            valid_event_count=len(cohort_ids),
            required_valid_trials=len(cohort_ids),
            independent_days=2,
            required_independent_days=2,
            event_count_sufficiency=1.0,
            independent_day_sufficiency=1.0,
            timing_coverage_ratio=coverage,
            phi_relative_mad=0.05,
            reviewed_phi_relative_mad_limit=0.10,
            phi_max_relative_deviation=0.10,
            reviewed_phi_max_relative_deviation_limit=0.20,
            phi_mad_consistency_score=consistency,
            phi_max_deviation_consistency_score=consistency,
            conservative_confidence_candidate=min(coverage, consistency),
        )

    @classmethod
    def review(cls, profile, channel):
        timing = cls.timing(channel)
        base = profile.so2 if channel == "SO2" else profile.ph
        return approve_channel_calibration(
            profile,
            channel=channel,
            timing_evidence=timing,
            confidence_evidence=cls.confidence_evidence(channel, timing, base.evidence_event_ids),
            response_config=cls.response_config(310.0 if channel == "SO2" else 190.0),
            confidence=0.8 if channel == "SO2" else 0.75,
            human_approved=True,
            reviewer_id="calibration-reviewer",
            review_time="2026-08-28T09:00:00+08:00",
        ).profile

    @staticmethod
    def approved_event(index, day):
        dq = 2.0
        return ActionResponseEvent(
            event_id="E%d" % index,
            condition_snapshot_version="v001",
            condition_label="17",
            base_condition_id="17",
            grid_id="P1-S17",
            policy_region_id="R_0017",
            mfac_context_id="CTX",
            action_start_time="2026-08-%02dT10:00:00+08:00" % day,
            action_source="MANUAL_LOCAL_STEP_IDENTIFICATION_REVIEWED",
            delta_q_actual=dq,
            delta_so2=-4.0 * dq,
            delta_ph=0.05 * dq,
            phi_event=-4.0,
            learning_eligible=True,
            metadata={
                "evidence_role": "LOCAL_GAIN",
                "manual_evidence_review_approved": True,
                "cohort_bootstrap_review_approved": True,
                "offline_bootstrap_evidence_allowed": True,
                "automatic_online_adaptation_allowed": False,
            },
        )

    def test_direct_calibrated_construction_is_rejected(self):
        with self.assertRaises(ValueError):
            DualResponseChannelCalibration(
                channel="SO2",
                status=CHANNEL_CALIBRATED,
                phi_prior=-4.0,
                phi_live0=-4.1,
                confidence=0.8,
                valid_event_count=3,
                independent_days=2,
                delay_profile=DelayProfile(250.0, 310.0, 600.0, 780.0),
                response_config=self.response_config(310.0),
                evidence_event_ids=self.EVENT_IDS,
            )

    def test_so2_and_ph_can_be_reviewed_independently(self):
        so2_profile = DualResponseCalibrationProfile(
            profile_id="P1",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=self.local_gain("SO2"),
            ph=self.insufficient("PH"),
        )
        reviewed = self.review(so2_profile, "SO2")
        self.assertTrue(reviewed.so2_calibrated)
        self.assertFalse(reviewed.ph_calibrated)

        ph_profile = DualResponseCalibrationProfile(
            profile_id="P2",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=self.insufficient("SO2"),
            ph=self.local_gain("PH"),
        )
        reviewed = self.review(ph_profile, "PH")
        self.assertTrue(reviewed.ph_calibrated)
        self.assertFalse(reviewed.so2_calibrated)

    def test_both_calibrated_still_cannot_activate_runtime(self):
        profile = DualResponseCalibrationProfile(
            profile_id="P3",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=self.local_gain("SO2"),
            ph=self.local_gain("PH"),
        )
        profile = self.review(profile, "SO2")
        profile = self.review(profile, "PH")
        self.assertTrue(profile.both_channels_calibrated)
        self.assertFalse(profile.can_enable_learning)
        self.assertFalse(profile.can_enable_residual)
        self.assertFalse(profile.can_enable_dcs)
        with self.assertRaises(ValueError):
            profile.to_runtime_config()

    def test_complete_but_invalid_response_window_is_not_approved(self):
        profile = DualResponseCalibrationProfile(
            profile_id="P-INVALID",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=self.local_gain("SO2"),
            ph=self.insufficient("PH"),
        )
        config = self.response_config(310.0)
        config["observation_seconds"] = 30.0
        config["measurement_window_seconds"] = 60.0
        timing = self.timing("SO2")
        with self.assertRaises(ValueError):
            approve_channel_calibration(
                profile,
                channel="SO2",
                timing_evidence=timing,
                confidence_evidence=self.confidence_evidence("SO2", timing),
                response_config=config,
                confidence=0.8,
                human_approved=True,
                reviewer_id="calibration-reviewer",
                review_time="2026-08-28T09:00:00+08:00",
            )

    def test_configured_window_boundary_cannot_be_observed_timing(self):
        with self.assertRaises(ValueError):
            self.timing("SO2", configured_boundary=True)

    def test_unsealed_timing_cannot_calibrate_channel(self):
        profile = DualResponseCalibrationProfile(
            profile_id="P-UNSEALED",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=self.local_gain("SO2"),
            ph=self.insufficient("PH"),
        )
        timing = self.timing("SO2", sealed=False)
        with self.assertRaises(ValueError):
            approve_channel_calibration(
                profile,
                channel="SO2",
                timing_evidence=timing,
                confidence_evidence=self.confidence_evidence("SO2", timing),
                response_config=self.response_config(310.0),
                confidence=0.8,
                human_approved=True,
                reviewer_id="calibration-reviewer",
                review_time="2026-08-28T09:00:00+08:00",
            )

    def test_timing_evidence_must_come_from_local_gain_cohort(self):
        profile = DualResponseCalibrationProfile(
            profile_id="P-TIMING",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=self.local_gain("SO2"),
            ph=self.insufficient("PH"),
        )
        timing = self.timing("SO2", event_ids=("E1", "OTHER"))
        with self.assertRaises(ValueError):
            approve_channel_calibration(
                profile,
                channel="SO2",
                timing_evidence=timing,
                confidence_evidence=self.confidence_evidence("SO2", timing),
                response_config=self.response_config(310.0),
                confidence=0.8,
                human_approved=True,
                reviewer_id="calibration-reviewer",
                review_time="2026-08-28T09:00:00+08:00",
            )

    def test_same_local_gain_cohort_is_required_when_both_channels_have_gain(self):
        with self.assertRaises(ValueError):
            DualResponseCalibrationProfile(
                profile_id="P4",
                condition_snapshot_version="v001",
                mfac_context_id="CTX",
                so2=self.local_gain("SO2", ("E1", "E2", "E3")),
                ph=self.local_gain("PH", ("E1", "E2", "OTHER")),
            )

    def test_physical_phi_direction_is_enforced(self):
        with self.assertRaises(ValueError):
            DualResponseChannelCalibration(
                channel="PH",
                status=CHANNEL_LOCAL_GAIN_READY,
                phi_prior=-0.05,
                phi_live0=-0.05,
                valid_event_count=3,
                independent_days=2,
                evidence_event_ids=self.EVENT_IDS,
            )

    def test_dual_bootstrap_becomes_local_gain_ready_not_calibrated(self):
        events = [self.approved_event(1, 27), self.approved_event(2, 27), self.approved_event(3, 28)]
        result = build_dual_response_bootstrap_evidence(
            events,
            so2_replay_config=MFACReplayConfig(eta=0.1, mu=1.0),
            ph_replay_config=PHReplayConfig(eta=0.1, mu=1.0),
        )
        profile = build_calibration_profile_from_dual_bootstrap(result.bundles[0], profile_id="P5")
        self.assertEqual(profile.so2.status, CHANNEL_LOCAL_GAIN_READY)
        self.assertEqual(profile.ph.status, CHANNEL_LOCAL_GAIN_READY)
        self.assertIsNone(profile.so2.confidence)
        self.assertIsNone(profile.ph.confidence)

    def test_serialization_round_trip_preserves_review_provenance(self):
        profile = DualResponseCalibrationProfile(
            profile_id="P6",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=self.local_gain("SO2"),
            ph=self.insufficient("PH"),
            metadata={"review_note": "test"},
        )
        restored = DualResponseCalibrationProfile.from_dict(self.review(profile, "SO2").to_dict())
        self.assertTrue(restored.so2_calibrated)
        self.assertTrue(restored.so2.metadata["calibration_review_approved"])
        self.assertTrue(restored.so2.metadata["timing_extraction_profile_reviewed"])
        self.assertTrue(restored.so2.metadata["confidence_evidence_id"])


if __name__ == "__main__":
    unittest.main()
