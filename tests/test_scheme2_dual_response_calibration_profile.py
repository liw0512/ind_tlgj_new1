import unittest

from system.model.map_control.mfac_model.bootstrap_trainer import MFACReplayConfig
from system.model.map_control.mfac_model.channel_calibration_review import (
    ObservedResponseTimingEvidence,
    approve_channel_calibration,
)
from system.model.map_control.mfac_model.dual_response_bootstrap import (
    build_dual_response_bootstrap_evidence,
)
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
        # Unit-test values only, not plant calibration.
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

    @staticmethod
    def local_gain(channel, event_ids=EVENT_IDS):
        if channel == "SO2":
            phi_prior, phi_live0 = -4.0, -4.1
        else:
            phi_prior, phi_live0 = 0.05, 0.051
        return DualResponseChannelCalibration(
            channel=channel,
            status=CHANNEL_LOCAL_GAIN_READY,
            phi_prior=phi_prior,
            phi_live0=phi_live0,
            valid_event_count=len(event_ids),
            independent_days=2,
            evidence_event_ids=event_ids,
        )

    @staticmethod
    def insufficient(channel):
        return DualResponseChannelCalibration(
            channel=channel,
            status=CHANNEL_INSUFFICIENT_EVIDENCE,
            reason_codes=("INSUFFICIENT_EVIDENCE",),
        )

    @classmethod
    def timing(cls, channel, *, event_ids=("E1", "E2"), configured_boundary=False):
        if channel == "SO2":
            delay = DelayProfile(
                onset_p50_seconds=250.0,
                onset_p90_seconds=310.0,
                response_p50_seconds=600.0,
                response_p90_seconds=780.0,
            )
        else:
            delay = DelayProfile(
                onset_p50_seconds=150.0,
                onset_p90_seconds=190.0,
                response_p50_seconds=650.0,
                response_p90_seconds=900.0,
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
        )

    @classmethod
    def review(cls, profile, channel):
        result = approve_channel_calibration(
            profile,
            channel=channel,
            timing_evidence=cls.timing(channel),
            response_config=cls.response_config(310.0 if channel == "SO2" else 190.0),
            confidence=0.8 if channel == "SO2" else 0.75,
            human_approved=True,
            reviewer_id="calibration-reviewer",
            review_time="2026-08-27T17:30:00+08:00",
        )
        return result.profile

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
                delay_profile=DelayProfile(
                    onset_p50_seconds=250.0,
                    onset_p90_seconds=310.0,
                    response_p50_seconds=600.0,
                    response_p90_seconds=780.0,
                ),
                response_config=self.response_config(310.0),
                evidence_event_ids=self.EVENT_IDS,
            )

    def test_so2_can_be_calibrated_while_ph_is_insufficient(self):
        profile = DualResponseCalibrationProfile(
            profile_id="P1",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=self.local_gain("SO2"),
            ph=self.insufficient("PH"),
        )
        reviewed = self.review(profile, "SO2")
        self.assertTrue(reviewed.so2_calibrated)
        self.assertFalse(reviewed.ph_calibrated)
        self.assertFalse(reviewed.both_channels_calibrated)
        self.assertFalse(reviewed.can_enable_learning)
        self.assertFalse(reviewed.can_enable_residual)

    def test_ph_can_be_calibrated_while_so2_is_insufficient(self):
        profile = DualResponseCalibrationProfile(
            profile_id="P2",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=self.insufficient("SO2"),
            ph=self.local_gain("PH"),
        )
        reviewed = self.review(profile, "PH")
        self.assertFalse(reviewed.so2_calibrated)
        self.assertTrue(reviewed.ph_calibrated)
        self.assertFalse(reviewed.both_channels_calibrated)

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
        with self.assertRaises(ValueError):
            approve_channel_calibration(
                profile,
                channel="SO2",
                timing_evidence=self.timing("SO2"),
                response_config=config,
                confidence=0.8,
                human_approved=True,
                reviewer_id="calibration-reviewer",
                review_time="2026-08-27T17:30:00+08:00",
            )

    def test_configured_window_boundary_cannot_be_observed_timing(self):
        with self.assertRaises(ValueError):
            self.timing("SO2", configured_boundary=True)

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
                response_config=self.response_config(310.0),
                confidence=0.8,
                human_approved=True,
                reviewer_id="calibration-reviewer",
                review_time="2026-08-27T17:30:00+08:00",
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
        events = [
            self.approved_event(1, 27),
            self.approved_event(2, 27),
            self.approved_event(3, 28),
        ]
        result = build_dual_response_bootstrap_evidence(
            events,
            so2_replay_config=MFACReplayConfig(eta=0.1, mu=1.0),
            ph_replay_config=PHReplayConfig(eta=0.1, mu=1.0),
        )
        self.assertEqual(len(result.bundles), 1)
        profile = build_calibration_profile_from_dual_bootstrap(
            result.bundles[0],
            profile_id="P5",
        )
        self.assertEqual(profile.so2.status, CHANNEL_LOCAL_GAIN_READY)
        self.assertEqual(profile.ph.status, CHANNEL_LOCAL_GAIN_READY)
        self.assertFalse(profile.so2_calibrated)
        self.assertFalse(profile.ph_calibrated)
        self.assertIsNone(profile.so2.confidence)
        self.assertIsNone(profile.ph.confidence)
        self.assertEqual(profile.so2.response_config, {})
        self.assertEqual(profile.ph.response_config, {})

    def test_serialization_round_trip_preserves_review_seal(self):
        profile = DualResponseCalibrationProfile(
            profile_id="P6",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            so2=self.local_gain("SO2"),
            ph=self.insufficient("PH"),
            metadata={"review_note": "test"},
        )
        profile = self.review(profile, "SO2")
        restored = DualResponseCalibrationProfile.from_dict(profile.to_dict())
        self.assertEqual(restored.profile_id, profile.profile_id)
        self.assertTrue(restored.so2_calibrated)
        self.assertFalse(restored.ph_calibrated)
        self.assertEqual(restored.metadata["review_note"], "test")
        self.assertTrue(restored.so2.metadata["calibration_review_approved"])


if __name__ == "__main__":
    unittest.main()
