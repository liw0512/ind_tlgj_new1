import tempfile
import unittest

from system.model.map_control.mfac_model.mfac_schema import MFACRuntimeState
from system.model.map_control.mfac_model.online_adaptation import MFACOnlineAdaptationConfig
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


class Scheme2DualResponseCoordinatorOrderingTest(unittest.TestCase):
    @staticmethod
    def config():
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
                delay_onset_seconds=0.0,
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
            ph_response=PHResponseConfig(
                baseline_window_seconds=30.0,
                delay_onset_seconds=20.0,
                observation_seconds=30.0,
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
    def state():
        return MFACRuntimeState(
            condition_snapshot_version="v001",
            mfac_context_id="MFAC-BASE-17",
            phi_live=-4.0,
            confidence_live=0.9,
            phi_ph_live=0.05,
            confidence_ph_live=0.9,
        )

    @staticmethod
    def cycle(coordinator, timestamp, **overrides):
        values = {
            "qbase_effective": 32.0,
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
            "actual_supply_flow_feedback": 32.0,
            "target_was_applied": False,
            "dcs_applied_target_supply_flow": None,
            "fast_active": False,
            "data_quality_ok": True,
        }
        values.update(overrides)
        return coordinator.process_cycle(timestamp=timestamp, **values)

    def build_and_reach(self, root):
        coordinator = Scheme2RuntimeCoordinator(
            self.config(),
            Scheme2RuntimeStore(root),
            runtime_state=self.state(),
        )
        self.cycle(
            coordinator,
            "2026-08-26T10:00:00+08:00",
            qbase_inputs_valid=False,
            actual_supply_flow_feedback=30.0,
        )
        self.cycle(
            coordinator,
            "2026-08-26T10:00:10+08:00",
            qbase_inputs_valid=False,
            actual_supply_flow_feedback=30.0,
        )
        self.cycle(
            coordinator,
            "2026-08-26T10:00:20+08:00",
            actual_supply_flow_feedback=30.0,
            target_was_applied=True,
            dcs_applied_target_supply_flow=32.0,
        )
        self.cycle(
            coordinator,
            "2026-08-26T10:00:30+08:00",
            actual_supply_flow_feedback=32.0,
            target_was_applied=True,
            dcs_applied_target_supply_flow=32.0,
        )
        reached = self.cycle(
            coordinator,
            "2026-08-26T10:00:40+08:00",
            actual_supply_flow_feedback=32.0,
            target_was_applied=True,
            dcs_applied_target_supply_flow=32.0,
        )
        self.assertEqual(reached.tracking_events[0].status, "REACHED")
        return coordinator

    def test_so2_can_complete_before_ph_without_finishing_action(self):
        with tempfile.TemporaryDirectory() as root:
            coordinator = self.build_and_reach(root)
            self.cycle(
                coordinator,
                "2026-08-26T10:00:50+08:00",
                outlet_so2=46.0,
                ph=6.15,
            )
            so2_done = self.cycle(
                coordinator,
                "2026-08-26T10:01:00+08:00",
                outlet_so2=42.0,
                ph=6.2,
            )
            self.assertEqual(so2_done.response_events[0].status, "COMPLETED")
            self.assertEqual(so2_done.ph_response_events, [])
            self.assertFalse(so2_done.metadata["response_ready_for_residual"])

            self.cycle(
                coordinator,
                "2026-08-26T10:01:10+08:00",
                outlet_so2=42.0,
                ph=6.25,
            )
            self.cycle(
                coordinator,
                "2026-08-26T10:01:20+08:00",
                outlet_so2=42.0,
                ph=6.3,
            )
            ph_done = self.cycle(
                coordinator,
                "2026-08-26T10:01:30+08:00",
                outlet_so2=42.0,
                ph=6.32,
            )
            self.assertEqual(ph_done.ph_response_events[0].status, "COMPLETED")
            self.assertTrue(ph_done.metadata["response_ready_for_residual"])

    def test_ph_censor_is_terminal_but_does_not_censor_so2_channel(self):
        with tempfile.TemporaryDirectory() as root:
            coordinator = self.build_and_reach(root)
            ph_censored = self.cycle(
                coordinator,
                "2026-08-26T10:00:50+08:00",
                outlet_so2=46.0,
                ph=None,
            )
            self.assertEqual(ph_censored.ph_response_events[0].status, "CENSORED")
            self.assertEqual(
                ph_censored.ph_response_events[0].censor_reason,
                "DATA_QUALITY_INVALID",
            )
            self.assertEqual(ph_censored.response_events, [])

            so2_done = self.cycle(
                coordinator,
                "2026-08-26T10:01:00+08:00",
                outlet_so2=42.0,
                ph=6.2,
            )
            self.assertEqual(so2_done.response_events[0].status, "COMPLETED")
            self.assertTrue(so2_done.metadata["response_ready_for_residual"])


if __name__ == "__main__":
    unittest.main()
