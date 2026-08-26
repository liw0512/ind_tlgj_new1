import unittest

from system.model.map_control.mfac_model.mfac_schema import MFACRuntimeState
from system.model.map_control.mfac_model.residual_control import (
    MFACResidualConfig,
    MFACResidualController,
    MFACResidualHoldManager,
)


class MFACResidualControlTest(unittest.TestCase):
    @staticmethod
    def state(confidence=0.8, phi=-5.0):
        return MFACRuntimeState(
            condition_snapshot_version="v001",
            mfac_context_id="MFAC-BASE-17",
            phi_live=phi,
            confidence_live=confidence,
        )

    @staticmethod
    def controller(max_abs=5.0):
        return MFACResidualController(
            MFACResidualConfig(
                rho=1.0,
                lambda_regularization=1.0,
                max_abs_residual=max_abs,
                min_confidence=0.6,
            )
        )

    def test_high_outlet_so2_produces_positive_slurry_residual(self):
        result = self.controller().compute(
            so2_target=35.0,
            outlet_so2=50.0,
            state=self.state(),
            control_enabled=True,
        )

        self.assertEqual(result.status, "CALCULATED")
        self.assertAlmostEqual(result.so2_error, -15.0)
        self.assertAlmostEqual(result.candidate_residual, 75.0 / 26.0)
        self.assertGreater(result.candidate_residual, 0.0)

    def test_low_outlet_so2_produces_negative_slurry_residual(self):
        result = self.controller().compute(
            so2_target=35.0,
            outlet_so2=25.0,
            state=self.state(),
            control_enabled=True,
        )

        self.assertEqual(result.status, "CALCULATED")
        self.assertLess(result.candidate_residual, 0.0)

    def test_control_disabled_keeps_candidate_zero(self):
        result = self.controller().compute(
            so2_target=35.0,
            outlet_so2=50.0,
            state=self.state(),
            control_enabled=False,
        )

        self.assertEqual(result.status, "CONTROL_DISABLED")
        self.assertEqual(result.candidate_residual, 0.0)

    def test_low_confidence_cannot_generate_residual(self):
        result = self.controller().compute(
            so2_target=35.0,
            outlet_so2=50.0,
            state=self.state(confidence=0.4),
            control_enabled=True,
        )

        self.assertEqual(result.status, "LOW_CONFIDENCE")
        self.assertEqual(result.candidate_residual, 0.0)

    def test_non_negative_phi_is_rejected(self):
        result = self.controller().compute(
            so2_target=35.0,
            outlet_so2=50.0,
            state=self.state(phi=1.0),
            control_enabled=True,
        )

        self.assertEqual(result.status, "INVALID_PHI")
        self.assertEqual(result.candidate_residual, 0.0)

    def test_residual_is_hard_clipped(self):
        result = self.controller(max_abs=1.0).compute(
            so2_target=35.0,
            outlet_so2=100.0,
            state=self.state(),
            control_enabled=True,
        )

        self.assertEqual(result.candidate_residual, 1.0)
        self.assertTrue(result.hard_clipped)

    def test_hold_manager_does_not_accumulate_each_cycle(self):
        controller = self.controller()
        hold = MFACResidualHoldManager()
        candidate = controller.compute(
            so2_target=35.0,
            outlet_so2=50.0,
            state=self.state(),
            control_enabled=True,
        )

        first = hold.update(candidate, allow_update=True)
        self.assertEqual(first.status, "UPDATED")
        initial = first.held_residual

        waiting1 = hold.update(candidate, allow_update=False)
        waiting2 = hold.update(candidate, allow_update=False)
        self.assertEqual(waiting1.status, "HOLD_WAITING_RESPONSE")
        self.assertAlmostEqual(waiting1.held_residual, initial)
        self.assertAlmostEqual(waiting2.held_residual, initial)
        self.assertAlmostEqual(hold.held_residual, initial)

    def test_next_ready_update_replaces_instead_of_adds(self):
        controller = self.controller()
        hold = MFACResidualHoldManager()
        first = controller.compute(
            so2_target=35.0,
            outlet_so2=50.0,
            state=self.state(),
            control_enabled=True,
        )
        hold.update(first, allow_update=True)

        second = controller.compute(
            so2_target=35.0,
            outlet_so2=40.0,
            state=self.state(),
            control_enabled=True,
        )
        result = hold.update(second, allow_update=True)

        self.assertAlmostEqual(result.held_residual, second.candidate_residual)
        self.assertNotAlmostEqual(
            result.held_residual,
            first.candidate_residual + second.candidate_residual,
        )


if __name__ == "__main__":
    unittest.main()
