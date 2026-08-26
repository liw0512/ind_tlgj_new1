import math
import unittest

from system.model.map_control.mfac_model.qbase import DynamicQbaseCalculator


class DynamicQbaseCalculatorTest(unittest.TestCase):
    def setUp(self):
        self.calculator = DynamicQbaseCalculator("xst")
        self.process = {
            "yyq_SO2": 2000.0,
            "outlet_so2_target": 20.0,
            "yyq_LL": 2_200_000.0,
            "xstshsjy_MD": 1200.0,
            "xstjy_PH": 6.2,
        }

    def test_matches_audited_craftsman_formula_example(self):
        result = self.calculator.calculate(self.process)

        expected = (
            (2000.0 - 20.0)
            * 2_200_000.0
            * (100.0 / 64.0)
            / (0.26 * 1_000_000.0 * 0.9 * 1200.0)
            * 1.7
        )
        self.assertTrue(result.valid)
        self.assertAlmostEqual(result.solid_fraction, 0.26, places=12)
        self.assertAlmostEqual(result.ca_s_ratio, 1.7, places=12)
        self.assertAlmostEqual(result.qbase_raw, expected, places=12)
        self.assertAlmostEqual(result.qbase_effective, 41.20592948717949, places=12)
        self.assertEqual(result.metadata["ca_s_reference_ph"], 6.0)
        self.assertEqual(
            result.metadata["measured_ph_role"],
            "SAFETY_SUPERVISION_ONLY",
        )
        self.assertEqual(result.metadata["control_permission"], "SHADOW_ONLY")

    def test_runtime_target_overrides_process_field(self):
        process = dict(self.process, outlet_so2_target=29.0)
        explicit = self.calculator.calculate(process, target_so2=20.0)
        baseline = self.calculator.calculate(self.process)
        self.assertAlmostEqual(explicit.qbase_effective, baseline.qbase_effective)
        self.assertEqual(explicit.target_so2, 20.0)

    def test_live_ph_does_not_change_current_plant_qbase(self):
        low_ph = self.calculator.calculate(dict(self.process, xstjy_PH=5.0))
        high_ph = self.calculator.calculate(dict(self.process, xstjy_PH=6.4))
        self.assertTrue(low_ph.valid)
        self.assertTrue(high_ph.valid)
        self.assertEqual(low_ph.ca_s_ratio, 1.7)
        self.assertEqual(high_ph.ca_s_ratio, 1.7)
        self.assertAlmostEqual(low_ph.qbase_effective, high_ph.qbase_effective)

    def test_actual_flow_and_scheme1_targets_cannot_pollute_qbase(self):
        low_actual = dict(
            self.process,
            xstshsjy_LL=1.0,
            current_flow=1.0,
            xst_base_flow=1.0,
            target_final_flow=1.0,
        )
        high_actual = dict(
            self.process,
            xstshsjy_LL=70.0,
            current_flow=70.0,
            xst_base_flow=70.0,
            target_final_flow=70.0,
        )
        left = self.calculator.calculate(low_actual)
        right = self.calculator.calculate(high_actual)
        self.assertAlmostEqual(left.qbase_effective, right.qbase_effective)

    def test_missing_density_fails_closed_without_flow_fallback(self):
        process = dict(self.process)
        process.pop("xstshsjy_MD")
        process["xstshsjy_LL"] = 69.0
        process["xst_base_flow"] = 31.0
        result = self.calculator.calculate(process)
        self.assertFalse(result.valid)
        self.assertIsNone(result.qbase_effective)
        self.assertIn(
            "MISSING_OR_NONFINITE:xstshsjy_MD",
            result.reason_codes,
        )

    def test_invalid_target_and_solid_fraction_are_rejected(self):
        target = self.calculator.calculate(self.process, target_so2=35.0)
        density = self.calculator.calculate(
            dict(self.process, xstshsjy_MD=1000.0)
        )
        self.assertFalse(target.valid)
        self.assertIn("SO2_TARGET_OUT_OF_ALLOWED_RANGE", target.reason_codes)
        self.assertFalse(density.valid)
        self.assertIn("SOLID_FRACTION_OUT_OF_RANGE", density.reason_codes)

    def test_no_removal_demand_returns_zero(self):
        result = self.calculator.calculate(
            dict(self.process, yyq_SO2=10.0),
            target_so2=20.0,
        )
        self.assertTrue(result.valid)
        self.assertEqual(result.status, "NO_REMOVAL_DEMAND")
        self.assertTrue(math.isclose(result.qbase_effective, 0.0))


if __name__ == "__main__":
    unittest.main()
