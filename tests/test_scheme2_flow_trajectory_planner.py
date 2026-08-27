import inspect
import unittest

from system.model.map_control.mfac_model.continuous_target import ContinuousTargetConfig
from system.model.map_control.mfac_model.flow_trajectory_planner import (
    FlowTrajectoryPlanner,
    FlowTrajectoryPlannerConfig,
)
from system.model.map_control.mfac_model.pending_dose_guard import PendingDoseGuardDecision


class FlowTrajectoryPlannerTest(unittest.TestCase):
    @staticmethod
    def planner():
        return FlowTrajectoryPlanner(
            FlowTrajectoryPlannerConfig(
                max_step_up=5.0,
                max_step_down=7.0,
                min_hold_seconds=300.0,
                demand_deadband=0.1,
            ),
            ContinuousTargetConfig(
                hard_min_supply_flow=0.0,
                hard_max_supply_flow=70.0,
            ),
        )

    @staticmethod
    def pending(status="CLEAR"):
        return PendingDoseGuardDecision(
            status=status,
            current_ph=6.2,
            current_actual_flow=30.0,
            phi_ph_live=0.01,
            confidence_ph_live=1.0,
            pending_equivalent_delta_q=0.0,
            pending_delta_ph=0.0,
            predicted_ph_after_pending=6.2,
            recent_slurry_volume_m3=1.0,
            active_contribution_count=0,
        )

    def test_planner_initializes_from_current_algorithm_target(self):
        planner = self.planner()
        result = planner.plan(
            timestamp="2026-08-01T10:00:00",
            raw_demand=30.0,
            raw_demand_valid=True,
            current_algorithm_target=30.0,
            pending_response=self.pending(),
        )
        self.assertEqual(result.status, "INITIALIZED")
        self.assertEqual(result.planned_target, 30.0)
        self.assertTrue(result.shadow_only)
        self.assertFalse(result.metadata["algorithm_target_replaced"])

    def test_minimum_hold_prevents_ten_second_accumulation(self):
        planner = self.planner()
        planner.plan(
            timestamp="2026-08-01T10:00:00",
            raw_demand=30.0,
            raw_demand_valid=True,
            current_algorithm_target=30.0,
            pending_response=self.pending(),
        )
        result = planner.plan(
            timestamp="2026-08-01T10:01:00",
            raw_demand=45.0,
            raw_demand_valid=True,
            current_algorithm_target=45.0,
            pending_response=self.pending(),
        )
        self.assertEqual(result.status, "HOLD_MIN_DURATION")
        self.assertEqual(result.planned_target, 30.0)
        self.assertGreater(result.hold_remaining_seconds, 0.0)

    def test_after_hold_only_one_bounded_step_is_proposed(self):
        planner = self.planner()
        planner.plan(
            timestamp="2026-08-01T10:00:00",
            raw_demand=30.0,
            raw_demand_valid=True,
            current_algorithm_target=30.0,
            pending_response=self.pending(),
        )
        result = planner.plan(
            timestamp="2026-08-01T10:05:01",
            raw_demand=45.0,
            raw_demand_valid=True,
            current_algorithm_target=45.0,
            pending_response=self.pending(),
        )
        self.assertEqual(result.status, "STEP_UP")
        self.assertEqual(result.planned_delta_q, 5.0)
        self.assertEqual(result.planned_target, 35.0)

    def test_pending_high_ph_risk_holds_additional_positive_step(self):
        planner = self.planner()
        planner.plan(
            timestamp="2026-08-01T10:00:00",
            raw_demand=30.0,
            raw_demand_valid=True,
            current_algorithm_target=30.0,
            pending_response=self.pending(),
        )
        result = planner.plan(
            timestamp="2026-08-01T10:06:00",
            raw_demand=45.0,
            raw_demand_valid=True,
            current_algorithm_target=45.0,
            pending_response=self.pending("WATCH_HIGH"),
        )
        self.assertEqual(result.status, "HOLD_PENDING_PH")
        self.assertEqual(result.planned_target, 30.0)

    def test_actual_flow_is_not_a_planner_target_fallback_api(self):
        parameters = inspect.signature(FlowTrajectoryPlanner.plan).parameters
        self.assertNotIn("actual_supply_flow_feedback", parameters)
        self.assertNotIn("actual_flow", parameters)


if __name__ == "__main__":
    unittest.main()
