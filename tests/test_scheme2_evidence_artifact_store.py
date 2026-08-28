import json
from pathlib import Path
import tempfile
import unittest

from system.model.config.mfac_paths import (
    MFAC_EVIDENCE_BUNDLES_DIR,
    MFAC_EVIDENCE_OBJECTS_DIR,
    MFAC_EVIDENCE_ROOT,
)
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
from system.model.map_control.mfac_model.evidence_artifact_store import (
    EvidenceArtifactLoader,
    EvidenceArtifactStore,
)
from system.model.map_control.mfac_model.evidence_provenance_bundle import (
    build_evidence_provenance_bundle,
)
from system.model.map_control.mfac_model.local_step_raw_trace import LocalStepRawTraceBundle
from system.model.map_control.mfac_model.mfac_schema import ActionResponseEvent, DelayProfile
from system.model.map_control.mfac_model.observed_timing_extractor import (
    ObservedProcessTrace,
    ObservedTraceSample,
)


class Scheme2EvidenceArtifactStoreTest(unittest.TestCase):
    EVENT_IDS = ("E1", "E2", "E3")

    @classmethod
    def approved_events(cls):
        result = []
        for index, event_id in enumerate(cls.EVENT_IDS, start=1):
            result.append(
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
        return result

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
            profile_id="CAL-STORE",
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
    def raw_bundle(event_id):
        so2_samples = (
            ObservedTraceSample("2026-08-28T08:59:50+08:00", 10.0, True),
            ObservedTraceSample("2026-08-28T09:00:00+08:00", 10.0, True),
            ObservedTraceSample("2026-08-28T09:00:10+08:00", 9.8, True),
        )
        ph_samples = (
            ObservedTraceSample("2026-08-28T08:59:50+08:00", 6.20, True),
            ObservedTraceSample("2026-08-28T09:00:00+08:00", 6.20, True),
            ObservedTraceSample("2026-08-28T09:00:10+08:00", 6.22, True),
        )
        so2 = ObservedProcessTrace(
            trace_id="RAW-SO2-%s" % event_id,
            event_id=event_id,
            trial_id="TRIAL-%s" % event_id,
            channel="SO2",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            actual_flow_reached_time="2026-08-28T09:00:00+08:00",
            samples=so2_samples,
        )
        ph = ObservedProcessTrace(
            trace_id="RAW-PH-%s" % event_id,
            event_id=event_id,
            trial_id="TRIAL-%s" % event_id,
            channel="PH",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            actual_flow_reached_time="2026-08-28T09:00:00+08:00",
            samples=ph_samples,
        )
        return LocalStepRawTraceBundle(
            trial_id="TRIAL-%s" % event_id,
            event_id=event_id,
            tracking_event_id="TRACK-%s" % event_id,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            actual_flow_reached_time="2026-08-28T09:00:00+08:00",
            so2_trace=so2,
            ph_trace=ph,
            status="TRACE_REVIEW_CANDIDATE",
            sample_count=3,
        )

    @classmethod
    def chain(cls, bundle_id="STORE-1"):
        timings = {channel: cls.timing(channel) for channel in ("SO2", "PH")}
        confidences = {channel: cls.confidence(channel, timings[channel]) for channel in ("SO2", "PH")}
        events = cls.approved_events()
        raw = [cls.raw_bundle("E1"), cls.raw_bundle("E2")]
        profile = cls.calibrated_profile(timings, confidences)
        bundle = build_evidence_provenance_bundle(
            bundle_id=bundle_id,
            cohort_approved_events=events,
            raw_trace_bundles=raw,
            timing_evidence=timings,
            confidence_evidence=confidences,
            calibration_profile=profile,
        )
        return bundle, events, raw, timings, confidences, profile

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def save_chain(self, bundle_id="STORE-1"):
        bundle, events, raw, timings, confidences, profile = self.chain(bundle_id)
        result = EvidenceArtifactStore(self.root).save(
            bundle,
            cohort_approved_events=events,
            raw_trace_bundles=raw,
            timing_evidence=timings,
            confidence_evidence=confidences,
            calibration_profile=profile,
        )
        return bundle, result

    def test_default_store_paths_use_canonical_contract(self):
        store = EvidenceArtifactStore()
        loader = EvidenceArtifactLoader()
        self.assertEqual(store.root, MFAC_EVIDENCE_ROOT)
        self.assertEqual(store.objects_root, MFAC_EVIDENCE_OBJECTS_DIR)
        self.assertEqual(store.bundles_root, MFAC_EVIDENCE_BUNDLES_DIR)
        self.assertEqual(loader.objects_root, MFAC_EVIDENCE_OBJECTS_DIR)
        self.assertEqual(loader.bundles_root, MFAC_EVIDENCE_BUNDLES_DIR)

    def test_atomic_round_trip_rebuilds_and_verifies_full_review_chain(self):
        bundle, written = self.save_chain()
        self.assertEqual(written.status, "STORED_REVIEW_EVIDENCE")
        self.assertTrue(written.review_chain_complete)
        self.assertTrue(Path(written.manifest_path).exists())
        self.assertFalse(list(self.root.rglob("*.tmp")))

        loaded = EvidenceArtifactLoader(self.root).load(bundle.bundle_id)
        self.assertTrue(loaded.loaded)
        self.assertEqual(loaded.status, "VERIFIED_REVIEW_EVIDENCE")
        self.assertTrue(loaded.review_chain_complete)
        self.assertIsNotNone(loaded.verification)
        self.assertTrue(loaded.verification.valid)
        self.assertEqual(set(loaded.timing_evidence), {"SO2", "PH"})
        self.assertEqual(set(loaded.confidence_evidence), {"SO2", "PH"})
        self.assertEqual(len(loaded.cohort_approved_events), 3)
        self.assertEqual(len(loaded.raw_trace_bundles), 2)
        self.assertFalse(loaded.learning_enabled)
        self.assertFalse(loaded.residual_control_enabled)
        self.assertFalse(loaded.dcs_write_enabled)
        with self.assertRaises(ValueError):
            loaded.to_runtime_config()

    def test_repeated_identical_save_is_idempotent(self):
        bundle, events, raw, timings, confidences, profile = self.chain("STORE-IDEMPOTENT")
        store = EvidenceArtifactStore(self.root)
        first = store.save(
            bundle,
            cohort_approved_events=events,
            raw_trace_bundles=raw,
            timing_evidence=timings,
            confidence_evidence=confidences,
            calibration_profile=profile,
        )
        before = Path(first.manifest_path).read_text(encoding="utf-8")
        second = store.save(
            bundle,
            cohort_approved_events=events,
            raw_trace_bundles=raw,
            timing_evidence=timings,
            confidence_evidence=confidences,
            calibration_profile=profile,
        )
        after = Path(second.manifest_path).read_text(encoding="utf-8")
        self.assertEqual(before, after)
        self.assertEqual(first.object_count, second.object_count)

    def test_same_bundle_id_cannot_overwrite_different_review_chain(self):
        bundle, events, raw, timings, confidences, profile = self.chain("STORE-IMMUTABLE")
        store = EvidenceArtifactStore(self.root)
        store.save(
            bundle,
            cohort_approved_events=events,
            raw_trace_bundles=raw,
            timing_evidence=timings,
            confidence_evidence=confidences,
            calibration_profile=profile,
        )
        events[0].delta_so2 = -9.0
        changed = build_evidence_provenance_bundle(
            bundle_id="STORE-IMMUTABLE",
            cohort_approved_events=events,
            raw_trace_bundles=raw,
            timing_evidence=timings,
            confidence_evidence=confidences,
            calibration_profile=profile,
        )
        with self.assertRaises(ValueError):
            store.save(
                changed,
                cohort_approved_events=events,
                raw_trace_bundles=raw,
                timing_evidence=timings,
                confidence_evidence=confidences,
                calibration_profile=profile,
            )
        loaded = EvidenceArtifactLoader(self.root).load("STORE-IMMUTABLE")
        self.assertTrue(loaded.loaded)
        self.assertEqual(loaded.cohort_approved_events[0].delta_so2, -8.0)

    def test_corrupt_existing_manifest_is_not_silently_overwritten(self):
        bundle, written = self.save_chain("STORE-CORRUPT-MANIFEST")
        manifest_path = Path(written.manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["activation_status"] = "ENABLED"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        bundle2, events, raw, timings, confidences, profile = self.chain(bundle.bundle_id)
        with self.assertRaises(ValueError):
            EvidenceArtifactStore(self.root).save(
                bundle2,
                cohort_approved_events=events,
                raw_trace_bundles=raw,
                timing_evidence=timings,
                confidence_evidence=confidences,
                calibration_profile=profile,
            )

    def test_tampered_content_addressed_object_is_rejected(self):
        bundle, _ = self.save_chain("STORE-TAMPER")
        digest = bundle.cohort_approved_events_ref.sha256
        object_path = self.root / "objects" / digest[:2] / (digest + ".json")
        payload = json.loads(object_path.read_text(encoding="utf-8"))
        payload[0]["delta_so2"] = -9.0
        object_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        loaded = EvidenceArtifactLoader(self.root).load(bundle.bundle_id)
        self.assertFalse(loaded.loaded)
        self.assertEqual(loaded.status, "EVIDENCE_INTEGRITY_FAILURE")
        self.assertTrue(any("digest mismatch" in reason for reason in loaded.reasons))

    def test_tampered_manifest_bundle_digest_is_rejected(self):
        bundle, written = self.save_chain("STORE-MANIFEST")
        manifest_path = Path(written.manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["provenance_bundle_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        loaded = EvidenceArtifactLoader(self.root).load(bundle.bundle_id)
        self.assertFalse(loaded.loaded)
        self.assertEqual(loaded.status, "EVIDENCE_INTEGRITY_FAILURE")
        self.assertTrue(any("manifest digest mismatch" in reason for reason in loaded.reasons))

    def test_missing_referenced_object_is_rejected(self):
        bundle, _ = self.save_chain("STORE-MISSING")
        digest = bundle.calibration_profile_ref.sha256
        object_path = self.root / "objects" / digest[:2] / (digest + ".json")
        object_path.unlink()
        loaded = EvidenceArtifactLoader(self.root).load(bundle.bundle_id)
        self.assertFalse(loaded.loaded)
        self.assertTrue(any("object is missing" in reason for reason in loaded.reasons))

    def test_unsafe_bundle_identifier_is_fail_closed(self):
        loaded = EvidenceArtifactLoader(self.root).load("../escape")
        self.assertFalse(loaded.loaded)
        self.assertEqual(loaded.status, "EVIDENCE_INTEGRITY_FAILURE")
        with self.assertRaises(ValueError):
            bundle, events, raw, timings, confidences, profile = self.chain("../escape")
            EvidenceArtifactStore(self.root).save(
                bundle,
                cohort_approved_events=events,
                raw_trace_bundles=raw,
                timing_evidence=timings,
                confidence_evidence=confidences,
                calibration_profile=profile,
            )

    def test_missing_manifest_is_not_treated_as_valid_evidence(self):
        loaded = EvidenceArtifactLoader(self.root).load("NOT-THERE")
        self.assertFalse(loaded.loaded)
        self.assertEqual(loaded.status, "EVIDENCE_BUNDLE_NOT_FOUND")
        self.assertFalse(loaded.review_chain_complete)


if __name__ == "__main__":
    unittest.main()
