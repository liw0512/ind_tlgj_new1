import inspect
import math
import unittest

from system.model.config.mfac_plant_contract import target_supply_flow_contract
from system.model.map_control.mfac_model.continuous_target import (
    COUNTERFACTUAL_SHADOW,
    ContinuousTargetConfig,
    ContinuousTargetPublisher,
)


class ContinuousTargetPublisherTest(unittest.TestCase):
    def test_calculates_qbase_plus_residual(self):
        publisher = ContinuousTargetPublisher()
        result = publisher.publish(
            30.0,
            2.5,
            timestamp="2026-08-26T14:00:00+08:00",
        )

        self.assertTrue(result.algorithm_target_valid)
        self.assertEqual(result.algorithm_target_status, "CALCULATED")
        self.assertEqual(result.algorithm_target_supply_flow, 32.5)
        self.assertEqual(result.qbase_effective, 30.0)
        self.assertEqual(result.residual_mfac_hold, 2.5)
        self.assertFalse(result.hard_clipped)
        self.assertEqual(publisher.last_valid_algorithm_target, 32.5)

    def test_default_hard_range_comes_from_plant_contract(self):
        contract = target_supply_flow_contract()
        lower = float(contract["minimum"])
        upper = float(contract["maximum"])
        publisher = ContinuousTargetPublisher()

        high = publisher.publish(upper - 1.0, 5.0)
        self.assertEqual(high.algorithm_target_supply_flow, upper)
        self.assertTrue(high.hard_clipped)

        low = publisher.publish(lower + 1.0, -5.0)
        self.assertEqual(low.algorithm_target_supply_flow, lower)
        self.assertTrue(low.hard_clipped)

    def test_invalid_input_holds_last_valid_algorithm_target(self):
        publisher = ContinuousTargetPublisher()
        publisher.publish(30.0, 2.0)

        result = publisher.publish(None, 2.0, inputs_valid=False)

        self.assertFalse(result.algorithm_target_valid)
        self.assertEqual(result.algorithm_target_status, "HOLD_LAST_INVALID_INPUT")
        self.assertEqual(result.algorithm_target_supply_flow, 32.0)
        self.assertEqual(publisher.last_valid_algorithm_target, 32.0)

    def test_non_finite_input_holds_last_valid_target(self):
        publisher = ContinuousTargetPublisher()
        publisher.publish(30.0, 0.0)

        result = publisher.publish(math.nan, 0.0)

        self.assertFalse(result.algorithm_target_valid)
        self.assertEqual(result.algorithm_target_supply_flow, 30.0)
        self.assertEqual(result.algorithm_target_status, "HOLD_LAST_INVALID_INPUT")

    def test_startup_fallback_is_explicit_setpoint_not_actual_flow(self):
        publisher = ContinuousTargetPublisher(startup_setpoint_target=28.0)

        result = publisher.publish(None, None, inputs_valid=False)

        self.assertFalse(result.algorithm_target_valid)
        self.assertEqual(
            result.algorithm_target_status,
            "STARTUP_FALLBACK_INVALID_INPUT",
        )
        self.assertEqual(result.algorithm_target_supply_flow, 28.0)
        self.assertIsNone(publisher.last_valid_algorithm_target)
        self.assertEqual(
            result.metadata["startup_fallback_source"],
            "EXPLICIT_SETPOINT_TARGET",
        )

    def test_no_valid_target_is_explicit_at_cold_start(self):
        publisher = ContinuousTargetPublisher()

        result = publisher.publish(None, None, inputs_valid=False)

        self.assertFalse(result.algorithm_target_valid)
        self.assertEqual(result.algorithm_target_status, "NO_VALID_TARGET")
        self.assertIsNone(result.algorithm_target_supply_flow)

    def test_actual_flow_is_not_part_of_publish_api(self):
        parameters = inspect.signature(ContinuousTargetPublisher.publish).parameters
        self.assertNotIn("actual_flow", parameters)
        self.assertNotIn("actual_supply_flow", parameters)
        self.assertNotIn("actual_supply_flow_feedback", parameters)

    def test_counterfactual_shadow_semantics_are_preserved(self):
        publisher = ContinuousTargetPublisher()
        result = publisher.publish(
            46.0,
            0.0,
            replay_semantics=COUNTERFACTUAL_SHADOW,
        )

        self.assertEqual(result.replay_semantics, COUNTERFACTUAL_SHADOW)
        self.assertEqual(result.algorithm_target_supply_flow, 46.0)

    def test_restore_rejects_corrupt_out_of_range_target(self):
        upper = float(target_supply_flow_contract()["maximum"])
        publisher = ContinuousTargetPublisher()
        with self.assertRaises(ValueError):
            publisher.restore_last_valid_algorithm_target(upper + 1.0)

    def test_custom_hard_bounds_are_supported_for_component_tests(self):
        # Component-level tests may explicitly exercise arbitrary bounds. The
        # formal production runtime separately rejects bounds that diverge from
        # PLANT_CONFIG.
        publisher = ContinuousTargetPublisher(
            ContinuousTargetConfig(
                hard_min_supply_flow=10.0,
                hard_max_supply_flow=60.0,
            )
        )

        result = publisher.publish(59.0, 5.0)
        self.assertEqual(result.algorithm_target_supply_flow, 60.0)
        self.assertTrue(result.hard_clipped)


if __name__ == "__main__":
    unittest.main()
