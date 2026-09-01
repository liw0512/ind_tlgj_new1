# -*- coding: utf-8 -*-
"""Lock Scheme2's pure first module to the reviewed Scheme1 source baseline.

The first module is canonical from:
  liw0512/ind_tlgj_new@0d99e18262dc2b1bf9fb03464de5eb4eb4166d44

Only the module1 -> module2 integration boundary is allowed to differ because
Scheme2 uses MFAC instead of the retired Scheme1 slurry-policy backend.
"""

import hashlib
import unittest
from pathlib import Path

from system.model.config.mfac_core_bridge_config import MFAC_CORE_BRIDGE_CONFIG
from system.model.config.mfac_paths import MFAC_ACTIVE_VERSION_FILE
from system.model.config.plant_config import PLANT_CONFIG
from system.model.map_control.condition_model.condition_config import (
    CONDITION_AXES,
    DEFAULT_MERGE_CONFIG,
    DEFAULT_ONLINE_CONFIG,
    INITIAL_CONDITION_TRAIN_CONFIG,
    INCREMENTAL_CONDITION_TRAIN_CONFIG,
    ONLINE_CONDITION_CLASSIFY_CONFIG,
)


SCHEME1_SOURCE_REPOSITORY = "liw0512/ind_tlgj_new"
SCHEME1_BASELINE_COMMIT = "0d99e18262dc2b1bf9fb03464de5eb4eb4166d44"

# Exact Git blob ids from Scheme1's condition_model tree at the baseline commit.
# These files are pure first-module implementation and therefore must not drift
# independently inside Scheme2.
SCHEME1_PURE_MODULE1_BLOBS = {
    "__init__.py": "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391",
    "auto_merge_manager.py": "44b4a6e065818650d4440e5e1ef8f6e43776bbd5",
    "condition_merger.py": "79ccff0a66816fe9ff870fd087ec0904b2c0e62e",
    "condition_schema.py": "c9102fd9ab8fafbb8d047b8319c24ad183d74f22",
    "grid_definition.py": "11c303c9ebce1f411a0197a38ba95d2fa9c0ff47",
    "grid_range_analyzer.py": "bc5eaca8a4aa2c4601bf6d9d77e6d6778220be04",
    "incremental_condition_updater.py": "30ff162cedb5d4ae44d63289089bdc0b8201b1eb",
    "initial_condition_builder.py": "d71da2a5a4e8f8b71e3396c3b62f22551258cd2a",
    "online_condition_classifier.py": "f13c1d49b3260d61832afe48180760d74bcb4377",
    "snapshot_io.py": "64200b8ba0270b21fc1d1707ee0d1a455e65500d",
}

# These source files intentionally require a Scheme2 adapter rather than a
# byte-for-byte copy because they cross the second-module boundary.
SCHEME2_ADAPTED_BOUNDARY_FILES = {
    "condition_config.py",
    "integrated_online_example.py",
    "integrated_version_manager.py",
    "online_condition_policy_bridge.py",
    "README.md",
}


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload).hexdigest()


class Scheme2FirstModuleScheme1ParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module_dir = (
            Path(__file__).resolve().parents[1]
            / "system"
            / "model"
            / "map_control"
            / "condition_model"
        )

    def test_pure_first_module_files_are_exact_scheme1_blobs(self):
        mismatches = {}
        for relative_path, expected_sha in SCHEME1_PURE_MODULE1_BLOBS.items():
            path = self.module_dir / relative_path
            self.assertTrue(path.is_file(), f"missing canonical module1 file: {path}")
            actual_sha = _git_blob_sha(path)
            if actual_sha != expected_sha:
                mismatches[relative_path] = (expected_sha, actual_sha)
        self.assertEqual(
            mismatches,
            {},
            "Scheme2 pure first-module code drifted from Scheme1 baseline",
        )

    def test_condition_merge_parameters_match_scheme1_baseline(self):
        expected = {
            "enabled": True,
            "mode": "evidence_only",
            "min_observed_samples": 10,
            "min_mature_samples": 30,
            "min_auto_merge_samples": 100,
            "min_auto_confirm_samples": 300,
            "min_common_state_samples": 10,
            "min_risk_samples": 30,
            "min_metric_coverage_ratio": 0.80,
            "min_consecutive_pass_snapshots": 3,
            "min_new_samples_per_member_for_confirmation": 10,
            "max_auto_region_cells": 8,
            "max_liquid_gas_relative_difference": 0.15,
            "max_pump_distribution_distance": 0.25,
            "max_risk_rate_difference": 0.10,
        }
        self.assertEqual(DEFAULT_MERGE_CONFIG, expected)

    def test_online_condition_parameters_match_scheme1_baseline(self):
        self.assertEqual(
            DEFAULT_ONLINE_CONFIG,
            {
                "stability_mode": "MAJORITY",
                "stability_window_size": 6,
                "majority_tie_policy": "KEEP_LAST_STABLE",
                "allow_provisional_region_fallback": True,
            },
        )

    def test_condition_axes_still_come_from_current_plant_contract(self):
        # Scheme1's module1 design derives plant facts from central plant_config.
        # Migration must preserve that design while using Scheme2's actual site.
        self.assertEqual(CONDITION_AXES, PLANT_CONFIG["condition_axes"])

    def test_first_module_training_interface_matches_scheme1_contract(self):
        self.assertTrue(
            INITIAL_CONDITION_TRAIN_CONFIG["output_csv_path"].endswith(
                "Initial_train_after_condition.csv"
            )
        )
        self.assertTrue(
            INCREMENTAL_CONDITION_TRAIN_CONFIG["output_csv_path"].endswith(
                "Incremental_train_after_condition.csv"
            )
        )
        self.assertTrue(
            MFAC_CORE_BRIDGE_CONFIG["condition_initial_script"].endswith(
                "initial_condition_builder.py"
            )
        )
        self.assertTrue(
            MFAC_CORE_BRIDGE_CONFIG["condition_incremental_script"].endswith(
                "incremental_condition_updater.py"
            )
        )
        self.assertEqual(
            MFAC_CORE_BRIDGE_CONFIG["initial_condition_output_csv"],
            INITIAL_CONDITION_TRAIN_CONFIG["output_csv_path"],
        )
        self.assertEqual(
            MFAC_CORE_BRIDGE_CONFIG["incremental_condition_output_csv"],
            INCREMENTAL_CONDITION_TRAIN_CONFIG["output_csv_path"],
        )

    def test_scheme2_integration_boundary_remains_owned_by_mfac(self):
        integrated = ONLINE_CONDITION_CLASSIFY_CONFIG["slurry_policy_online"][
            "integrated_version"
        ]
        # Reload cadence is owned by Scheme2's integrated manager, not duplicated
        # inside the migrated first-module config.
        self.assertNotIn("reload_check_interval_seconds", integrated)
        self.assertEqual(
            integrated["active_version_file"], str(MFAC_ACTIVE_VERSION_FILE)
        )
        self.assertEqual(MFAC_CORE_BRIDGE_CONFIG["second_module_backend"], "MFAC")


if __name__ == "__main__":
    unittest.main()
