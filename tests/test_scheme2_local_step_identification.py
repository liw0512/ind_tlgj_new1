import unittest

from system.model.map_control.mfac_model.continuous_target import ContinuousTargetConfig
from system.model.map_control.mfac_model.local_step_identification import (
    LocalStepIdentificationConfig,
    LocalStepIdentificationGate,
)
from system.model.map_control.mfac_model.ph_arbitration import (
    PHResidualArbitrationConfig,
)


class Scheme2LocalStepIdentificationTest(unittest.TestCase):
    @staticmethod
    def gate():
        # Numeric values here are unit-test fixtures only, not site calibration.
        return LocalStepIdentificationGate(
            LocalStepIdentificationConfig(
                step_up_m3_h=2.0,
                ph_lower_margin_inside_operating=0.05,
                ph_upper_margin_inside_operating=0.05,
                outlet_so2_headroom_to_safe_max=10.0,
                min_quiet_seconds=1800.0,
                min_candidate_interval_seconds=3600.0,
                max_abs_actual_minus_qbase=1.0,
                max_abs_qbase_drift=1.0,
                max_abs_inlet_so2_change=50.0,
                max_outlet_so2_baseline_range=2.0,
            ),
            ContinuousTargetConfig(
                hard_min_supply_flow=0.0,
                hard_max_supply_flow=70.0,
            ),
            PHResidualArbitrationConfig(
                operating_min=6.0,
                operating_max=6.4,
                safe_min=5.6,
                safe_max=6.8,
                guard_band=0.15,
                min_confidence=0.5,
            ),
            outlet_so2_safe_max=35.0,
        )

    @staticmethod
    def inputs():
        return {
            "timestamp": "2026-08-27T10:00:00+08:00",
            "request_enabled": True,
            "actual_supply_flow": 30.0,
            "qbase_effective": 30.2,
            "ph_value": 6.20,
            "outlet_so2": 15.0,
            "condition_snapshot_version": "v001",
            "mfac_context_id": "CTX",
            "seconds_since_last_supply_action": 2400.0,
            "seconds_since_last_identification": 7200.0,
            "qbase_drift": 0.2,
            "inlet_so2_change": 20.0,
            "outlet_so2_baseline_range": 1.0,
            "fast_active": False,
            "data_quality_ok": True,
            "equipment_changed": False,
        }

    def test_clean_state_only_creates_manual_review_candidate(self):
        gate = self.gate()
        result = gate.propose(**self.inputs())
        self.assertEqual(result.status, "REVIEW_CANDIDATE")
        self.assertTrue(result.eligible_for_manual_test_review)
        self.assertEqual(result.proposed_test_target_supply_flow, 32.0)
        self.assertTrue(result.manual_review_required)
        self.assertTrue(result.manual_execution_required)
        self.assertFalse(result.automatic_execution_allowed)
        self.assertFalse(result.dcs_write_enabled)
        self.assertFalse(result.learning_permission)
        self.assertFalse(result.metadata["normal_algorithm_target_replaced"])

    def test_recent_supply_action_blocks_candidate(self):
        values = self.inputs()
        values["seconds_since_last_supply_action"] = 600.0
        result = self.gate().propose(**values)
        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("RECENT_SUPPLY_ACTION_NOT_SETTLED", result.reasons)

    def test_high_ph_blocks_candidate_inside_normal_runtime(self):
        values = self.inputs()
        values["ph_value"] = 6.38
        result = self.gate().propose(**values)
        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("PH_OUTSIDE_IDENTIFICATION_BAND", result.reasons)

    def test_outlet_so2_without_headroom_blocks_candidate(self):
        values = self.inputs()
        values["outlet_so2"] = 30.0
        result = self.gate().propose(**values)
        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("OUTLET_SO2_HEADROOM_INSUFFICIENT", result.reasons)

    def test_step_near_plant_max_is_blocked_not_clipped(self):
        values = self.inputs()
        values["actual_supply_flow"] = 69.0
        values["qbase_effective"] = 69.0
        result = self.gate().propose(**values)
        self.assertEqual(result.status, "BLOCKED")
        self.assertIsNone(result.proposed_test_target_supply_flow)
        self.assertIn("PROPOSED_STEP_EXCEEDS_PLANT_MAX", result.reasons)

    def test_fast_or_equipment_change_blocks_identification(self):
        for key in ("fast_active", "equipment_changed"):
            with self.subTest(key=key):
                values = self.inputs()
                values[key] = True
                result = self.gate().propose(**values)
                self.assertEqual(result.status, "BLOCKED")

    def test_gate_exposes_no_execution_api(self):
        gate = self.gate()
        self.assertFalse(hasattr(gate, "execute"))
        self.assertFalse(gate.dcs_write_enabled)
        self.assertFalse(gate.automatic_execution_allowed)


if __name__ == "__main__":
    unittest.main()
