import unittest

from system.model.map_control.mfac_model.continuous_target import (
    COUNTERFACTUAL_SHADOW,
    ONLINE_SHADOW,
)
from system.model.map_control.mfac_model.process_response import (
    ProcessResponseConfig,
    ProcessResponseMonitor,
)
from system.model.map_control.mfac_model.supply_flow_tracking import (
    SupplyFlowTrackingEvent,
)


class ProcessResponseMonitorTest(unittest.TestCase):
    @staticmethod
    def config():
        return ProcessResponseConfig(
            baseline_window_seconds=30.0,
            delay_onset_seconds=10.0,
            observation_seconds=20.0,
            measurement_window_seconds=10.0,
            max_sample_gap_seconds=15.0,
            target_change_tolerance=0.0,
            min_baseline_samples=2,
            min_response_samples=2,
        )

    @staticmethod
    def reached_event(replay_semantics=ONLINE_SHADOW):
        return SupplyFlowTrackingEvent(
            tracking_event_id="S2-FLOW-00000001",
            algorithm_target_supply_flow=32.0,
            target_change_time="2026-08-26T10:00:00+08:00",
            status="REACHED",
            dcs_applied_target_supply_flow=32.0,
            actual_supply_flow_before=30.0,
            actual_supply_flow_feedback=32.0,
            target_actual_gap=0.0,
            actual_flow_reached_time="2026-08-26T10:00:20+08:00",
            terminal_time="2026-08-26T10:00:20+08:00",
            target_was_applied=True,
            replay_semantics=replay_semantics,
            metadata={"execution_delay_seconds": 20.0},
        )

    @staticmethod
    def observe(
        monitor,
        timestamp,
        outlet_so2,
        *,
        reached_event=None,
        inlet_so2=1000.0,
        qbase=30.0,
        ph=6.2,
        target=35.0,
        actual_flow=32.0,
        snapshot="v001",
        context="MFAC-BASE-17",
        fast=False,
        quality=True,
    ):
        return monitor.update(
            timestamp=timestamp,
            outlet_so2=outlet_so2,
            inlet_so2=inlet_so2,
            qbase_effective=qbase,
            ph=ph,
            so2_target=target,
            actual_supply_flow_feedback=actual_flow,
            condition_snapshot_version=snapshot,
            mfac_context_id=context,
            fast_active=fast,
            data_quality_ok=quality,
            reached_event=reached_event,
        )

    def seed_baseline(self, monitor):
        self.observe(monitor, "2026-08-26T10:00:00+08:00", 50.0, actual_flow=30.0)
        self.observe(monitor, "2026-08-26T10:00:10+08:00", 50.0, actual_flow=31.0)

    def test_completed_response_uses_actual_delta_q_and_so2_response(self):
        monitor = ProcessResponseMonitor(self.config())
        self.seed_baseline(monitor)
        started = self.observe(
            monitor,
            "2026-08-26T10:00:20+08:00",
            50.0,
            reached_event=self.reached_event(),
            actual_flow=32.0,
        )
        self.assertEqual(started.emitted_events, [])
        self.assertEqual(started.active_tracking_event_id, "S2-FLOW-00000001")

        self.observe(monitor, "2026-08-26T10:00:30+08:00", 48.0, qbase=30.1)
        self.observe(monitor, "2026-08-26T10:00:40+08:00", 42.0, qbase=30.2)
        completed = self.observe(
            monitor,
            "2026-08-26T10:00:50+08:00",
            40.0,
            qbase=30.2,
        )

        self.assertEqual(completed.active_tracking_event_id, "")
        self.assertEqual(len(completed.emitted_events), 1)
        event = completed.emitted_events[0]
        self.assertEqual(event.status, "COMPLETED")
        self.assertEqual(event.q_before, 30.0)
        self.assertEqual(event.q_after, 32.0)
        self.assertEqual(event.delta_q_actual, 2.0)
        self.assertEqual(event.so2_before, 50.0)
        self.assertEqual(event.so2_after, 41.0)
        self.assertEqual(event.delta_so2, -9.0)
        self.assertAlmostEqual(event.qbase_before, 30.0)
        self.assertAlmostEqual(event.qbase_after, 30.2)
        self.assertAlmostEqual(event.qbase_drift, 0.2)
        self.assertEqual(event.metadata["execution_delay_seconds"], 20.0)

    def test_fast_overlap_censors_response(self):
        monitor = ProcessResponseMonitor(self.config())
        self.seed_baseline(monitor)
        self.observe(
            monitor,
            "2026-08-26T10:00:20+08:00",
            50.0,
            reached_event=self.reached_event(),
        )
        result = self.observe(
            monitor,
            "2026-08-26T10:00:30+08:00",
            48.0,
            fast=True,
        )

        event = result.emitted_events[0]
        self.assertEqual(event.status, "CENSORED")
        self.assertEqual(event.censor_reason, "FAST_OVERLAP")
        self.assertTrue(event.fast_overlap)

    def test_context_change_censors_response(self):
        monitor = ProcessResponseMonitor(self.config())
        self.seed_baseline(monitor)
        self.observe(
            monitor,
            "2026-08-26T10:00:20+08:00",
            50.0,
            reached_event=self.reached_event(),
        )
        result = self.observe(
            monitor,
            "2026-08-26T10:00:30+08:00",
            48.0,
            context="MFAC-BASE-18",
        )

        event = result.emitted_events[0]
        self.assertEqual(event.status, "CENSORED")
        self.assertEqual(event.censor_reason, "MFAC_CONTEXT_CHANGED")
        self.assertTrue(event.condition_changed)

    def test_target_change_censors_response(self):
        monitor = ProcessResponseMonitor(self.config())
        self.seed_baseline(monitor)
        self.observe(
            monitor,
            "2026-08-26T10:00:20+08:00",
            50.0,
            reached_event=self.reached_event(),
        )
        result = self.observe(
            monitor,
            "2026-08-26T10:00:30+08:00",
            48.0,
            target=34.0,
        )

        event = result.emitted_events[0]
        self.assertEqual(event.status, "CENSORED")
        self.assertEqual(event.censor_reason, "SO2_TARGET_CHANGED")
        self.assertTrue(event.target_changed)

    def test_sample_gap_censors_response(self):
        monitor = ProcessResponseMonitor(self.config())
        self.seed_baseline(monitor)
        self.observe(
            monitor,
            "2026-08-26T10:00:20+08:00",
            50.0,
            reached_event=self.reached_event(),
        )
        result = self.observe(
            monitor,
            "2026-08-26T10:00:40+08:00",
            42.0,
        )

        event = result.emitted_events[0]
        self.assertEqual(event.status, "CENSORED")
        self.assertEqual(event.censor_reason, "SAMPLE_GAP")

    def test_insufficient_baseline_is_explicit(self):
        monitor = ProcessResponseMonitor(self.config())
        result = self.observe(
            monitor,
            "2026-08-26T10:00:20+08:00",
            50.0,
            reached_event=self.reached_event(),
        )

        self.assertEqual(result.active_tracking_event_id, "")
        event = result.emitted_events[0]
        self.assertEqual(event.status, "INSUFFICIENT_BASELINE")
        self.assertEqual(event.censor_reason, "INSUFFICIENT_BASELINE_SAMPLES")

    def test_non_reached_tracking_event_is_rejected(self):
        monitor = ProcessResponseMonitor(self.config())
        event = self.reached_event()
        event.status = "PENDING"
        with self.assertRaises(ValueError):
            self.observe(
                monitor,
                "2026-08-26T10:00:20+08:00",
                50.0,
                reached_event=event,
            )

    def test_counterfactual_history_cannot_start_causal_response(self):
        monitor = ProcessResponseMonitor(self.config())
        with self.assertRaises(ValueError):
            self.observe(
                monitor,
                "2026-08-26T10:00:20+08:00",
                50.0,
                reached_event=self.reached_event(COUNTERFACTUAL_SHADOW),
            )

    def test_response_window_parameters_have_no_defaults(self):
        with self.assertRaises(TypeError):
            ProcessResponseConfig()


if __name__ == "__main__":
    unittest.main()
