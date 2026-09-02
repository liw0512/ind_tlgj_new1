import unittest
from pathlib import Path

from system.data_opts.DataClientMain import DataClientMain, ProcessForMapConsole
from system.model.Process4MapControl import (
    ProcessForMapConsole as UnifiedProcessForMapConsole,
)


class Scheme2ProductionRuntimeRouteTest(unittest.TestCase):
    def test_data_client_uses_single_process4_runtime(self):
        self.assertIs(ProcessForMapConsole, UnifiedProcessForMapConsole)
        self.assertEqual(
            ProcessForMapConsole.__module__,
            "system.model.Process4MapControl",
        )

    def test_legacy_mfac_process4_entry_is_removed(self):
        project_root = Path(__file__).resolve().parents[1]
        self.assertFalse(
            (project_root / "system" / "model" / "Process4MapControlMFAC.py").exists()
        )

    def test_data_client_constructor_resolves_same_runtime_class(self):
        # Do not instantiate DataClientMain here because the production P4PC
        # constructor starts database and worker-thread infrastructure. The
        # imported class identity is the startup routing contract.
        self.assertIn("ProcessForMapConsole", DataClientMain.__init__.__code__.co_names)
        self.assertIs(
            DataClientMain.__init__.__globals__["ProcessForMapConsole"],
            UnifiedProcessForMapConsole,
        )


if __name__ == "__main__":
    unittest.main()