import tempfile
import unittest

from system.model.map_control.mfac_model.mfac_schema import MFACRuntimeState
from system.model.map_control.mfac_model.online_adaptation import (
    MFACOnlineAdaptationConfig,
)
from system.model.map_control.mfac_model.ph_adaptation import (
    PHOnlineAdaptationConfig,
    PHOnlineAdapter,
)
from system.model.map_control.mfac_model.ph_arbitration import (
    PHResidualArbitrationConfig,
    PHResidualArbiter,
)
from system.model.map_control.mfac_model.ph_response import (
    PHResponseConfig,
    PHResponseEvent,
)
from system.model.map_control.mfac_model.process_response import ProcessResponseConfig
from system.model.map_control.mfac_model.residual_control import (
    MFACResidualConfig,
    MFACResidualDecision,
)
from system.model.map_control.mfac_model.runtime_coordinator import (
    Scheme2RuntimeCoordinator,
    Scheme2RuntimeCoordinatorConfig,
)
from system.model.map_control.mfac_model.runtime_store import Scheme2RuntimeStore
from system.model.map_control.mfac_model.supply_flow_tracking import (
    SupplyFlowTrackingConfig,
)


class Scheme2DualResponseTest(unittest.TestCase):
    @staticmethod
    def runtime_state():
        return MFACRuntimeState(
            condition_snapshot_version="v001",
            mfac_context_id="MFAC-BASE-17",
            phi_live=-4.0,
            confidence_live=0.9,
            phi_ph_live=0.05,
            confidence_ph_live=0.9,
        )

    def test_runtime_state_keeps_legacy_so2_fields_and_ph_state(self):
        state = self.runtime_state()
        payload = state.to_dict()
        self.assertEqual(payload["phi_live"], -4.0)
        self.assertEqual(payload["phi_so2_live"], -4.0)
        self.assertEqual(payload["phi_ph_live"], 0.05)

        restored = MFACRuntimeState.from_dict(payload)
        self.assertEqual(restored.phi_live, -4.0)
        self.assertEqual(restored.phi_so2_live, -4.0)
        self.assertEqual(restored.phi_ph_live, 0.05)

        old_payload = {
            "condition_snapshot_version": "v001",
            "mfac_context_id": "MFAC-BASE-17",
            "phi_live": -4.0,
            "confidence_live": 0.8,
        }
        old_restored = MFACRuntimeState.from_dict(old_payload)
        self.assertEqual(old_restored.phi_live, -4.0)
        self.assertIsNone(old_restored.phi_ph_live)

    def test_ph_online_adapter_requires_positive_physical_direction(self):
        adapter = PHOnlineAdapter(
            PHOnlineAdaptationConfig(
                eta=0.2,
                mu=1.0,
                phi_lower_bound=0.001,
                phi_upper_bound=1.0,
                max_single_update_abs=0.1,
            )
        )
        event = PHResponseEvent(
            response_event_id="S2-PH-RESP-00000001",
            tracking_event_id="S2-FLOW-00000001",
            status="COMPLETED",
            condition_snapshot_version="v001",
            mfac_context_id="MFAC-BASE-17",
            target_change_time="2026-08-26T10:00:00+08:00",
            actual_flow_reached_time="2026-08-26T10:00:20+08:00",
            response_start_time="2026-08-26T10:00:30+08:00",
            response_end_time="2026-08-26T10:00:50+08:00",
            q_before=30.0,
            q_after=32.0,
            delta_q_actual=2.0,
            ph_before=6.1,
            ph_after=6.3,
            delta_ph=0.2,
            qbase_before=30.0,
            qbase_after=30.0,
            qbase_drift=0.0,
        )
        result = adapter.update(self.runtime_state(), event)
        self.assertTrue(result.updated)
        self.assertTrue(result.phi_updated)
        self.assertTrue(result.confidence_updated)
        self.assertGreater(result.new_phi, 0.0)
        self.assertEqual(result.runtime_state.phi_live, -4.0)
        self.assertEqual(result.runtime_state.ph_valid_event_count, 1)

        wrong_direction = PHResponseEvent(**{
            **event.__dict__,
            "response_event_id": "S2-PH-RESP-00000002",
            "delta_ph": -0.2,
        })
        conflict = adapter.update(result.runtime_state, wrong_direction)
        self.assertTrue(conflict.updated)
        self.assertFalse(conflict.phi_updated)
        self.assertTrue(conflict.confidence_updated)
        self.assertEqual(
            conflict.reason,
            "CONFIDENCE_DOWNGRADED_PHYSICAL_CONFLICT",
        )
        self.assertAlmostEqual(conflict.new_phi, result.new_phi)
        self.assertLess(conflict.new_confidence, result.new_confidence)
        self.assertEqual(conflict.runtime_state.ph_valid_event_count, 1)

    def test_ph_arbitration_scales_so2_residual_without_addition(self):
        arbiter = PHResidualArbiter(
            PHResidualArbitrationConfig(
                operating_min=6.0,
                operating_max=6.4,
                safe_min=5.6,
                safe_max=6.8,
                guard_band=0.15,
                min_confidence=0.5,
            )
        )
        source = MFACResidualDecision(
            status="CALCULATED",
            candidate_residual=2.0,
        )
        state = self.runtime_state()
        state.phi_ph_live = 0.1
        decision = arbiter.arbitrate(
            ph_value=6.35,
            state=state,
            so2_residual=source,
            arbitration_enabled=True,
        )
        self.assertEqual(decision.status, "SCALE")
        self.assertAlmostEqual(decision.residual_scale, 0.25)
        self.assertAlmostEqual(decision.final_residual, 0.5)
        self.assertFalse(decision.metadata["additive_ph_residual"])
        self.assertAlmostEqual(
            decision.final_residual,
            source.candidate_residual * decision.residual_scale,
        )

        blocked = arbiter.arbitrate(
            ph_value=6.45,
            state=state,
            so2_residual=source,
            arbitration_enabled=True,
        )
        self.assertEqual(blocked.status, "BLOCK")
        self.assertEqual(blocked.final_residual, 0.0)

    @staticmethod
    def coordinator_config():
        return Scheme2RuntimeCoordinatorConfig(
            tracking=SupplyFlowTrackingConfig(
                target_change_deadband=0.5,
                reach_tolerance=0.1,
                required_sustain_seconds=10.0,
                execution_timeout_seconds=120.0,
                max_sample_gap_seconds=15.0,
            ),
            response=ProcessResponseConfig(
                baseline_window_seconds=30.0,
                delay_onset_seconds=20.0,
                observation_seconds=30.0,
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
            ph_response=PHResponseConfig(
                baseline_window_seconds=30.0,
                delay_onset_seconds=0.0,
                observation_seconds=20.0,
                measurement_window_seconds=10.0,
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
            residual_control_enabled=False,
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
            "ph": 6.1,
            "actual_supply_flow_feedback": 30.0,
            "target_was_applied": False,
            "dcs_applied_target_supply_flow": None,
            "fast_active": False,
            "data_quality_ok": True,
        }
        values.update(overrides)
        return coordinator.process_cycle(timestamp=timestamp, **values)

    def test_coordinator_waits_for_both_response_channels(self):
        with tempfile.TemporaryDirectory() as root:
            coordinator = Scheme2RuntimeCoordinator(
                self.coordinator_config(),
                Scheme2RuntimeStore(root),
                runtime_state=self.runtime_state(),
            )

            self.cycle(
                coordinator,
                "2026-08-26T10:00:00+08:00",
                qbase_inputs_valid=False,
                ph=6.1,
            )
            self.cycle(
                coordinator,
                "2026-08-26T10:00:10+08:00",
                qbase_inputs_valid=False,
                ph=6.1,
            )
            self.cycle(
                coordinator,
                "2026-08-26T10:00:20+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=30.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=32.0,
                ph=6.1,
            )
            self.cycle(
                coordinator,
                "2026-08-26T10:00:30+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=32.0,
                ph=6.1,
            )
            reached = self.cycle(
                coordinator,
                "2026-08-26T10:00:40+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
                target_was_applied=True,
                dcs_applied_target_supply_flow=32.0,
                ph=6.1,
            )
            self.assertEqual(reached.tracking_events[0].status, "REACHED")
            self.assertNotEqual(reached.active_response_tracking_event_id, "")
            self.assertNotEqual(reached.active_ph_response_tracking_event_id, "")

            self.cycle(
                coordinator,
                "2026-08-26T10:00:50+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
                outlet_so2=48.0,
                ph=6.2,
            )
            ph_done = self.cycle(
                coordinator,
                "2026-08-26T10:01:00+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
                outlet_so2=47.0,
                ph=6.3,
            )
            self.assertEqual(len(ph_done.ph_response_events), 1)
            self.assertEqual(ph_done.ph_response_events[0].status, "COMPLETED")
            self.assertEqual(ph_done.response_events, [])
            self.assertFalse(ph_done.metadata["response_ready_for_residual"])

            self.cycle(
                coordinator,
                "2026-08-26T10:01:10+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
                outlet_so2=46.0,
                ph=6.3,
            )
            self.cycle(
                coordinator,
                "2026-08-26T10:01:20+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
                outlet_so2=44.0,
                ph=6.3,
            )
            so2_done = self.cycle(
                coordinator,
                "2026-08-26T10:01:30+08:00",
                qbase_effective=32.0,
                actual_supply_flow_feedback=32.0,
                outlet_so2=42.0,
                ph=6.3,
            )
            self.assertEqual(len(so2_done.response_events), 1)
            self.assertEqual(so2_done.response_events[0].status, "COMPLETED")
            self.assertTrue(so2_done.metadata["response_ready_for_residual"])
            self.assertEqual(
                so2_done.metadata["response_ready_tracking_event_id"],
                so2_done.response_events[0].tracking_event_id,
            )
            self.assertTrue(so2_done.metadata["dual_response_enabled"])
            self.assertFalse(so2_done.metadata["additive_ph_residual"])
            self.assertFalse(so2_done.learning_enabled)
            self.assertFalse(so2_done.residual_control_enabled)
            self.assertFalse(so2_done.dcs_write_enabled)


if __name__ == "__main__":
    unittest.main()
