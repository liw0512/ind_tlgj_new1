import copy
import unittest
from pathlib import Path

from system.model.config.mfac_core_bridge_config import MFAC_CORE_BRIDGE_CONFIG
from system.model.config.mfac_plant_contract import (
    ph_arbitration_plant_values,
    primary_tower_contract,
    target_supply_flow_contract,
)
from system.model.config.plant_config import PLANT_CONFIG
from system.model.config.standard_fields import TARGET_SO2_COLUMN
from system.model.map_control.mfac_model.continuous_target import ContinuousTargetConfig
from system.model.map_control.mfac_model.mfac_primary_config import (
    MFAC_ACTIVE_VERSION_FILE,
    MFAC_OUTPUT_ROOT,
    MFAC_PRIMARY_ARTIFACT_CONFIG,
    MFAC_RUNTIME_DIR,
)
from system.model.map_control.mfac_model.primary_runtime import MFACUnifiedRuntimePolicy
from system.model.map_control.mfac_model.qbase.dynamic_qbase_calculator import (
    DynamicQbaseCalculator,
)


class Scheme2SingleSourceParameterTest(unittest.TestCase):
    def test_artifact_paths_have_one_canonical_source(self):
        self.assertEqual(
            Path(MFAC_CORE_BRIDGE_CONFIG["mfac_output_root"]),
            MFAC_OUTPUT_ROOT,
        )
        self.assertEqual(
            Path(MFAC_CORE_BRIDGE_CONFIG["active_version_file"]),
            MFAC_ACTIVE_VERSION_FILE,
        )
        self.assertEqual(
            Path(MFAC_PRIMARY_ARTIFACT_CONFIG["output_root"]),
            MFAC_OUTPUT_ROOT,
        )
        self.assertEqual(
            Path(MFAC_PRIMARY_ARTIFACT_CONFIG["active_version_file"]),
            MFAC_ACTIVE_VERSION_FILE,
        )
        self.assertEqual(
            Path(MFAC_PRIMARY_ARTIFACT_CONFIG["runtime_dir"]),
            MFAC_RUNTIME_DIR,
        )
        self.assertEqual(
            Path(MFAC_PRIMARY_ARTIFACT_CONFIG["runtime"]["runtime_dir"]),
            MFAC_RUNTIME_DIR,
        )

    def test_artifact_config_does_not_duplicate_runtime_permission_switches(self):
        for field in ("learn_enabled", "residual_enabled", "dcs_write_enabled"):
            self.assertNotIn(field, MFAC_PRIMARY_ARTIFACT_CONFIG)
        runtime = MFAC_PRIMARY_ARTIFACT_CONFIG["runtime"]
        self.assertFalse(runtime["learning_enabled"])
        self.assertFalse(runtime["residual_control_enabled"])
        self.assertFalse(runtime["dcs_write_enabled"])

    def test_continuous_target_defaults_come_from_plant_contract(self):
        plant = target_supply_flow_contract()
        target = ContinuousTargetConfig()
        self.assertEqual(target.hard_min_supply_flow, plant["minimum"])
        self.assertEqual(target.hard_max_supply_flow, plant["maximum"])

    def test_primary_runtime_uses_plant_feedback_tower_and_ph_fields(self):
        target = target_supply_flow_contract()
        tower = primary_tower_contract()
        policy = MFACUnifiedRuntimePolicy(
            active_pointer={"integrated_version": "v001"}
        )
        status = policy.status()
        self.assertEqual(
            status["actual_flow_feedback_column"], target["feedback_column"]
        )
        self.assertEqual(status["primary_tower_id"], tower["tower_id"])
        self.assertEqual(status["ph_column"], tower["ph_column"])
        self.assertEqual(status["target_hard_min"], target["minimum"])
        self.assertEqual(status["target_hard_max"], target["maximum"])

    def test_feedback_column_must_match_tower_supply_flow_mapping(self):
        plant = copy.deepcopy(PLANT_CONFIG)
        plant["scheme2"]["target_supply_flow"]["feedback_column"] = "wrong_flow"
        with self.assertRaises(ValueError):
            primary_tower_contract(plant)

    def test_ph_arbitration_plant_values_are_derived_from_primary_tower(self):
        tower = primary_tower_contract()
        ph = ph_arbitration_plant_values()
        self.assertEqual(ph["safe_min"], tower["safe_min"])
        self.assertEqual(ph["safe_max"], tower["safe_max"])
        self.assertEqual(ph["operating_min"], tower["operating_min"])
        self.assertEqual(ph["operating_max"], tower["operating_max"])
        self.assertEqual(ph["guard_band"], tower["guard_band"])

    def test_qbase_rejects_second_target_field_authority(self):
        plant = copy.deepcopy(PLANT_CONFIG)
        plant["scheme2"]["qbase"]["target_so2_column"] = "other_target"
        tower_id = primary_tower_contract(plant)["tower_id"]
        with self.assertRaises(ValueError):
            DynamicQbaseCalculator(tower_id, plant_config=plant)
        self.assertEqual(TARGET_SO2_COLUMN, "outlet_so2_target")


if __name__ == "__main__":
    unittest.main()
