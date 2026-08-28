import unittest

from system.model.Process4MapControl import ProcessForMapConsole
from system.model.config.mfac_paths import MFAC_ACTIVE_VERSION_FILE
from system.model.config.mfac_training_lifecycle import (
    INCREMENTAL_OFFLINE_TRAINING_DAYS,
    INITIAL_OFFLINE_TRAINING_DAYS,
    training_days_for_mode,
)
from system.model.config.process4map_config import PROCESS4MAP_CONFIG, TrainingConfig
from system.model.map_control.condition_model.condition_config import (
    ONLINE_CONDITION_CLASSIFY_CONFIG,
)
from system.model.map_control.mfac_model.offline_training_config import (
    OFFLINE_ONLINE_LIFECYCLE_CONTRACT,
)


class Scheme2TrainingConfigSingleSourceTest(unittest.TestCase):
    def test_canonical_lifecycle_days_are_7_initial_and_3_incremental(self):
        self.assertEqual(INITIAL_OFFLINE_TRAINING_DAYS, 7)
        self.assertEqual(INCREMENTAL_OFFLINE_TRAINING_DAYS, 3)
        self.assertEqual(training_days_for_mode("INITIAL"), 7)
        self.assertEqual(training_days_for_mode("INCREMENTAL"), 3)

    def test_redundant_values_are_computed_views_not_dataclass_fields(self):
        redundant_names = {
            "initial_training_days",
            "incremental_training_days",
            "incremental_trigger_interval_days",
            "initial_minimum_records",
            "incremental_minimum_records",
            "initial_database_record_limit",
            "incremental_database_record_limit",
        }
        self.assertTrue(redundant_names.isdisjoint(TrainingConfig.__dataclass_fields__))

        cfg = PROCESS4MAP_CONFIG.training
        self.assertEqual(cfg.initial_training_days, 7)
        self.assertEqual(cfg.incremental_training_days, 3)
        self.assertEqual(cfg.incremental_trigger_interval_days, 3)
        self.assertEqual(cfg.target_records_for_days(7), 60_480)
        self.assertEqual(cfg.initial_minimum_records, 54_432)
        self.assertEqual(cfg.target_records_for_days(3), 25_920)
        self.assertEqual(cfg.incremental_minimum_records, 23_328)
        self.assertEqual(cfg.initial_database_record_limit, 0)
        self.assertEqual(cfg.incremental_database_record_limit, 0)

    def test_process4_consumes_the_derived_values_without_second_override(self):
        process = ProcessForMapConsole.__new__(ProcessForMapConsole)
        process.process_config = PROCESS4MAP_CONFIG

        initial = ProcessForMapConsole._training_mode_settings(process, "initial")
        incremental = ProcessForMapConsole._training_mode_settings(
            process, "incremental"
        )

        self.assertEqual(initial["days"], 7)
        self.assertEqual(initial["minimum_records"], 54_432)
        self.assertEqual(initial["database_record_limit"], 0)
        self.assertEqual(incremental["days"], 3)
        self.assertEqual(incremental["minimum_records"], 23_328)
        self.assertEqual(incremental["database_record_limit"], 0)

    def test_offline_contract_uses_same_days_and_has_no_ambiguous_periodic_value(self):
        self.assertEqual(
            OFFLINE_ONLINE_LIFECYCLE_CONTRACT["initial_training_days"], 7
        )
        self.assertEqual(
            OFFLINE_ONLINE_LIFECYCLE_CONTRACT["incremental_training_days"], 3
        )
        self.assertNotIn(
            "periodic_offline_retrain_days", OFFLINE_ONLINE_LIFECYCLE_CONTRACT
        )
        self.assertFalse(
            OFFLINE_ONLINE_LIFECYCLE_CONTRACT["online_update_is_periodic"]
        )
        self.assertEqual(
            OFFLINE_ONLINE_LIFECYCLE_CONTRACT["online_update_trigger"],
            "VALID_COMPLETED_CAUSAL_RESPONSE_EVENT",
        )

    def test_condition_module_uses_canonical_mfac_active_path_without_reload_override(self):
        integrated = ONLINE_CONDITION_CLASSIFY_CONFIG[
            "slurry_policy_online"
        ]["integrated_version"]
        self.assertEqual(
            integrated["active_version_file"],
            str(MFAC_ACTIVE_VERSION_FILE),
        )
        self.assertNotIn("reload_check_interval_seconds", integrated)


if __name__ == "__main__":
    unittest.main()
