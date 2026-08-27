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
    def config():
        return PendingDoseGuardConfig(
            flow_change_deadband=1.0,
            response_onset_seconds=60.0,
            response_peak_seconds=600.0,
            response_memory_seconds=1200.0,
            max_sample_gap_seconds=30.0,
            min_confidence=0.5,
        )

    def test_flow_step_creates_pending_ph_not_volume_times_phi(self):
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
        self.assertAlmostEqual(result.pending_equivalent_delta_q, 10.0)
        self.assertAlmostEqual(result.pending_delta_ph, 0.10)
        self.assertAlmostEqual(result.predicted_ph_after_pending, 6.30)
        self.assertEqual(result.status, "CLEAR")
        self.assertEqual(
            result.metadata["recent_volume_semantics"],
            "AUDIT_ONLY_NOT_CONTROL_DEBT",
        )

    def test_pending_fraction_reduces_as_response_is_realized(self):
        guard = PendingDoseGuard(self.config(), self.envelope())
        guard.update(
            timestamp="2026-08-01T10:00:00",
            actual_supply_flow_feedback=30.0,
            ph_value=6.20,
            state=self.state(),
        )
        guard.update(
            timestamp="2026-08-01T10:00:10",
            actual_supply_flow_feedback=40.0,
            ph_value=6.20,
            state=self.state(),
        )
        result = guard.update(
            timestamp="2026-08-01T10:05:10",
            actual_supply_flow_feedback=40.0,
            ph_value=6.24,
            state=self.state(),
        )
        self.assertLess(result.pending_equivalent_delta_q, 10.0)
        self.assertGreater(result.pending_equivalent_delta_q, 0.0)

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
        self.assertGreaterEqual(result.predicted_ph_after_pending, 6.65)

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
