import unittest
from datetime import datetime, timedelta, timezone

from system.model.map_control.mfac_model.observed_timing_extractor import (
    ObservedProcessTrace,
    ObservedTimingExtractionConfig,
    ObservedTraceSample,
    build_observed_response_timing_evidence,
    extract_observed_timing_from_trace,
)


class Scheme2ObservedTimingExtractorTest(unittest.TestCase):
    @staticmethod
    def config():
        # Unit-test values only; not site calibration.
        return ObservedTimingExtractionConfig(
            baseline_window_seconds=60.0,
            max_observation_seconds=120.0,
            max_sample_gap_seconds=15.0,
            smoothing_window_samples=1,
            onset_abs_threshold=0.03,
            onset_sustain_samples=2,
            response_fraction_of_extremum=0.8,
            response_sustain_samples=2,
            min_response_abs_amplitude=0.08,
            min_baseline_samples=6,
            min_post_reach_samples=8,
        )

    @staticmethod
    def trace(channel, event_id, day=27, gap=False):
        reached = datetime(2026, 8, day, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        samples = []
        baseline = 10.0 if channel == "SO2" else 6.2
        for seconds in range(-60, 1, 10):
            samples.append(ObservedTraceSample((reached + timedelta(seconds=seconds)).isoformat(), baseline))
        if channel == "PH":
            values = [6.20, 6.20, 6.22, 6.24, 6.27, 6.30, 6.31, 6.31, 6.31, 6.31, 6.31, 6.31]
        else:
            values = [10.0, 9.8, 9.6, 9.2, 8.5, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0, 8.0]
        for index, value in enumerate(values, start=1):
            seconds = index * 10
            if gap and index >= 5:
                seconds += 20
            samples.append(ObservedTraceSample((reached + timedelta(seconds=seconds)).isoformat(), value))
        return ObservedProcessTrace(
            trace_id="TRACE-%s-%s" % (channel, event_id),
            event_id=event_id,
            trial_id="TRIAL-%s" % event_id,
            channel=channel,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            actual_flow_reached_time=reached.isoformat(),
            samples=tuple(samples),
        )

    def test_ph_timing_is_extracted_from_raw_trace(self):
        result = extract_observed_timing_from_trace(self.trace("PH", "E1"), self.config())
        self.assertEqual(result.status, "EXTRACTED")
        self.assertEqual(result.observed_onset_seconds, 40.0)
        self.assertEqual(result.observed_response_seconds, 60.0)
        self.assertGreater(result.observed_directional_extremum, 0.10)
        self.assertFalse(result.metadata["configured_window_boundary_used_as_observed_timing"])

    def test_so2_direction_is_negative_in_process_but_positive_in_directional_response(self):
        config = self.config()
        config = ObservedTimingExtractionConfig(
            **{**config.__dict__, "onset_abs_threshold": 0.3, "min_response_abs_amplitude": 1.0}
        )
        result = extract_observed_timing_from_trace(self.trace("SO2", "E1"), config)
        self.assertEqual(result.status, "EXTRACTED")
        self.assertEqual(result.observed_onset_seconds, 30.0)
        self.assertEqual(result.observed_response_seconds, 60.0)
        self.assertGreaterEqual(result.observed_directional_extremum, 2.0)

    def test_two_independent_traces_build_observed_timing_evidence(self):
        traces = [self.trace("PH", "E1", day=27), self.trace("PH", "E2", day=28)]
        result = build_observed_response_timing_evidence(
            traces,
            config=self.config(),
            evidence_id="TIMING-PH-1",
        )
        self.assertEqual(result.status, "OBSERVED_TIMING_REVIEW_CANDIDATE")
        self.assertIsNotNone(result.timing_evidence)
        evidence = result.timing_evidence
        self.assertEqual(evidence.event_ids, ("E1", "E2"))
        self.assertEqual(evidence.independent_days, 2)
        self.assertEqual(evidence.delay_profile.onset_p50_seconds, 40.0)
        self.assertEqual(evidence.delay_profile.response_p90_seconds, 60.0)
        self.assertEqual(
            evidence.metadata["response_timing_definition"],
            "FIRST_SUSTAINED_REVIEWED_FRACTION_OF_OBSERVED_EXTREMUM",
        )

    def test_sample_gap_rejects_trace(self):
        result = extract_observed_timing_from_trace(
            self.trace("PH", "E1", gap=True),
            self.config(),
        )
        self.assertEqual(result.status, "REJECTED")
        self.assertIn("SAMPLE_GAP", result.reasons)

    def test_configured_window_boundary_cannot_enter_raw_trace_contract(self):
        trace = self.trace("PH", "E1")
        with self.assertRaises(ValueError):
            ObservedProcessTrace(
                trace_id=trace.trace_id,
                event_id=trace.event_id,
                trial_id=trace.trial_id,
                channel=trace.channel,
                condition_snapshot_version=trace.condition_snapshot_version,
                mfac_context_id=trace.mfac_context_id,
                actual_flow_reached_time=trace.actual_flow_reached_time,
                samples=trace.samples,
                configured_window_boundary_used_as_observed_timing=True,
            )


if __name__ == "__main__":
    unittest.main()
