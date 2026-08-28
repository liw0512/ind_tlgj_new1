import tempfile
import unittest

from system.model.map_control.mfac_model.historical_sensitivity_map import (
    HistoricalSensitivityMap,
    HistoricalSensitivityMapConfig,
    HistoricalSensitivityQuery,
    HistoricalSensitivitySurface,
)
from system.model.map_control.mfac_model.mfac_schema import MFACRuntimeState
from system.model.map_control.mfac_model.runtime_config import build_mfac_runtime


class Scheme2HistoricalSensitivityMapTest(unittest.TestCase):
    @staticmethod
    def map_config():
        return HistoricalSensitivityMapConfig(
            max_neighbor_grid_distance=2,
            neighbor_confidence_penalty=0.8,
            pooled_confidence_penalty=0.35,
            max_profile_extrapolation_distance=3.0,
        )

    @staticmethod
    def surface(profile_id, context_id, grid_id, *, so2=-0.4, ph=0.02):
        return HistoricalSensitivitySurface(
            profile_id=profile_id,
            condition_snapshot_version="v1",
            mfac_context_id=context_id,
            grid_id=grid_id,
            phi_so2_prior=so2,
            phi_ph_prior=ph,
            confidence_so2=0.8,
            confidence_ph=0.7,
            event_count=30,
            independent_days=5,
            feature_center={
                "qbase": 30.0,
                "inlet_so2": 1500.0,
                "ph": 6.2,
                "outlet_so2": 10.0,
            },
            feature_scale={
                "qbase": 10.0,
                "inlet_so2": 100.0,
                "ph": 0.1,
                "outlet_so2": 5.0,
            },
            support_min={
                "qbase": 20.0,
                "inlet_so2": 1400.0,
                "ph": 6.1,
                "outlet_so2": 5.0,
            },
            support_max={
                "qbase": 40.0,
                "inlet_so2": 1600.0,
                "ph": 6.3,
                "outlet_so2": 15.0,
            },
            phi_so2_coefficients={"qbase": -0.1},
            phi_ph_coefficients={"qbase": 0.005},
        )

    def test_exact_context_is_continuous_not_exact_state_lookup(self):
        mapping = HistoricalSensitivityMap(
            "v1",
            [self.surface("S10", "MFAC-COND-C1", "P10-S1")],
            self.map_config(),
        )
        first = mapping.resolve(
            HistoricalSensitivityQuery(
                condition_snapshot_version="v1",
                mfac_context_id="MFAC-COND-C1",
                grid_id="P10-S1",
                qbase=30.0,
                inlet_so2=1500.0,
                ph=6.2,
                outlet_so2=10.0,
            )
        )
        second = mapping.resolve(
            HistoricalSensitivityQuery(
                condition_snapshot_version="v1",
                mfac_context_id="MFAC-COND-C1",
                grid_id="P10-S1",
                qbase=35.0,
                inlet_so2=1520.0,
                ph=6.18,
                outlet_so2=11.0,
            )
        )
        self.assertTrue(first.available)
        self.assertEqual(first.mapping_source, "EXACT_CONTEXT")
        self.assertAlmostEqual(first.phi_so2, -0.4)
        self.assertAlmostEqual(second.phi_so2, -0.45)
        self.assertAlmostEqual(second.phi_ph, 0.0225)
        self.assertFalse(second.extrapolated)

    def test_unseen_exact_context_uses_neighbor_grid_instead_of_empty_lookup(self):
        mapping = HistoricalSensitivityMap(
            "v1",
            [
                self.surface("S10", "MFAC-COND-OLD10", "P10-S1", so2=-0.3, ph=0.02),
                self.surface("S12", "MFAC-COND-OLD12", "P12-S1", so2=-0.5, ph=0.03),
            ],
            self.map_config(),
        )
        decision = mapping.resolve(
            HistoricalSensitivityQuery(
                condition_snapshot_version="v1",
                mfac_context_id="MFAC-COND-NEW11",
                grid_id="P11-S1",
                qbase=30.0,
                inlet_so2=1500.0,
                ph=6.2,
                outlet_so2=10.0,
            )
        )
        self.assertTrue(decision.available)
        self.assertEqual(decision.mapping_source, "NEIGHBOR_INTERPOLATED")
        self.assertAlmostEqual(decision.phi_so2, -0.4)
        self.assertAlmostEqual(decision.phi_ph, 0.025)
        self.assertTrue(decision.extrapolated)
        self.assertLess(decision.confidence_so2, 0.8)

    def test_out_of_support_penalizes_confidence_without_exact_state_rejection(self):
        mapping = HistoricalSensitivityMap(
            "v1",
            [self.surface("S10", "MFAC-COND-C1", "P10-S1")],
            self.map_config(),
        )
        decision = mapping.resolve(
            HistoricalSensitivityQuery(
                condition_snapshot_version="v1",
                mfac_context_id="MFAC-COND-C1",
                grid_id="P10-S1",
                qbase=45.0,
                inlet_so2=1500.0,
                ph=6.2,
                outlet_so2=10.0,
            )
        )
        self.assertTrue(decision.available)
        self.assertTrue(decision.extrapolated)
        self.assertIn("WORKPOINT_EXTRAPOLATED", decision.reason_codes)
        self.assertLess(decision.confidence_so2, 0.8)

    def test_map_round_trip_preserves_hierarchical_contract(self):
        original = HistoricalSensitivityMap(
            "v1",
            [self.surface("S10", "MFAC-COND-C1", "P10-S1")],
            self.map_config(),
            pooled_profile=self.surface("POOL", "", "", so2=-0.25, ph=0.015),
        )
        restored = HistoricalSensitivityMap.from_dict(original.to_dict())
        decision = restored.resolve(
            HistoricalSensitivityQuery(
                condition_snapshot_version="v1",
                mfac_context_id="UNKNOWN",
                grid_id="P30-S1",
                qbase=30.0,
                inlet_so2=1500.0,
                ph=6.2,
                outlet_so2=10.0,
            )
        )
        self.assertTrue(decision.available)
        self.assertEqual(decision.mapping_source, "POOLED_FALLBACK")
        self.assertAlmostEqual(decision.phi_so2, -0.25)
        self.assertLess(decision.confidence_so2, 0.8)

    @staticmethod
    def runtime_config(root):
        return {
            "enabled": True,
            "learning_enabled": False,
            "residual_control_enabled": False,
            "dcs_write_enabled": False,
            "persist_runtime": False,
            "runtime_dir": root,
            "tracking": {
                "target_change_deadband": 0.5,
                "reach_tolerance": 0.1,
                "required_sustain_seconds": 10.0,
                "execution_timeout_seconds": 30.0,
                "max_sample_gap_seconds": 15.0,
            },
            "so2_response": {
                "baseline_window_seconds": 30.0,
                "delay_onset_seconds": 10.0,
                "observation_seconds": 20.0,
                "measurement_window_seconds": 10.0,
                "max_sample_gap_seconds": 15.0,
                "target_change_tolerance": 0.0,
                "min_baseline_samples": 2,
                "min_response_samples": 2,
            },
            "so2_adaptation": {
                "eta": 0.2,
                "mu": 1.0,
                "phi_lower_bound": -10.0,
                "phi_upper_bound": -0.1,
                "max_single_update_abs": 1.0,
            },
            "residual": {
                "rho": 1.0,
                "lambda_regularization": 1.0,
                "max_abs_residual": 5.0,
                "min_confidence": 0.5,
            },
            "ph_response": {
                "baseline_window_seconds": 20.0,
                "delay_onset_seconds": 5.0,
                "observation_seconds": 15.0,
                "measurement_window_seconds": 5.0,
                "max_sample_gap_seconds": 15.0,
                "target_change_tolerance": 0.0,
                "min_baseline_samples": 2,
                "min_response_samples": 2,
            },
            "ph_adaptation": {
                "eta": 0.2,
                "mu": 1.0,
                "phi_lower_bound": 0.01,
                "phi_upper_bound": 1.0,
                "max_single_update_abs": 0.1,
            },
            "ph_arbitration": {"min_confidence": 0.5},
            "pending_dose": {
                "flow_change_deadband": 1.0,
                "response_onset_seconds": 10.0,
                "response_peak_seconds": 30.0,
                "max_sample_gap_seconds": 15.0,
                "min_confidence": 0.5,
            },
            "trajectory_planner": {
                "max_step_up": 2.0,
                "max_step_down": 3.0,
                "min_hold_seconds": 20.0,
                "demand_deadband": 0.1,
            },
        }

    def test_runtime_maps_prior_until_each_channel_has_online_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            result = build_mfac_runtime(self.runtime_config(root))
            self.assertTrue(result.configured)
            coordinator = result.coordinator
            coordinator.set_historical_sensitivity_map(
                HistoricalSensitivityMap(
                    "v1",
                    [self.surface("S10", "MFAC-COND-C1", "P10-S1")],
                    self.map_config(),
                )
            )

            first = coordinator.process_cycle(
                timestamp="2026-08-28T10:00:00+08:00",
                qbase_effective=30.0,
                qbase_inputs_valid=True,
                outlet_so2=10.0,
                so2_target=20.0,
                condition_snapshot_version="v1",
                mfac_context_id="MFAC-COND-C1",
                condition_label="C1",
                base_condition_id="B1",
                grid_id="P10-S1",
                inlet_so2=1500.0,
                ph=6.2,
                actual_supply_flow_feedback=30.0,
            )
            self.assertAlmostEqual(first.runtime_state.phi_live, -0.4)
            self.assertAlmostEqual(first.runtime_state.phi_ph_live, 0.02)
            self.assertEqual(first.metadata["context_status"], "CONTEXT_HISTORICAL_PRIOR:EXACT_CONTEXT")
            self.assertEqual(first.algorithm_target.algorithm_target_supply_flow, 30.0)
            self.assertFalse(first.learning_enabled)
            self.assertFalse(first.residual_control_enabled)
            self.assertFalse(first.dcs_write_enabled)

            second = coordinator.process_cycle(
                timestamp="2026-08-28T10:00:10+08:00",
                qbase_effective=35.0,
                qbase_inputs_valid=True,
                outlet_so2=11.0,
                so2_target=20.0,
                condition_snapshot_version="v1",
                mfac_context_id="MFAC-COND-C1",
                condition_label="C1",
                base_condition_id="B1",
                grid_id="P10-S1",
                inlet_so2=1520.0,
                ph=6.18,
                actual_supply_flow_feedback=30.0,
            )
            self.assertAlmostEqual(second.runtime_state.phi_live, -0.45)
            self.assertAlmostEqual(second.runtime_state.phi_ph_live, 0.0225)

            learned_so2 = MFACRuntimeState(
                condition_snapshot_version="v1",
                mfac_context_id="MFAC-COND-C1",
                phi_live=-0.8,
                confidence_live=0.9,
                valid_event_count=1,
                last_event_id="ONLINE-SO2-1",
                phi_ph_live=0.02,
                confidence_ph_live=0.7,
                ph_valid_event_count=0,
            )
            coordinator.set_runtime_state(learned_so2)
            third = coordinator.process_cycle(
                timestamp="2026-08-28T10:00:20+08:00",
                qbase_effective=40.0,
                qbase_inputs_valid=True,
                outlet_so2=11.0,
                so2_target=20.0,
                condition_snapshot_version="v1",
                mfac_context_id="MFAC-COND-C1",
                condition_label="C1",
                base_condition_id="B1",
                grid_id="P10-S1",
                inlet_so2=1520.0,
                ph=6.18,
                actual_supply_flow_feedback=30.0,
            )
            self.assertAlmostEqual(third.runtime_state.phi_live, -0.8)
            self.assertEqual(third.runtime_state.valid_event_count, 1)
            self.assertAlmostEqual(third.runtime_state.phi_ph_live, 0.025)


if __name__ == "__main__":
    unittest.main()
