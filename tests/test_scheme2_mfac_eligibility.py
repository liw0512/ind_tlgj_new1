import unittest

from system.model.map_control.mfac_model.mfac_eligibility import (
    MFACEligibilityConfig,
    StrictMFACEligibilityGate,
)


class StrictMFACEligibilityGateTest(unittest.TestCase):
    def setUp(self):
        self.gate = StrictMFACEligibilityGate(
            MFACEligibilityConfig(
                require_qbase_stable=True,
                max_abs_qbase_drift=0.5,
            )
        )

    @staticmethod
    def clean_evidence():
        return {
            "flow_shape": "STEP",
            "flow_disturbance_class": "STEADY",
            "scheme1_valid": True,
            "effect_complete": True,
            "flow_context_eligible": True,
            "followup_action_in_response": False,
            "circulation_changed": False,
            "major_process_transition": False,
            "equipment_changed": False,
            "context_stability_evidence_available": True,
            "condition_context_changed": False,
            "target_evidence_available": True,
            "target_changed": False,
            "qbase_evidence_available": True,
            "qbase_before": 30.0,
            "qbase_after": 30.2,
            "qbase_drift": 0.2,
            "delta_q_actual": 2.0,
            "delta_so2": -10.0,
        }

    def test_clean_step_is_eligible(self):
        result = self.gate.evaluate(self.clean_evidence())
        self.assertTrue(result.eligible)
        self.assertEqual(result.decision, "ELIGIBLE")
        self.assertEqual(result.metrics["phi_event"], -5.0)

    def test_fast_route_is_rejected(self):
        evidence = self.clean_evidence()
        evidence["flow_disturbance_class"] = "FAST"
        result = self.gate.evaluate(evidence)
        self.assertFalse(result.eligible)
        self.assertEqual(result.decision, "REJECTED")
        self.assertTrue(
            any(reason.startswith("DISTURBANCE_CLASS_NOT_ALLOWED") for reason in result.reasons)
        )

    def test_positive_phi_is_rejected(self):
        evidence = self.clean_evidence()
        evidence["delta_so2"] = 10.0
        result = self.gate.evaluate(evidence)
        self.assertFalse(result.eligible)
        self.assertIn("PHI_DIRECTION_NOT_NEGATIVE", result.reasons)

    def test_missing_qbase_is_insufficient_not_approved(self):
        evidence = self.clean_evidence()
        evidence["qbase_evidence_available"] = False
        evidence["qbase_before"] = None
        evidence["qbase_after"] = None
        evidence["qbase_drift"] = None
        result = self.gate.evaluate(evidence)
        self.assertFalse(result.eligible)
        self.assertEqual(result.decision, "INSUFFICIENT_EVIDENCE")
        self.assertIn("MISSING_QBASE_STABILITY_EVIDENCE", result.reasons)

    def test_pulse_is_not_bootstrap_sample_in_v1(self):
        evidence = self.clean_evidence()
        evidence["flow_shape"] = "PULSE"
        result = self.gate.evaluate(evidence)
        self.assertFalse(result.eligible)
        self.assertIn("FLOW_SHAPE_NOT_ALLOWED:PULSE", result.reasons)


if __name__ == "__main__":
    unittest.main()
