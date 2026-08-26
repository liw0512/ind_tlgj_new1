import unittest

from system.model.map_control.mfac_model.continuous_target import (
    COUNTERFACTUAL_SHADOW,
    ONLINE_SHADOW,
)
from system.model.map_control.mfac_model.supply_flow_tracking import (
    SupplyFlowTrackingConfig,
    SupplyFlowTrackingMonitor,
)


class SupplyFlowTrackingMonitorTest(unittest.TestCase):
    @staticmethod
    def monitor():
        return SupplyFlowTrackingMonitor(
            SupplyFlowTrackingConfig(
                target_change_deadband=0.5,
                reach_tolerance=0.2,
                required_sustain_seconds=20.0,
                execution_timeout_seconds=60.0,
                max_sample_gap_seconds=30.0,
            )
        )

    def test_counterfactual_shadow_never_creates_causal_tracking(self):
        monitor = self.monitor()
        result = monitor.update(
            timestamp="2026-08-26T10:00:00+08:00",
            algorithm_target_supply_flow=46.0,
            algorithm_target_valid=True,
            target_was_applied=False,
            actual_supply_flow_feedback=69.0,
            replay_semantics=COUNTERFACTUAL_SHADOW,
        )

        self.assertIsNone(result.active_event)
        self.assertEqual(len(result.emitted_events), 1)
        event = result.emitted_events[0]
        self.assertEqual(event.status, "COUNTERFACTUAL_SHADOW")
        self.assertFalse(event.target_was_applied)
        self.assertEqual(event.algorithm_target_supply_flow, 46.0)
        self.assertEqual(event.actual_supply_flow_feedback, 69.0)

    def test_causal_tracking_requires_applied_target(self):
        monitor = self.monitor()
        result = monitor.update(
            timestamp="2026-08-26T10:00:00+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=False,
            dcs_applied_target_supply_flow=None,
            actual_supply_flow_feedback=39.0,
        )

        self.assertIsNone(result.active_event)
        self.assertEqual(result.emitted_events[0].status, "NOT_APPLIED")

    def test_actual_reach_requires_sustained_tolerance(self):
        monitor = self.monitor()
        first = monitor.update(
            timestamp="2026-08-26T10:00:00+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=39.5,
            actual_supply_flow_feedback=38.0,
        )
        self.assertIsNotNone(first.active_event)
        self.assertEqual(first.active_event.status, "PENDING")

        second = monitor.update(
            timestamp="2026-08-26T10:00:10+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=39.5,
            actual_supply_flow_feedback=39.4,
        )
        self.assertIsNotNone(second.active_event)

        third = monitor.update(
            timestamp="2026-08-26T10:00:30+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=39.5,
            actual_supply_flow_feedback=39.6,
        )

        self.assertIsNone(third.active_event)
        reached = third.emitted_events[-1]
        self.assertEqual(reached.status, "REACHED")
        self.assertEqual(
            reached.actual_flow_reached_time,
            "2026-08-26T10:00:30+08:00",
        )

    def test_reach_is_based_on_dcs_applied_target_not_algorithm_target(self):
        monitor = SupplyFlowTrackingMonitor(
            SupplyFlowTrackingConfig(
                target_change_deadband=0.5,
                reach_tolerance=0.1,
                required_sustain_seconds=0.0,
                execution_timeout_seconds=60.0,
                max_sample_gap_seconds=30.0,
            )
        )
        result = monitor.update(
            timestamp="2026-08-26T10:00:00+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=39.5,
            actual_supply_flow_feedback=39.5,
        )

        self.assertIsNone(result.active_event)
        reached = result.emitted_events[-1]
        self.assertEqual(reached.status, "REACHED")
        self.assertEqual(reached.metadata["dcs_applied_actual_error"], 0.0)
        self.assertEqual(reached.target_actual_gap, 0.5)

    def test_new_material_target_supersedes_pending_event(self):
        monitor = self.monitor()
        first = monitor.update(
            timestamp="2026-08-26T10:00:00+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=40.0,
            actual_supply_flow_feedback=35.0,
        )
        old_id = first.active_event.tracking_event_id

        second = monitor.update(
            timestamp="2026-08-26T10:00:10+08:00",
            algorithm_target_supply_flow=42.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=42.0,
            actual_supply_flow_feedback=36.0,
        )

        self.assertEqual(second.emitted_events[0].tracking_event_id, old_id)
        self.assertEqual(second.emitted_events[0].status, "SUPERSEDED")
        self.assertIsNotNone(second.active_event)
        self.assertEqual(second.active_event.algorithm_target_supply_flow, 42.0)

    def test_small_target_motion_inside_deadband_does_not_restart_tracking(self):
        monitor = self.monitor()
        first = monitor.update(
            timestamp="2026-08-26T10:00:00+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=40.0,
            actual_supply_flow_feedback=35.0,
        )
        event_id = first.active_event.tracking_event_id

        second = monitor.update(
            timestamp="2026-08-26T10:00:10+08:00",
            algorithm_target_supply_flow=40.4,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=40.4,
            actual_supply_flow_feedback=36.0,
        )

        self.assertEqual(second.emitted_events, [])
        self.assertEqual(second.active_event.tracking_event_id, event_id)

    def test_execution_timeout_is_terminal(self):
        monitor = self.monitor()
        monitor.update(
            timestamp="2026-08-26T10:00:00+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=40.0,
            actual_supply_flow_feedback=35.0,
        )
        result = monitor.update(
            timestamp="2026-08-26T10:01:00+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=40.0,
            actual_supply_flow_feedback=36.0,
        )

        self.assertIsNone(result.active_event)
        self.assertEqual(result.emitted_events[-1].status, "SAMPLE_GAP")

    def test_execution_timeout_when_sampling_remains_continuous(self):
        monitor = self.monitor()
        monitor.update(
            timestamp="2026-08-26T10:00:00+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=40.0,
            actual_supply_flow_feedback=35.0,
        )
        monitor.update(
            timestamp="2026-08-26T10:00:30+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=40.0,
            actual_supply_flow_feedback=35.5,
        )
        result = monitor.update(
            timestamp="2026-08-26T10:01:01+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=40.0,
            actual_supply_flow_feedback=36.0,
        )

        self.assertIsNone(result.active_event)
        self.assertEqual(result.emitted_events[-1].status, "SAMPLE_GAP")

    def test_timeout_can_be_observed_before_gap_limit(self):
        monitor = SupplyFlowTrackingMonitor(
            SupplyFlowTrackingConfig(
                target_change_deadband=0.5,
                reach_tolerance=0.2,
                required_sustain_seconds=20.0,
                execution_timeout_seconds=20.0,
                max_sample_gap_seconds=30.0,
            )
        )
        monitor.update(
            timestamp="2026-08-26T10:00:00+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=40.0,
            actual_supply_flow_feedback=35.0,
        )
        result = monitor.update(
            timestamp="2026-08-26T10:00:21+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=40.0,
            actual_supply_flow_feedback=36.0,
        )

        self.assertIsNone(result.active_event)
        self.assertEqual(result.emitted_events[-1].status, "TIMEOUT")

    def test_missing_feedback_is_explicit_terminal_status(self):
        monitor = self.monitor()
        result = monitor.update(
            timestamp="2026-08-26T10:00:00+08:00",
            algorithm_target_supply_flow=40.0,
            algorithm_target_valid=True,
            target_was_applied=True,
            dcs_applied_target_supply_flow=40.0,
            actual_supply_flow_feedback=None,
        )

        self.assertIsNone(result.active_event)
        self.assertEqual(result.emitted_events[-1].status, "FEEDBACK_MISSING")

    def test_parameters_have_no_uncalibrated_defaults(self):
        with self.assertRaises(TypeError):
            SupplyFlowTrackingConfig()


if __name__ == "__main__":
    unittest.main()
