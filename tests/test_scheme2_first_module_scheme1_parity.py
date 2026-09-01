# -*- coding: utf-8 -*-
"""Lock Scheme2's first-module algorithm parameters to the Scheme1 baseline."""

import unittest

from system.model.config.mfac_paths import MFAC_ACTIVE_VERSION_FILE
from system.model.map_control.condition_model.condition_config import (
    DEFAULT_MERGE_CONFIG,
    DEFAULT_ONLINE_CONFIG,
    ONLINE_CONDITION_CLASSIFY_CONFIG,
)


SCHEME1_BASELINE_COMMIT = "0d99e18262dc2b1bf9fb03464de5eb4eb4166d44"


class Scheme2FirstModuleScheme1ParityTests(unittest.TestCase):
    def test_condition_merge_parameters_match_scheme1_baseline(self):
        self.assertEqual(DEFAULT_MERGE_CONFIG["enabled"], True)
        self.assertEqual(DEFAULT_MERGE_CONFIG["mode"], "evidence_only")
        self.assertEqual(DEFAULT_MERGE_CONFIG["min_observed_samples"], 10)
        self.assertEqual(DEFAULT_MERGE_CONFIG["min_mature_samples"], 30)
        self.assertEqual(DEFAULT_MERGE_CONFIG["min_auto_merge_samples"], 100)
        self.assertEqual(DEFAULT_MERGE_CONFIG["min_auto_confirm_samples"], 300)
        self.assertEqual(DEFAULT_MERGE_CONFIG["min_common_state_samples"], 10)
        self.assertEqual(DEFAULT_MERGE_CONFIG["min_risk_samples"], 30)
        self.assertEqual(DEFAULT_MERGE_CONFIG["min_metric_coverage_ratio"], 0.80)
        self.assertEqual(DEFAULT_MERGE_CONFIG["min_consecutive_pass_snapshots"], 3)
        self.assertEqual(
            DEFAULT_MERGE_CONFIG["min_new_samples_per_member_for_confirmation"], 10
        )
        self.assertEqual(DEFAULT_MERGE_CONFIG["max_auto_region_cells"], 8)
        self.assertEqual(
            DEFAULT_MERGE_CONFIG["max_liquid_gas_relative_difference"], 0.15
        )
        self.assertEqual(DEFAULT_MERGE_CONFIG["max_pump_distribution_distance"], 0.25)
        self.assertEqual(DEFAULT_MERGE_CONFIG["max_risk_rate_difference"], 0.10)

    def test_online_condition_parameters_match_scheme1_baseline(self):
        self.assertEqual(DEFAULT_ONLINE_CONFIG["stability_mode"], "MAJORITY")
        self.assertEqual(DEFAULT_ONLINE_CONFIG["stability_window_size"], 6)
        self.assertEqual(
            DEFAULT_ONLINE_CONFIG["majority_tie_policy"], "KEEP_LAST_STABLE"
        )
        self.assertTrue(DEFAULT_ONLINE_CONFIG["allow_provisional_region_fallback"])

    def test_scheme2_integration_boundary_remains_owned_by_mfac(self):
        integrated = ONLINE_CONDITION_CLASSIFY_CONFIG["slurry_policy_online"][
            "integrated_version"
        ]
        self.assertNotIn("reload_check_interval_seconds", integrated)
        self.assertEqual(
            integrated["active_version_file"], str(MFAC_ACTIVE_VERSION_FILE)
        )


if __name__ == "__main__":
    unittest.main()
