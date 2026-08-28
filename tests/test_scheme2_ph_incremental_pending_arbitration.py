import unittest

from system.model.map_control.mfac_model.mfac_schema import MFACRuntimeState
from system.model.map_control.mfac_model.ph_arbitration import (
    PHResidualArbitrationConfig,
    PHResidualArbiter,
)
from system.model.map_control.mfac_model.residual_control import MFACResidualDecision


class Scheme2PHIncrementalPendingArbitrationTest(unittest.TestCase):
    @staticmethod
    def arbiter():
        return PHResidualArbiter(
            PHResidualArbitrationConfig(
                operating_min=6.0,
                operating_max=6.4,
                safe_min=5.6,
                safe_max=6.8,
                guard_band=0.15,
                min_confidence=0.5,
            )
        )

    @staticmethod
    def state(phi_ph=0.1, confidence=0.9):
        return MFACRuntimeState(
            condition_snapshot_version="v1",
            mfac_context_id="MFAC-COND-C1",
            phi_live=-0.4,
            confidence_live=0.9,
            phi_ph_live=phi_ph,
            confidence_ph_live=confidence,
        )

    @staticmethod
    def residual(value):
        return MFACResidualDecision(
            status="CALCULATED",
            candidate_residual=float(value),
        )

    def test_scale_applies_to_candidate_minus_held_not_absolute_candidate(self):
        decision = self.arbiter().arbitrate(
            ph_value=6.35,
            state=self.state(phi_ph=0.1),
            so2_residual=self.residual(4.0),
            held_residual=3.0,
            arbitration_enabled=True,
        )
        self.assertEqual(decision.status, "SCALE")
        self.assertAlmostEqual(decision.requested_delta_residual, 1.0)
        self.assertAlmostEqual(decision.residual_scale, 0.5)
        self.assertAlmostEqual(decision.allowed_delta_residual, 0.5)
        self.assertAlmostEqual(decision.final_residual, 3.5)
        self.assertEqual(
            decision.metadata["residual_scale_applies_to"],
            "CANDIDATE_MINUS_HELD",
        )

    def test_high_ph_allows_reducing_existing_positive_residual(self):
        decision = self.arbiter().arbitrate(
            ph_value=6.45,
            state=self.state(phi_ph=0.1),
            so2_residual=self.residual(2.0),
            held_residual=3.0,
            arbitration_enabled=True,
        )
        self.assertNotEqual(decision.status, "BLOCK")
        self.assertAlmostEqual(decision.requested_delta_residual, -1.0)
        self.assertAlmostEqual(decision.final_residual, 2.0)

    def test_pending_upper_base_can_scale_when_current_ph_alone_would_pass(self):
        arbiter = self.arbiter()
        no_pending = arbiter.arbitrate(
            ph_value=6.30,
            state=self.state(phi_ph=0.05),
            so2_residual=self.residual(1.0),
            held_residual=0.0,
            arbitration_enabled=True,
        )
        self.assertEqual(no_pending.status, "PASS")
        self.assertAlmostEqual(no_pending.predicted_ph, 6.35)

        with_pending = arbiter.arbitrate(
            ph_value=6.30,
            state=self.state(phi_ph=0.05),
            so2_residual=self.residual(1.0),
            held_residual=0.0,
            pending_predicted_ph_upper=6.38,
            pending_predicted_ph_lower=6.25,
            arbitration_enabled=True,
        )
        self.assertEqual(with_pending.status, "SCALE")
        self.assertAlmostEqual(with_pending.pending_base_ph, 6.38)
        self.assertAlmostEqual(with_pending.residual_scale, 0.4)
        self.assertAlmostEqual(with_pending.final_residual, 0.4)
        self.assertTrue(with_pending.metadata["pending_ph_base_used"])

    def test_pending_lower_base_can_scale_negative_increment(self):
        decision = self.arbiter().arbitrate(
            ph_value=6.12,
            state=self.state(phi_ph=0.05),
            so2_residual=self.residual(-1.0),
            held_residual=0.0,
            pending_predicted_ph_upper=6.18,
            pending_predicted_ph_lower=6.02,
            arbitration_enabled=True,
        )
        self.assertEqual(decision.status, "SCALE")
        self.assertAlmostEqual(decision.pending_base_ph, 6.02)
        self.assertAlmostEqual(decision.residual_scale, 0.4)
        self.assertAlmostEqual(decision.final_residual, -0.4)

    def test_low_confidence_model_keeps_current_ph_direction_guard(self):
        arbiter = self.arbiter()
        blocked = arbiter.arbitrate(
            ph_value=6.45,
            state=self.state(phi_ph=0.1, confidence=0.1),
            so2_residual=self.residual(4.0),
            held_residual=3.0,
            arbitration_enabled=True,
        )
        self.assertEqual(blocked.status, "BLOCK")
        self.assertEqual(blocked.reason, "POSITIVE_INCREMENT_WORSENS_HIGH_PH")

        allowed_reduction = arbiter.arbitrate(
            ph_value=6.45,
            state=self.state(phi_ph=0.1, confidence=0.1),
            so2_residual=self.residual(2.0),
            held_residual=3.0,
            arbitration_enabled=True,
        )
        self.assertEqual(allowed_reduction.status, "PASS_CURRENT_PH_ONLY")
        self.assertAlmostEqual(allowed_reduction.final_residual, 2.0)


if __name__ == "__main__":
    unittest.main()
