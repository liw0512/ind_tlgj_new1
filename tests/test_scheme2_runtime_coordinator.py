import tempfile
import unittest

from system.model.map_control.mfac_model.continuous_target import (
    COUNTERFACTUAL_SHADOW,
    ONLINE_SHADOW,
)
from system.model.map_control.mfac_model.mfac_schema import MFACRuntimeState
from system.model.map_control.mfac_model.online_adaptation import (
    MFACOnlineAdaptationConfig,
)
from system.model.map_control.mfac_model.process_response import (
    ProcessResponseConfig,
)
from system.model.map_control.mfac_model.residual_control import (
    MFACResidualConfig,
)
from system.model.map_control.mfac_model.runtime_coordinator import (
    Scheme2RuntimeCoordinator,
    Scheme2RuntimeCoordinatorConfig,
)
from system.model.map_control.mfac_model.runtime_store import Scheme2RuntimeStore
from system.model.map_control.mfac_model.supply_flow_tracking import (
    SupplyFlowTrackingConfig,
)


class Scheme2RuntimeCoordinatorTest(unittest.TestCase):
    @staticmethod
    def config(
        *,
        learning=False,
        residual=False,
        tracking_gap=15.0,
        tracking_timeout=25.0,
    ):
        return Scheme2RuntimeCoordinatorConfig(
            tracking=SupplyFlowTrackingConfig(
                target_change_deadband=0.5,
                reach_tolerance=0.1,
                required_sustain_seconds=10.0,
                execution_timeout_seconds=tracking_timeout,
                max_sample_gap_seconds=tracking_gap,
            ),
            response=ProcessResponseConfig(
                baseline_window_seconds=30.0,
                delay_onset_seconds=10.0,
                observation_seconds=20.0,
                measurement_window_seconds=10.0,
                max_sample_gap_seconds=15.0,
                target_change_tolerance=0.0,
                min_baseline_samples=2,
                min_response_samples=2,
            ),
            online_adaptation=MFACOnlineAdaptationConfig(
                eta=0.2,
                mu=1.0,
                phi_lower_bound=-10.0,
                phi_upper_bound=-0.1,
                max_single_update_abs=1.0,
            ),
            residual=MFACResidualConfig(
                rho=1.0,
                lambda_regularization=1.0,
                max_abs_residual=5.0,
                min_confidence=0.5,
            ),
            learning_enabled=learning,
            residual_control_enabled=residual,
        )

    @staticmethod
    def state():
        return MFACRuntimeState(
            condition_snapshot_version="v001",
            mfac_context_id="MFAC-BASE-17",
            phi_live=-4.0,
            confidence_live=0.9,
        )

    @staticmethod
    def cycle(coordinator, timestamp, **overrides):
        values = {
            "qbase_effective": 30.0,
            "qbase_inputs_valid": True,
            "outlet_so2": 50.0,
            "so2_target": 35.0,
            "condition_snapshot_version": "v001",
            "mfac_context_id": "MFAC-BASE-17",
            "condition_label": "17",
            "base_condition_id": "17",
            "grid_id": "P1-S1",
            "policy_region_id": "R_P1_S1",
            "inlet_so2": 1000.0,
            "ph": 6.2,
            "actual_supply_flow_feedback": 30.0,
            "target_was_applied": False,
            "dcs_applied_target_supply_flow": None,
            "replay_semantics": ONLINE_SHADOW,
            "fast_active": False,
            "data_quality_ok": True,
        }
        values.update(overrides)
        return coordinator.process_cycle(timestamp=timestamp, **values)

    def build(self, root, **config_overrides):
        return Scheme2RuntimeCoordinator(
            self.config(**config_overrides),
            Scheme2RuntimeStore(root),
            runtime_state=self.state(),
        )

    def test_shadow_safety_keeps_learn_residual_and_dcs_write_off(self):
        with tempfile.TemporaryDirectory() as root:
            coordinator = self.build(root)
            result = self.cycle(
                coordinator,
                "2026-08-26T10:00:00+08:00",
                qbase_effective=31.0,
                actual_supply_flow_feedback=69.0,
            )

            self.assertEqual(result.algorithm_target.algorithm_target_supply_flow, 31.0)
            self.assertFalse(result.learning_enabled)
            self.assertFalse(result.residual_control_enabled)
            self.assertFalse(result.dcs_write_enabled)
            self.assertEqual(result.residual_hold.held_residual, 0.0)
            self.assertFalse(
                result.metadata["actual_flow_used_as_algorithm_target"]
            )

    def test_counterfactual_replay_never_starts_causal_response(self):
        with tempfile.TemporaryDirectory() as root:
            coordinator = self.build(root)
            result = self.cycle(
                coordinator,
                "2026-08-26T10:00:00+08:00",
                qbase_effective=31.0,
                actual_supply_flow_feedback=69.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=31.0,
                replay_semantics=COUNTERFACTUAL_SHADOW,
            )

            self.assertEqual(
                [event.status for event in result.tracking_events],
                ["COUNTERFACTUAL_SHADOW"],
            )
            self.assertEqual(result.response_events, [])
            self.assertEqual(result.active_response_tracking_event_id, "")

    def test_full_event_chain_anchors_on_reach_and_holds_residual(self):
        with tempfile.TemporaryDirectory() as root:
            coordinator = self.build(root, learning=True, residual=True)

            self.cycle(
                coordinator,
                "2026-08-26T10:00:00+08:00",
                qbase_inputs_valid=False,
            )
            self.cycle(
                coordinator,
                "2026-08-26T10:00:10+08:00",
                qbase_inputs_valid=False,
            )
            self.cycle(
                coordinator,
                "2026-08-26T10:00:20+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=30.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=32.0,
            )
            self.cycle(
                coordinator,
                "2026-08-26T10:00:30+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=32.0,
            )
            reached = self.cycle(
                coordinator,
                "2026-08-26T10:00:40+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=32.0,
            )
            self.assertEqual(reached.tracking_events[0].status, "REACHED")
            self.assertEqual(
                reached.tracking_events[0].actual_flow_reached_time,
                "2026-08-26T10:00:40+08:00",
            )
            self.assertEqual(reached.residual_hold.held_residual, 0.0)

            waiting_one = self.cycle(
                coordinator,
                "2026-08-26T10:00:50+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
                outlet_so2=48.0,
            )
            waiting_two = self.cycle(
                coordinator,
                "2026-08-26T10:01:00+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
                outlet_so2=42.0,
            )
            self.assertEqual(waiting_one.residual_hold.held_residual, 0.0)
            self.assertEqual(waiting_two.residual_hold.held_residual, 0.0)
            self.assertEqual(
                waiting_two.residual_hold.status,
                "HOLD_WAITING_RESPONSE",
            )

            completed = self.cycle(
                coordinator,
                "2026-08-26T10:01:10+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
                outlet_so2=40.0,
            )
            self.assertEqual(completed.response_events[0].status, "COMPLETED")
            self.assertEqual(
                completed.response_events[0].actual_flow_reached_time,
                "2026-08-26T10:00:40+08:00",
            )
            self.assertEqual(
                completed.response_events[0].response_start_time,
                "2026-08-26T10:00:50+08:00",
            )
            self.assertEqual(completed.action_response_events[0].delta_q_actual, 2.0)
            self.assertTrue(completed.action_response_events[0].learning_eligible)
            self.assertTrue(completed.adaptation_results[0].updated)
            self.assertEqual(completed.residual_hold.status, "UPDATED")
            self.assertGreater(completed.residual_hold.held_residual, 0.0)

            next_cycle = self.cycle(
                coordinator,
                "2026-08-26T10:01:20+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
            )
            repeated = self.cycle(
                coordinator,
                "2026-08-26T10:01:30+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
            )
            self.assertEqual(
                next_cycle.algorithm_target.algorithm_target_supply_flow,
                repeated.algorithm_target.algorithm_target_supply_flow,
            )
            self.assertEqual(
                next_cycle.residual_hold.held_residual,
                repeated.residual_hold.held_residual,
            )

    def test_tracking_terminal_statuses_are_visible_at_coordinator_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            superseded = self.build(root + "/superseded")
            self.cycle(
                superseded,
                "2026-08-26T10:00:00+08:00",
                qbase_effective=30.0,
                actual_supply_flow_feedback=20.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=30.0,
            )
            changed = self.cycle(
                superseded,
                "2026-08-26T10:00:05+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=20.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=32.0,
            )
            self.assertIn("SUPERSEDED", [e.status for e in changed.tracking_events])

            timeout = self.build(
                root + "/timeout",
                tracking_gap=60.0,
                tracking_timeout=25.0,
            )
            self.cycle(
                timeout,
                "2026-08-26T10:00:00+08:00",
                actual_supply_flow_feedback=20.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=30.0,
            )
            timed_out = self.cycle(
                timeout,
                "2026-08-26T10:00:26+08:00",
                actual_supply_flow_feedback=20.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=30.0,
            )
            self.assertEqual(timed_out.tracking_events[0].status, "TIMEOUT")

            sample_gap = self.build(root + "/gap")
            self.cycle(
                sample_gap,
                "2026-08-26T10:00:00+08:00",
                actual_supply_flow_feedback=20.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=30.0,
            )
            gap = self.cycle(
                sample_gap,
                "2026-08-26T10:00:20+08:00",
                actual_supply_flow_feedback=20.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=30.0,
            )
            self.assertEqual(gap.tracking_events[0].status, "SAMPLE_GAP")

            missing = self.build(root + "/missing")
            self.cycle(
                missing,
                "2026-08-26T10:00:00+08:00",
                actual_supply_flow_feedback=20.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=30.0,
            )
            no_feedback = self.cycle(
                missing,
                "2026-08-26T10:00:05+08:00",
                actual_supply_flow_feedback=None,
                target_was_applied=True,
                dcs_applied_target_supply_flow=30.0,
            )
            self.assertEqual(
                no_feedback.tracking_events[0].status,
                "FEEDBACK_MISSING",
            )

    def test_persisted_target_holds_without_actual_flow_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.build(root)
            self.cycle(
                first,
                "2026-08-26T10:00:00+08:00",
                qbase_effective=33.0,
                actual_supply_flow_feedback=69.0,
            )

            restored = self.build(root)
            result = self.cycle(
                restored,
                "2026-08-26T10:00:10+08:00",
                qbase_effective=None,
                qbase_inputs_valid=False,
                actual_supply_flow_feedback=12.0,
            )
            self.assertEqual(
                result.algorithm_target.algorithm_target_supply_flow,
                33.0,
            )
            self.assertEqual(
                result.algorithm_target.algorithm_target_status,
                "HOLD_LAST_INVALID_INPUT",
            )


if __name__ == "__main__":
    unittest.main()
