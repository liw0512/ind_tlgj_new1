import unittest
from pathlib import Path

from system.model.config.mfac_core_bridge_config import MFAC_CORE_BRIDGE_CONFIG
from system.model.config.slurry_core_bridge_config import SLURRY_CORE_BRIDGE_CONFIG
from system.model.map_control.condition_model.online_condition_policy_bridge import (
    SlurryPolicyOnlineBridge,
)
from system.model.map_control.mfac_model.historical_episode import (
    extract_decision_episodes,
    run_episode_pipeline,
)


class Scheme2LegacySecondModuleReplacementTest(unittest.TestCase):
    def test_legacy_second_module_source_directory_is_removed(self):
        project_root = Path(__file__).resolve().parents[1]
        legacy = (
            project_root
            / "system"
            / "model"
            / "map_control"
            / "slurry_policy_model"
        )
        self.assertFalse(legacy.exists())

    def test_mfac_lifecycle_config_is_canonical(self):
        for key in (
            "mfac_initial_script",
            "mfac_incremental_script",
            "mfac_activate_script",
            "mfac_config",
            "mfac_output_root",
            "active_version_file",
        ):
            value = str(MFAC_CORE_BRIDGE_CONFIG[key]).replace("\\", "/")
            self.assertIn("/mfac_model/", value)
            self.assertNotIn("/slurry_policy_model/", value)
        self.assertEqual(MFAC_CORE_BRIDGE_CONFIG["second_module_backend"], "MFAC")
        self.assertNotIn("slurry_policy_initial_script", MFAC_CORE_BRIDGE_CONFIG)

    def test_legacy_lifecycle_config_is_only_a_mfac_compatibility_view(self):
        mapping = {
            "slurry_policy_initial_script": "mfac_initial_script",
            "slurry_policy_incremental_script": "mfac_incremental_script",
            "slurry_policy_activate_script": "mfac_activate_script",
            "slurry_policy_config": "mfac_config",
            "slurry_policy_output_root": "mfac_output_root",
        }
        for legacy, canonical in mapping.items():
            self.assertEqual(
                SLURRY_CORE_BRIDGE_CONFIG[legacy],
                MFAC_CORE_BRIDGE_CONFIG[canonical],
            )
        self.assertEqual(SLURRY_CORE_BRIDGE_CONFIG["second_module_backend"], "MFAC")

    def test_compatibility_bridge_backend_is_mfac(self):
        bridge = SlurryPolicyOnlineBridge(
            {
                "enabled": True,
                "initialize_on_start": True,
                "output_prefix": "mfac_",
                "legacy_output_prefix": "slurry_policy_",
                "emit_legacy_compatibility": True,
                "target_column": "outlet_so2_target",
                "failure_mode": "RAISE",
            },
            initial_active_pointer={
                "integrated_version": "v001",
                "condition": {"version": "v001"},
                "mfac": {"version": "v001"},
            },
        )
        result = bridge.process(
            {
                "date": "2026-08-26T18:00:00+08:00",
                "condition_snapshot_version": "v001",
                "condition_label": "17",
                "base_condition_id": "17",
                "grid_id": "P1-S1",
                "policy_region_id": "R_P1_S1",
                "yyq_SO2": 2000.0,
                "yyq_LL": 2200000.0,
                "xstshsjy_MD": 1200.0,
                "xstjy_PH": 6.2,
                "outlet_so2_target": 20.0,
                # Real flow is evidence only and must not become target fallback.
                "xstshsjy_LL": 69.0,
            }
        )
        self.assertEqual(result["second_module_type"], "MFAC")
        self.assertEqual(result["mfac_model_type"], "MFAC")
        self.assertEqual(result["slurry_policy_backend"], "MFAC")
        self.assertTrue(result["slurry_policy_deprecated_compat"])
        self.assertFalse(result["second_module_dcs_write_enabled"])
        self.assertFalse(
            result["mfac_debug"]["actual_flow_used_as_algorithm_target"]
        )
        self.assertNotEqual(
            result["second_module_algorithm_target_supply_flow"],
            69.0,
        )

    def test_historical_episode_engine_is_owned_by_mfac(self):
        self.assertTrue(callable(extract_decision_episodes))
        self.assertTrue(callable(run_episode_pipeline))


if __name__ == "__main__":
    unittest.main()
