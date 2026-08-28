import unittest

from system.model.map_control.mfac_model.decision_permission_gate import (
    ResidualDecisionPermissionGate,
)


class Scheme2DecisionPermissionGateTest(unittest.TestCase):
    def test_initial_decision_then_waits_for_response(self):
        gate = ResidualDecisionPermissionGate(min_hold_seconds=60.0)
        first = gate.evaluate(
            timestamp="2026-08-28T10:00:00+08:00",
            residual_control_enabled=True,
            qbase_inputs_valid=True,
            data_quality_ok=True,
            fast_active=False,
            equipment_changed=False,
            held_residual=0.0,
            proposed_residual=2.0,
            response_ready=False,
            pending_status="CLEAR",
        )
        self.assertTrue(first.allowed)
        self.assertEqual(first.status, "ALLOW_INITIAL_DECISION")
        gate.record_residual_change(
            timestamp="2026-08-28T10:00:00+08:00",
            previous_residual=0.0,
            new_residual=2.0,
        )

        waiting = gate.evaluate(
            timestamp="2026-08-28T10:01:30+08:00",
            residual_control_enabled=True,
            qbase_inputs_valid=True,
            data_quality_ok=True,
            fast_active=False,
            equipment_changed=False,
            held_residual=2.0,
            proposed_residual=3.0,
            response_ready=False,
            pending_status="CLEAR",
        )
        self.assertFalse(waiting.allowed)
        self.assertEqual(waiting.status, "HOLD_WAITING_RESPONSE")

    def test_response_and_min_hold_are_both_required(self):
        gate = ResidualDecisionPermissionGate(min_hold_seconds=120.0)
        gate.record_residual_change(
            timestamp="2026-08-28T10:00:00+08:00",
            previous_residual=0.0,
            new_residual=2.0,
        )
        early = gate.evaluate(
            timestamp="2026-08-28T10:01:00+08:00",
            residual_control_enabled=True,
            qbase_inputs_valid=True,
            data_quality_ok=True,
            fast_active=False,
            equipment_changed=False,
            held_residual=2.0,
            proposed_residual=3.0,
            response_ready=True,
            pending_status="CLEAR",
        )
        self.assertFalse(early.allowed)
        self.assertEqual(early.status, "HOLD_MIN_DURATION")
        self.assertAlmostEqual(early.hold_remaining_seconds, 60.0)

        later = gate.evaluate(
            timestamp="2026-08-28T10:02:00+08:00",
            residual_control_enabled=True,
            qbase_inputs_valid=True,
            data_quality_ok=True,
            fast_active=False,
            equipment_changed=False,
            held_residual=2.0,
            proposed_residual=3.0,
            response_ready=False,
            pending_status="CLEAR",
        )
        self.assertTrue(later.allowed)

    def test_runtime_guards_and_pending_direction_block_decision(self):
        gate = ResidualDecisionPermissionGate(min_hold_seconds=0.0)
        fast = gate.evaluate(
            timestamp="2026-08-28T10:00:00+08:00",
            residual_control_enabled=True,
            qbase_inputs_valid=True,
            data_quality_ok=True,
            fast_active=True,
            equipment_changed=False,
            held_residual=0.0,
            proposed_residual=2.0,
            response_ready=False,
            pending_status="CLEAR",
        )
        self.assertFalse(fast.allowed)
        self.assertIn("FAST_ACTIVE", fast.reason_codes)

        pending = gate.evaluate(
            timestamp="2026-08-28T10:00:10+08:00",
            residual_control_enabled=True,
            qbase_inputs_valid=True,
            data_quality_ok=True,
            fast_active=False,
            equipment_changed=False,
            held_residual=0.0,
            proposed_residual=2.0,
            response_ready=False,
            pending_status="WATCH_HIGH",
        )
        self.assertFalse(pending.allowed)
        self.assertEqual(pending.status, "HOLD_PENDING_PH")


if __name__ == "__main__":
    unittest.main()
