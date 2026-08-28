import tempfile
import unittest

from system.model.map_control.mfac_model.flow_trajectory_planner import (
    FlowTrajectoryPlannerConfig,
)
from system.model.map_control.mfac_model.mfac_schema import MFACRuntimeState
from system.model.map_control.mfac_model.online_adaptation import MFACOnlineAdaptationConfig
from system.model.map_control.mfac_model.pending_dose_guard import PendingDoseGuardConfig
from system.model.map_control.mfac_model.ph_adaptation import PHOnlineAdaptationConfig
from system.model.map_control.mfac_model.ph_arbitration import PHResidualArbitrationConfig
from system.model.map_control.mfac_model.ph_response import PHResponseConfig
from system.model.map_control.mfac_model.process_response import ProcessResponseConfig
from system.model.map_control.mfac_model.residual_control import MFACResidualConfig
from system.model.map_control.mfac_model.runtime_coordinator import (
    Scheme2RuntimeCoordinator,
    Scheme2RuntimeCoordinatorConfig,
)
from system.model.map_control.mfac_model.runtime_store import Scheme2RuntimeStore
from system.model.map_control.mfac_model.supply_flow_tracking import SupplyFlowTrackingConfig
from system.model.map_control.mfac_model.trajectory_coordinator import (
    Scheme2TrajectoryShadowCoordinator,
)


