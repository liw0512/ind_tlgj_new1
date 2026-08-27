import unittest

from system.model.map_control.mfac_model.channel_calibration_review import (
    ObservedResponseTimingEvidence,
    approve_channel_calibration,
)
from system.model.map_control.mfac_model.channel_confidence_evidence import (
    ChannelConfidenceEvidence,
)
from system.model.map_control.mfac_model.dual_response_calibration_profile import (
    CHANNEL_CALIBRATED,
    CHANNEL_LOCAL_GAIN_READY,
    DUAL_RESPONSE_CALIBRATION_PROFILE_VERSION,
    LEGACY_DUAL_RESPONSE_CALIBRATION_PROFILE_V2_VERSION,
    LEGACY_DUAL_RESPONSE_CALIBRATION_PROFILE_VERSION,
    DualResponseCalibrationProfile,
    DualResponseChannelCalibration,
)
from system.model.map_control.mfac_model.mfac_schema import DelayProfile


class Scheme2ChannelCalibrationReviewBoundaryTest(unittest.TestCase):
    @staticmethod
    def local_gain(channel):
        return DualResponseChannelCalibration(
            channel=channel,
            status=CHANNEL_LOCAL_GAIN_READY,
            phi_prior=-4.0 if channel == "SO2" else 0.05,
            phi_live0=-4.1 if channel == "SO2" else 0.051,
            valid_event_count=3,
            independent_days=2,
            evidence_event_ids=("E1", "E2", "E3"),
        )

    @staticmethod
    def timing(channel):
        return ObservedResponseTimingEvidence(
            evidence_id="OBS-%s" % channel,
            channel=channel,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            delay_profile=DelayProfile(
                onset_p50_seconds=100.0,
                onset_p90_seconds=150.0,
                response_p50_seconds=500.0,
                response_p90_seconds=700.0,
            ),
            event_ids=("E1", "E2"),
            observed_event_count=2,
            independent_days=2,
        )

    @staticmethod
    def confidence_evidence(channel, timing):
        return ChannelConfidenceEvidence(
            evidence_id="CONF-%s" % channel,
            channel=channel,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            cohort_review_id="COHORT-1",
            timing_evidence_id=timing.evidence_id,
            cohort_event_ids=("E1", "E2", "E3"),
            timing_event_ids=tuple(timing.event_ids),
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
    def base_profile(cls):
        return DualResponseCalibrationProfile(
            profile_id="P-BOUNDARY",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=cls.local_gain("SO2"),
            ph=cls.local_gain("PH"),
        )

    @classmethod
    def approve_so2(cls, profile=None, *, human_approved=True):
        profile = profile or cls.base_profile()
        timing = cls.timing("SO2")
        return approve_channel_calibration(
            profile,
            channel="SO2",
            timing_evidence=timing,
            confidence_evidence=cls.confidence_evidence("SO2", timing),
            response_config=cls.response_config(),
            confidence=0.8,
            human_approved=human_approved,
            reviewer_id="reviewer",
            review_time="2026-08-27T17:40:00+08:00",
        )

    def test_review_record_and_profile_remain_non_activating(self):
        result = self.approve_so2()
        self.assertEqual(result.profile.so2.status, CHANNEL_CALIBRATED)
        self.assertEqual(result.profile.ph.status, CHANNEL_LOCAL_GAIN_READY)
        self.assertEqual(result.review.activation_status, "NOT_ACTIVATABLE")
        self.assertFalse(result.review.learning_enabled)
        self.assertFalse(result.review.residual_control_enabled)
        self.assertFalse(result.review.dcs_write_enabled)
        self.assertTrue(result.review.confidence_evidence_id)
        self.assertFalse(result.review.metadata["confidence_candidate_is_not_probability"] is False)
        self.assertFalse(result.profile.can_enable_learning)
        self.assertFalse(result.profile.can_enable_residual)
        self.assertFalse(result.profile.can_enable_dcs)

    def test_human_approval_is_required(self):
        with self.assertRaises(ValueError):
            self.approve_so2(human_approved=False)

    def test_legacy_local_gain_profiles_migrate_to_v3(self):
        for legacy in (
            LEGACY_DUAL_RESPONSE_CALIBRATION_PROFILE_VERSION,
            LEGACY_DUAL_RESPONSE_CALIBRATION_PROFILE_V2_VERSION,
        ):
            payload = self.base_profile().to_dict()
            payload["semantics_version"] = legacy
            restored = DualResponseCalibrationProfile.from_dict(payload)
            self.assertEqual(
                restored.semantics_version,
                DUAL_RESPONSE_CALIBRATION_PROFILE_VERSION,
            )
            self.assertEqual(restored.so2.status, CHANNEL_LOCAL_GAIN_READY)
            self.assertEqual(restored.ph.status, CHANNEL_LOCAL_GAIN_READY)

    def test_legacy_calibrated_profiles_require_re_review(self):
        result = self.approve_so2()
        for legacy in (
            LEGACY_DUAL_RESPONSE_CALIBRATION_PROFILE_VERSION,
            LEGACY_DUAL_RESPONSE_CALIBRATION_PROFILE_V2_VERSION,
        ):
            payload = result.profile.to_dict()
            payload["semantics_version"] = legacy
            with self.assertRaises(ValueError):
                DualResponseCalibrationProfile.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
