import unittest

from system.model.map_control.condition_model.online_condition_policy_bridge import (
    SlurryPolicyOnlineBridge,
)


class Scheme2LegacyPrefixCompatibilityTest(unittest.TestCase):
    def test_legacy_output_prefix_config_cannot_replace_mfac_namespace(self):
        bridge = SlurryPolicyOnlineBridge(
            {
                "enabled": True,
                "initialize_on_start": True,
                # Historical condition_config value.  It must now mean legacy
                # compatibility, not the canonical second-module namespace.
                "output_prefix": "slurry_policy_",
                "target_column": "outlet_so2_target",
                "failure_mode": "RAISE",
            },
            initial_active_pointer={"integrated_version": "v001"},
        )
        self.assertEqual(bridge.output_prefix, "mfac_")
        self.assertEqual(bridge.legacy_output_prefix, "slurry_policy_")

        result = bridge.process(
            {
                "date": "2026-08-27T09:40:00+08:00",
                "condition_snapshot_version": "v001",
                "condition_label": "17",
                "base_condition_id": "17",
                "grid_id": "P1-S1",
                "policy_region_id": "R_P1_S1",
                "yyq_SO2": 2000.0,
                "yyq_LL": 2200000.0,
                "xstshsjy_MD": 1200.0,
                "xstshsjy_LL": 69.0,
                "xstjy_PH": 6.2,
                "jyq_SO2": 50.0,
                "outlet_so2_target": 20.0,
            }
        )
        self.assertIn("mfac_algorithm_target_supply_flow", result)
        self.assertIn("slurry_policy_algorithm_target_supply_flow", result)
        self.assertEqual(
            result["mfac_algorithm_target_supply_flow"],
            result["slurry_policy_algorithm_target_supply_flow"],
        )
        self.assertEqual(result["second_module_type"], "MFAC")
        self.assertEqual(result["slurry_policy_backend"], "MFAC")


if __name__ == "__main__":
    unittest.main()