class Scheme2PendingIncrementalRuntimeTest(unittest.TestCase):
    @staticmethod
    def state(
        snapshot="v1",
        context="MFAC-COND-C1",
        *,
        grid_id=None,
    ):
        metadata = {}
        if grid_id is not None:
            metadata["runtime_grid_id"] = grid_id
        return MFACRuntimeState(
            condition_snapshot_version=snapshot,
            mfac_context_id=context,
            phi_live=-1.0,
            confidence_live=0.9,
            valid_event_count=7,
            last_event_id="SO2-7",
            phi_ph_live=0.1,
            confidence_ph_live=0.9,
            ph_valid_event_count=5,
            ph_last_event_id="PH-5",
            metadata=metadata,
        )

    @staticmethod
    def config(*, persist_runtime=False):
        return Scheme2RuntimeCoordinatorConfig(
            tracking=SupplyFlowTrackingConfig(
                target_change_deadband=0.5,
                reach_tolerance=0.2,
                required_sustain_seconds=10.0,
                execution_timeout_seconds=60.0,
                max_sample_gap_seconds=15.0,
            ),
            response=ProcessResponseConfig(
                baseline_window_seconds=20.0,
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
                max_abs_residual=10.0,
                min_confidence=0.5,
            ),
            ph_response=PHResponseConfig(
                baseline_window_seconds=20.0,
                delay_onset_seconds=5.0,
                observation_seconds=15.0,
                measurement_window_seconds=5.0,
                max_sample_gap_seconds=15.0,
                target_change_tolerance=0.0,
                min_baseline_samples=2,
                min_response_samples=2,
            ),
            ph_online_adaptation=PHOnlineAdaptationConfig(
                eta=0.2,
                mu=1.0,
                phi_lower_bound=0.001,
                phi_upper_bound=1.0,
                max_single_update_abs=0.1,
            ),
            ph_arbitration=PHResidualArbitrationConfig(
                operating_min=6.0,
                operating_max=6.4,
                safe_min=5.6,
                safe_max=6.8,
                guard_band=0.15,
                min_confidence=0.5,
            ),
            learning_enabled=False,
            residual_control_enabled=True,
            persist_runtime=bool(persist_runtime),
        )

    @staticmethod
    def cycle(
        coordinator,
        timestamp,
        *,
        ph,
        actual_flow,
        outlet_so2=28.0,
        snapshot="v1",
        context="MFAC-COND-C1",
        condition_label="C1",
        grid_id="P10-S1",
    ):
        return coordinator.process_cycle(
            timestamp=timestamp,
            qbase_effective=30.0,
            qbase_inputs_valid=True,
            outlet_so2=outlet_so2,
            so2_target=20.0,
            condition_snapshot_version=snapshot,
            mfac_context_id=context,
            condition_label=condition_label,
            base_condition_id="B1",
            grid_id=grid_id,
            inlet_so2=1500.0,
            ph=ph,
            actual_supply_flow_feedback=actual_flow,
            target_was_applied=False,
            dcs_applied_target_supply_flow=None,
            fast_active=False,
            data_quality_ok=True,
        )

    @staticmethod
    def trajectory(
        coordinator_config,
        root,
        *,
        initial_hold,
        runtime_state=None,
        runtime_store=None,
    ):
        return Scheme2TrajectoryShadowCoordinator(
            coordinator_config,
            runtime_store or Scheme2RuntimeStore(root, enabled=False),
            runtime_state=(
                Scheme2PendingIncrementalRuntimeTest.state()
                if runtime_state is None
                else runtime_state
            ),
            initial_residual_mfac_hold=initial_hold,
            pending_dose_config=PendingDoseGuardConfig(
                flow_change_deadband=0.5,
                response_onset_seconds=0.0,
                response_peak_seconds=100.0,
                max_sample_gap_seconds=15.0,
                min_confidence=0.5,
            ),
            trajectory_planner_config=FlowTrajectoryPlannerConfig(
                max_step_up=2.0,
                max_step_down=2.0,
                min_hold_seconds=20.0,
                demand_deadband=0.1,
            ),
        )

    @staticmethod
    def empty_trajectory(coordinator_config, root, runtime_store):
        return Scheme2TrajectoryShadowCoordinator(
            coordinator_config,
            runtime_store,
            runtime_state=None,
            initial_residual_mfac_hold=0.0,
            pending_dose_config=PendingDoseGuardConfig(
                flow_change_deadband=0.5,
                response_onset_seconds=0.0,
                response_peak_seconds=100.0,
                max_sample_gap_seconds=15.0,
                min_confidence=0.5,
            ),
            trajectory_planner_config=FlowTrajectoryPlannerConfig(
                max_step_up=2.0,
                max_step_down=2.0,
                min_hold_seconds=20.0,
                demand_deadband=0.1,
            ),
        )

    def test_base_runtime_arbitrates_candidate_minus_current_hold(self):
        with tempfile.TemporaryDirectory() as root:
            coordinator = Scheme2RuntimeCoordinator(
                self.config(),
                Scheme2RuntimeStore(root, enabled=False),
                runtime_state=self.state(),
                initial_residual_mfac_hold=3.0,
            )
            result = self.cycle(
                coordinator,
                "2026-08-28T12:00:00+08:00",
                ph=6.35,
                actual_flow=30.0,
            )
            self.assertAlmostEqual(result.residual_decision.candidate_residual, 4.0)
            self.assertEqual(result.ph_arbitration.status, "SCALE")
            self.assertAlmostEqual(result.ph_arbitration.held_residual, 3.0)
            self.assertAlmostEqual(result.ph_arbitration.requested_delta_residual, 1.0)
            self.assertAlmostEqual(result.ph_arbitration.final_residual, 3.5)
            self.assertEqual(
                result.metadata["ph_arbitration_context"]["pending_source"],
                "NONE",
            )
            self.assertAlmostEqual(result.residual_hold.held_residual, 3.0)

    def test_trajectory_gate_allows_one_safe_initial_residual_decision(self):
        with tempfile.TemporaryDirectory() as root:
            coordinator = self.trajectory(self.config(), root, initial_hold=0.0)
            result = self.cycle(
                coordinator,
                "2026-08-28T12:00:00+08:00",
                ph=6.20,
                actual_flow=30.0,
                outlet_so2=28.0,
            )
            # SO2 alone wants +4, but pH arbitration sees 6.2 + 0.1*4 = 6.6
            # and scales the first safe residual to +2 before the cadence gate.
            self.assertAlmostEqual(result.residual_decision.candidate_residual, 4.0)
            self.assertEqual(result.ph_arbitration.status, "SCALE")
            self.assertAlmostEqual(result.ph_arbitration.final_residual, 2.0)
            self.assertEqual(
                result.metadata["residual_decision_permission"]["status"],
                "ALLOW_INITIAL_DECISION",
            )
            self.assertAlmostEqual(result.residual_hold.held_residual, 2.0)
            self.assertTrue(coordinator.residual_decision_gate.awaiting_response)
            # Target for this cycle was calculated from the previous held value;
            # the new residual becomes the target input on the next cycle.
            self.assertAlmostEqual(result.algorithm_target.algorithm_target_supply_flow, 30.0)

    def test_pending_guard_future_ph_is_used_by_same_runtime_arbiter(self):
        with tempfile.TemporaryDirectory() as root:
            coordinator = self.trajectory(self.config(), root, initial_hold=3.0)

            # First cycle has no desired residual change, so the decision gate
            # remains free while PendingDoseGuard establishes its flow baseline.
            first = self.cycle(
                coordinator,
                "2026-08-28T12:00:00+08:00",
                ph=6.20,
                actual_flow=30.0,
                outlet_so2=26.0,
            )
            self.assertAlmostEqual(first.residual_decision.candidate_residual, 3.0)
            self.assertAlmostEqual(first.residual_hold.held_residual, 3.0)
            self.assertEqual(
                first.metadata["residual_decision_permission"]["status"],
                "NO_CHANGE_REQUIRED",
            )

            # Actual flow then moves +2.  SO2 alone wants residual 4, but the
            # still-pending pH response raises the future base to 6.4, so the
            # pH arbiter removes the +1 increment and leaves the held residual 3.
            second = self.cycle(
                coordinator,
                "2026-08-28T12:00:10+08:00",
                ph=6.20,
                actual_flow=32.0,
                outlet_so2=28.0,
            )
            pending = second.metadata["pending_dose_guard"]
            self.assertAlmostEqual(pending["pending_up_equivalent_delta_q"], 2.0)
            self.assertAlmostEqual(pending["predicted_ph_upper"], 6.4)
            self.assertEqual(
                second.metadata["ph_arbitration_context"]["pending_source"],
                "PENDING_DOSE_GUARD",
            )
            self.assertAlmostEqual(second.ph_arbitration.pending_base_ph, 6.4)
            self.assertEqual(second.ph_arbitration.status, "SCALE")
            self.assertAlmostEqual(second.ph_arbitration.residual_scale, 0.0)
            self.assertAlmostEqual(second.ph_arbitration.final_residual, 3.0)
            self.assertEqual(
                second.metadata["residual_decision_permission"]["status"],
                "NO_CHANGE_REQUIRED",
            )
            self.assertEqual(second.metadata["trajectory_plan"]["status"], "AT_DEMAND")

    def test_weekly_snapshot_handoff_keeps_online_phi_but_resets_residual(self):
        with tempfile.TemporaryDirectory() as root:
            store = Scheme2RuntimeStore(root)
            store.upsert_context(
                self.state(snapshot="v1", grid_id="P10-S1"),
                residual_mfac_hold=3.0,
            )
            store.save()
            coordinator = self.empty_trajectory(
                self.config(persist_runtime=False),
                root,
                Scheme2RuntimeStore(root, enabled=False),
            )

            result = self.cycle(
                coordinator,
                "2026-08-28T12:00:00+08:00",
                ph=6.20,
                actual_flow=30.0,
                outlet_so2=20.0,
                snapshot="v2",
                context="MFAC-COND-C1",
                condition_label="C1",
                grid_id="P10-S1",
            )

            self.assertEqual(
                result.metadata["context_status"],
                "CONTEXT_CROSS_SNAPSHOT_HANDOFF",
            )
            self.assertIsNotNone(result.runtime_state)
            self.assertEqual(result.runtime_state.condition_snapshot_version, "v2")
            self.assertAlmostEqual(result.runtime_state.phi_live, -1.0)
            self.assertAlmostEqual(result.runtime_state.confidence_live, 0.9)
            self.assertEqual(result.runtime_state.valid_event_count, 7)
            self.assertAlmostEqual(result.runtime_state.phi_ph_live, 0.1)
            self.assertEqual(result.runtime_state.ph_valid_event_count, 5)
            self.assertAlmostEqual(result.residual_hold.held_residual, 0.0)
            self.assertFalse(
                result.metadata["cross_snapshot_runtime_state_handoff"][
                    "residual_reused"
                ]
            )
            self.assertEqual(
                result.metadata["runtime_state_handoff_policy"],
                "EXACT_STATE_THEN_SAME_CONTEXT_GRID_THEN_REVIEWED_PRIOR",
            )

    def test_weekly_snapshot_handoff_rejects_changed_grid(self):
        with tempfile.TemporaryDirectory() as root:
            store = Scheme2RuntimeStore(root)
            store.upsert_context(
                self.state(snapshot="v1", grid_id="P10-S1"),
                residual_mfac_hold=3.0,
            )
            store.save()
            coordinator = self.empty_trajectory(
                self.config(persist_runtime=False),
                root,
                Scheme2RuntimeStore(root, enabled=False),
            )

            result = self.cycle(
                coordinator,
                "2026-08-28T12:00:00+08:00",
                ph=6.20,
                actual_flow=30.0,
                outlet_so2=20.0,
                snapshot="v2",
                context="MFAC-COND-C1",
                condition_label="C1",
                grid_id="P11-S1",
            )

            self.assertIsNone(result.runtime_state)
            self.assertEqual(
                result.metadata["cross_snapshot_runtime_state_handoff"]["reason"],
                "MIGRATION_GRID_MISMATCH",
            )
            self.assertAlmostEqual(result.residual_hold.held_residual, 0.0)


if __name__ == "__main__":
    unittest.main()