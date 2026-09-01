# -*- coding: utf-8 -*-
"""Lock Scheme2's full first module to the reviewed Scheme1 source baseline.

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
SCHEME1_PURE_CONDITION_BLOBS = {
    "__init__.py": "e69de29bb2d1d643b8b29ae775ad8c2e48c5391",
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

# FAST_CHANGE is a direct dependency owned by OnlineConditionPolicyPipeline and
# therefore belongs to the canonical first-module runtime semantics as well.
SCHEME1_FAST_BLOBS = {
    "README.md": "b3a427b4ecef9b1c500f1d2b4cb2121cd819a7c2",
    "__init__.py": "191d659216e01facd400a952994ea592c462264f",
    "fast_change_config.py": "d46ba12e456379c774130d7cc301ba47b4f3b84d",
    "fast_change_history_manager.py": "1a0d72746097e5b0efecd7498ac5145b9e4a4ece",
    "fast_change_mode_detector.py": "f87e98a55150ceb9d44f5232c239bcd934a71a14",
}

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


def _assert_blob_map(testcase, root: Path, expected_map, label: str):
    mismatches = {}
    for relative_path, expected_sha in expected_map.items():
        path = root / relative_path
        testcase.assertTrue(path.is_file(), f"missing canonical {label} file: {path}")
        actual_sha = _git_blob_sha(path)
        if actual_sha != expected_sha:
            mismatches[relative_path] = (expected_sha, actual_sha)
    testcase.assertEqual(mismatches, {}, f"Scheme2 {label} drifted from Scheme1 baseline")


class Scheme2FirstModuleScheme1ParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        map_control_root = (
            Path(__file__).resolve().parents[1]
            / "system"
            / "model"
            / "map_control"
        )
        cls.condition_dir = map_control_root / "condition_model"
        cls.fast_dir = map_control_root / "fast_change_mode"

    def test_pure_condition_files_are_exact_scheme1_blobs(self):
        _assert_blob_map(
            self,
            self.condition_dir,
            SCHEME1_PURE_CONDITION_BLOBS,
            "condition-model source",
        )

    def test_fast_dependency_files_are_exact_scheme1_blobs(self):
        _assert_blob_map(
            self,
            self.fast_dir,
            SCHEME1_FAST_BLOBS,
            "FAST first-module dependency",
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
        self.assertNotIn("reload_check_interval_seconds", integrated)
        self.assertEqual(
            integrated["active_version_file"], str(MFAC_ACTIVE_VERSION_FILE)
        )
        self.assertEqual(MFAC_CORE_BRIDGE_CONFIG["second_module_backend"], "MFAC")


if __name__ == "__main__":
    unittest.main()
