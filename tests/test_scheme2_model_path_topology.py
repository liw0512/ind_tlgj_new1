from __future__ import annotations

import unittest

from system.model.config.mfac_paths import (
    CONDITION_ROOT,
    CONDITION_SNAPSHOTS_DIR,
    FAST_OUTPUT_ROOT,
    FAST_ROOT,
    FAST_RUNTIME_ROOT,
    MAP_CONTROL_ROOT,
    MFAC_DIAGNOSTICS_ROOT,
    MFAC_EVIDENCE_ROLE_V2_1_DIR,
    MFAC_OUTPUT_ROOT,
    MFAC_ROOT,
    MFAC_SNAPSHOTS_DIR,
    PROJECT_ROOT,
)
from system.model.map_control.fast_change_mode.fast_change_history_manager import (
    DEFAULT_OUTPUT_ROOT as FAST_MANAGER_OUTPUT_ROOT,
    DEFAULT_RUNTIME_ROOT as FAST_MANAGER_RUNTIME_ROOT,
)


class Scheme2ModelPathTopologyTest(unittest.TestCase):
    def test_three_core_modules_live_under_map_control(self):
        expected_map_control = PROJECT_ROOT / "system" / "model" / "map_control"
        self.assertEqual(MAP_CONTROL_ROOT, expected_map_control)
        self.assertEqual(CONDITION_ROOT, expected_map_control / "condition_model")
        self.assertEqual(MFAC_ROOT, expected_map_control / "mfac_model")
        self.assertEqual(FAST_ROOT, expected_map_control / "fast_change_mode")

    def test_generated_artifacts_stay_inside_own_module(self):
        self.assertEqual(CONDITION_SNAPSHOTS_DIR, CONDITION_ROOT / "snapshots")
        self.assertEqual(MFAC_OUTPUT_ROOT, MFAC_ROOT / "mfac_model_output")
        self.assertEqual(MFAC_SNAPSHOTS_DIR, MFAC_OUTPUT_ROOT / "snapshots")
        self.assertEqual(MFAC_DIAGNOSTICS_ROOT, MFAC_OUTPUT_ROOT / "diagnostics")
        self.assertEqual(
            MFAC_EVIDENCE_ROLE_V2_1_DIR,
            MFAC_DIAGNOSTICS_ROOT / "historical_evidence_role_v2_1",
        )
        self.assertEqual(FAST_OUTPUT_ROOT, FAST_ROOT / "fast_change_output")
        self.assertEqual(FAST_RUNTIME_ROOT, FAST_ROOT / "fast_change_runtime")

    def test_fast_history_manager_uses_module_local_defaults(self):
        self.assertEqual(FAST_MANAGER_OUTPUT_ROOT, FAST_OUTPUT_ROOT)
        self.assertEqual(FAST_MANAGER_RUNTIME_ROOT, FAST_RUNTIME_ROOT)
        self.assertNotEqual(FAST_MANAGER_OUTPUT_ROOT.parent, PROJECT_ROOT / "files")

    def test_canonical_paths_never_use_temporary_scheme2_workspace(self):
        canonical_paths = (
            CONDITION_ROOT,
            CONDITION_SNAPSHOTS_DIR,
            MFAC_ROOT,
            MFAC_OUTPUT_ROOT,
            MFAC_SNAPSHOTS_DIR,
            MFAC_DIAGNOSTICS_ROOT,
            MFAC_EVIDENCE_ROLE_V2_1_DIR,
            FAST_ROOT,
            FAST_OUTPUT_ROOT,
            FAST_RUNTIME_ROOT,
        )
        for path in canonical_paths:
            self.assertNotIn("_scheme2_work", path.parts)


if __name__ == "__main__":
    unittest.main()
