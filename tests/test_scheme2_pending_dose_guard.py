import unittest

from system.model.map_control.mfac_model.mfac_schema import MFACRuntimeState
from system.model.map_control.mfac_model.pending_dose_guard import (
    PendingDoseGuard,
    PendingDoseGuardConfig,
)
from system.model.map_control.mfac_model.ph_arbitration import (
    PHResidualArbitrationConfig,
)


class PendingDoseGuardTest(unittest.TestCase):
    @staticmethod
    def envelope():
        return PHResidualArbitrationConfig(
            operating_min=6.0,
            operating_max=6.4,
            safe_min=5.6,
            safe_max=6.8,
            guard_band=0.15,
            min_confidence=0.5,
        )

    @staticmethod
    def state(phi=0.01):
        return MFACRuntimeState(
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            phi_live=-2.0,
            confidence_live=1.0,
            phi_ph_live=phi,
            confidence_ph_live=1.0,
        )

    @staticmethod
    def config(**overrides):
        values = {
            "flow_change_deadband": 1.0,
            "response_onset_seconds": 60.0,
            "response_peak_seconds": 600.0,
            "max_sample_gap_seconds": 30.0,
            "min_confidence": 0.5,
        }
        values.update(overrides)
        return PendingDoseGuardConfig(**values)

    @staticmethod
    def advance(guard, start_second, end_second, flow, ph, state):
        result = None
        for second in range(start_second, end_second + 1, 10):
            result = guard.update(
                timestamp=(
                    "2026-08-01T10:%02d:%02d"
                    % ((second // 60) % 60, second % 60)
                ),
                actual_supply_flow_feedback=flow,
                ph_value=ph,
                state=state,
            )
        return result

    def test_flow_step_creates_future_ph_risk_not_volume_times_phi(self):
        guard = PendingDoseGuard(self.config(), self.envelope())
        guard.update(
            timestamp="2026-08-01T10:00:00",
            actual_supply_flow_feedback=30.0,
            ph_value=6.20,
            state=self.state(),
        )
        result = guard.update(
            timestamp="2026-08-01T10:00:10",
            actual_supply_flow_feedback=40.0,
            ph_value=6.20,
            state=self.state(),
        )
        self.assertEqual(result.active_contribution_count, 1)
        self.assertAlmostEqual(result.pending_up_equivalent_delta_q, 10.0)
        self.assertAlmostEqual(result.pending_delta_ph, 0.10)
        self.assertAlmostEqual(result.predicted_ph_upper, 6.30)
        self.assertEqual(result.status, "CLEAR")
        self.assertEqual(
            result.metadata["recent_volume_semantics"],
            "AUDIT_ONLY_NOT_CONTROL_DEBT",
        )
        self.assertEqual(
            result.metadata["future_extrema_method"],
            "PIECEWISE_LINEAR_STEP_BREAKPOINTS",
        )
        self.assertFalse(result.metadata["recovery_memory_used_for_pending_control"])
        self.assertEqual(result.metadata["pending_control_horizon_seconds"], 600.0)

    def test_future_increment_reduces_as_step_response_is_realized(self):
        guard = PendingDoseGuard(self.config(), self.envelope())
        state = self.state()
        guard.update(
            timestamp="2026-08-01T10:00:00",
            actual_supply_flow_feedback=30.0,
            ph_value=6.20,
            state=state,
        )
        guard.update(
            timestamp="2026-08-01T10:00:10",
            actual_supply_flow_feedback=40.0,
            ph_value=6.20,
            state=state,
        )
        result = self.advance(guard, 20, 310, 40.0, 6.24, state)
        self.assertLess(result.pending_up_equivalent_delta_q, 10.0)
        self.assertGreater(result.pending_up_equivalent_delta_q, 0.0)

    def test_contribution_is_no_longer_pending_after_its_peak(self):
        guard = PendingDoseGuard(self.config(), self.envelope())
        state = self.state()
        guard.update(
            timestamp="2026-08-01T10:00:00",
            actual_supply_flow_feedback=30.0,
            ph_value=6.20,
            state=state,
        )
        guard.update(
            timestamp="2026-08-01T10:00:10",
            actual_supply_flow_feedback=40.0,
            ph_value=6.20,
            state=state,
        )
        result = self.advance(guard, 20, 610, 40.0, 6.30, state)
        self.assertEqual(result.active_contribution_count, 0)
        self.assertAlmostEqual(result.pending_up_equivalent_delta_q, 0.0)
        self.assertAlmostEqual(result.pending_down_equivalent_delta_q, 0.0)
        self.assertEqual(result.predicted_peak_horizon_seconds, 0.0)

    def test_large_unrealized_positive_change_flags_upper_risk(self):
        guard = PendingDoseGuard(self.config(), self.envelope())
        state = self.state(phi=0.02)
        guard.update(
            timestamp="2026-08-01T10:00:00",
            actual_supply_flow_feedback=30.0,
            ph_value=6.20,
            state=state,
        )
        result = guard.update(
            timestamp="2026-08-01T10:00:10",
            actual_supply_flow_feedback=60.0,
            ph_value=6.20,
            state=state,
        )
        self.assertEqual(result.status, "LIMIT_POSITIVE")
        self.assertGreaterEqual(result.predicted_ph_upper, 6.65)

    def test_return_to_baseline_does_not_erase_post_pulse_peak_risk(self):
        guard = PendingDoseGuard(self.config(), self.envelope())
        state = self.state(phi=0.02)
        guard.update(
            timestamp="2026-08-01T10:00:00",
            actual_supply_flow_feedback=30.0,
            ph_value=6.20,
            state=state,
        )
        guard.update(
            timestamp="2026-08-01T10:00:10",
            actual_supply_flow_feedback=60.0,
            ph_value=6.20,
            state=state,
        )
        self.advance(guard, 20, 300, 60.0, 6.30, state)
        result = guard.update(
            timestamp="2026-08-01T10:05:10",
            actual_supply_flow_feedback=30.0,
            ph_value=6.30,
            state=state,
        )
        self.assertGreater(result.pending_up_equivalent_delta_q, 0.0)
        self.assertGreater(result.predicted_ph_upper, 6.30)
        self.assertGreater(result.predicted_peak_horizon_seconds, 0.0)

    def test_legacy_response_memory_changes_audit_window_not_pending_prediction(self):
        state = self.state()
        short = PendingDoseGuard(
            self.config(response_memory_seconds=600.0),
            self.envelope(),
        )
        long = PendingDoseGuard(
            self.config(response_memory_seconds=3600.0),
            self.envelope(),
        )
        for guard in (short, long):
            guard.update(
                timestamp="2026-08-01T10:00:00",
                actual_supply_flow_feedback=30.0,
                ph_value=6.20,
                state=state,
            )
        short_result = short.update(
            timestamp="2026-08-01T10:00:10",
            actual_supply_flow_feedback=40.0,
            ph_value=6.20,
            state=state,
        )
        long_result = long.update(
            timestamp="2026-08-01T10:00:10",
            actual_supply_flow_feedback=40.0,
            ph_value=6.20,
            state=state,
        )
        self.assertEqual(
            short_result.pending_up_equivalent_delta_q,
            long_result.pending_up_equivalent_delta_q,
        )
        self.assertEqual(
            short_result.predicted_peak_horizon_seconds,
            long_result.predicted_peak_horizon_seconds,
        )
        self.assertEqual(short_result.metadata["audit_volume_window_seconds"], 600.0)
        self.assertEqual(long_result.metadata["audit_volume_window_seconds"], 3600.0)
        self.assertFalse(short_result.metadata["recovery_memory_used_for_pending_control"])
        self.assertFalse(long_result.metadata["recovery_memory_used_for_pending_control"])

    def test_large_sample_gap_discards_unattributable_pending_history(self):
        guard = PendingDoseGuard(self.config(), self.envelope())
        state = self.state()
        guard.update(
            timestamp="2026-08-01T10:00:00",
            actual_supply_flow_feedback=30.0,
            ph_value=6.20,
            state=state,
        )
        guard.update(
            timestamp="2026-08-01T10:00:10",
            actual_supply_flow_feedback=40.0,
            ph_value=6.20,
            state=state,
        )
        result = guard.update(
            timestamp="2026-08-01T10:10:00",
            actual_supply_flow_feedback=40.0,
            ph_value=6.20,
            state=state,
        )
        self.assertEqual(result.active_contribution_count, 0)
        self.assertEqual(result.pending_equivalent_delta_q, 0.0)
        self.assertEqual(result.metadata["last_reset_reason"], "SAMPLE_GAP")


if __name__ == "__main__":
    unittest.main()
