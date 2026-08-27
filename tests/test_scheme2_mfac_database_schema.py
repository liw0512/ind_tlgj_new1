import unittest

from system.model.config.mfac_database_schema import (
    MFAC_MODEL_RESULT_FIELD_TYPES,
    mfac_model_result_columns,
)


class Scheme2MFACDatabaseSchemaTest(unittest.TestCase):
    def test_canonical_mfac_fields_are_persisted(self):
        columns = set(mfac_model_result_columns())
        required = {
            "mfac_loaded_version",
            "mfac_runtime_mode",
            "mfac_qbase",
            "mfac_qbase_effective",
            "mfac_residual_mfac_hold",
            "mfac_algorithm_target_supply_flow",
            "mfac_runtime_cycle",
            "mfac_learn_enabled",
            "mfac_residual_enabled",
            "mfac_dcs_write_enabled",
            "mfac_runtime_config_status",
            "mfac_runtime_configured",
            "mfac_runtime_config_version",
            "mfac_runtime_config_missing_fields",
            "second_module_type",
        }
        self.assertTrue(required.issubset(columns))
        self.assertEqual(
            MFAC_MODEL_RESULT_FIELD_TYPES["mfac_runtime_cycle"],
            "jsonb",
        )
        self.assertEqual(
            MFAC_MODEL_RESULT_FIELD_TYPES["mfac_runtime_config_missing_fields"],
            "jsonb",
        )
        self.assertEqual(
            MFAC_MODEL_RESULT_FIELD_TYPES["mfac_algorithm_target_supply_flow"],
            "float8",
        )

    def test_legacy_columns_remain_only_for_non_breaking_migration(self):
        columns = set(mfac_model_result_columns())
        self.assertIn("slurry_policy_action_family", columns)
        self.assertIn("slurry_policy_decision_status", columns)
        self.assertIn("mfac_action_family", columns)
        self.assertIn("mfac_decision_status", columns)


if __name__ == "__main__":
    unittest.main()
