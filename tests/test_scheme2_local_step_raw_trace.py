import unittest
from datetime import datetime, timedelta, timezone

from system.model.map_control.mfac_model.local_step_raw_trace import (
    LocalStepRawTraceRecorder,
)
from system.model.map_control.mfac_model.local_step_trial_protocol import (
    LocalStepTrialPlan,
)


class Scheme2LocalStepRawTraceTest(unittest.TestCase):
    @staticmethod
    def plan():
        return LocalStepTrialPlan(
            trial_id="TRIAL-1",
            proposal_id="PROPOSAL-1",
            reviewer_id="reviewer",
            approval_time="2026-08-27T10:00:00+08:00",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            pretrial_actual_supply_flow=30.0,
            pretrial_qbase_effective=30.0,
            pretrial_ph=6.2,
            pretrial_outlet_so2=10.0,
            approved_test_target_supply_flow=32.0,
            approved_step_up_m3_h=2.0,
            manual_return_target_supply_flow=30.0,
        )

    @staticmethod
    def time(seconds):
        base = datetime(2026, 8, 27, 10, 5, tzinfo=timezone(timedelta(hours=8)))
        return (base + timedelta(seconds=seconds)).isoformat()

    def test_valid_trace_bundle_binds_both_channels_to_same_event(self):
        recorder = LocalStepRawTraceRecorder(self.plan())
        for seconds in (-60, -50, -40, -30, -20, -10, 0, 10, 20, 30, 40):
            recorder.record(
                timestamp=self.time(seconds),
                outlet_so2=10.0 if seconds <= 0 else 10.0 - 0.02 * seconds,
                ph=6.2 if seconds <= 0 else 6.2 + 0.001 * seconds,
                condition_snapshot_version="v001",
                mfac_context_id="CTX",
                data_quality_ok=True,
            )
        recorder.mark_actual_flow_reached(
            tracking_event_id="TRACK-1",
            actual_flow_reached_time=self.time(0),
        )
        bundle = recorder.finalize(event_id="MFAC-LOCAL-GAIN-TRIAL-1")
        self.assertEqual(bundle.status, "TRACE_REVIEW_CANDIDATE")
        self.assertEqual(bundle.so2_trace.event_id, bundle.ph_trace.event_id)
        self.assertEqual(bundle.so2_trace.actual_flow_reached_time, self.time(0))
        self.assertFalse(bundle.learning_enabled)
        self.assertFalse(bundle.residual_control_enabled)
        self.assertFalse(bundle.dcs_write_enabled)

    def test_context_change_marks_bundle_invalid(self):
        recorder = LocalStepRawTraceRecorder(self.plan())
        recorder.record(
            timestamp=self.time(-10),
            outlet_so2=10.0,
            ph=6.2,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
        )
        recorder.record(
            timestamp=self.time(0),
            outlet_so2=10.0,
            ph=6.2,
            condition_snapshot_version="v001",
            mfac_context_id="OTHER",
        )
        recorder.record(
            timestamp=self.time(10),
            outlet_so2=9.8,
            ph=6.22,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
        )
        recorder.mark_actual_flow_reached(
            tracking_event_id="TRACK-1",
            actual_flow_reached_time=self.time(0),
        )
        bundle = recorder.finalize(event_id="MFAC-LOCAL-GAIN-TRIAL-1")
        self.assertEqual(bundle.status, "INVALID_TRACE")
        self.assertIn("MFAC_CONTEXT_CHANGED", bundle.reasons)

    def test_reached_time_is_required(self):
        recorder = LocalStepRawTraceRecorder(self.plan())
        recorder.record(
            timestamp=self.time(0),
            outlet_so2=10.0,
            ph=6.2,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
        )
        bundle = recorder.finalize(event_id="MFAC-LOCAL-GAIN-TRIAL-1")
        self.assertEqual(bundle.status, "INVALID_TRACE")
        self.assertIn("ACTUAL_FLOW_REACHED_TIME_REQUIRED", bundle.reasons)

    def test_recorder_has_no_execution_api(self):
        recorder = LocalStepRawTraceRecorder(self.plan())
        self.assertFalse(hasattr(recorder, "execute"))
        self.assertFalse(recorder.automatic_execution_allowed)
        self.assertFalse(recorder.dcs_write_enabled)


if __name__ == "__main__":
    unittest.main()
