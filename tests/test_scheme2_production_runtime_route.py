import unittest

from system.data_opts.DataClientMain import DataClientMain, ProcessForMapConsole
from system.model.Process4MapControlMFAC import (
    ProcessForMapConsole as UnifiedMFACProcessForMapConsole,
)


class Scheme2ProductionRuntimeRouteTest(unittest.TestCase):
    def test_data_client_uses_unified_mfac_process4_runtime(self):
        self.assertIs(ProcessForMapConsole, UnifiedMFACProcessForMapConsole)
        self.assertEqual(
            ProcessForMapConsole.__module__,
            "system.model.Process4MapControlMFAC",
        )

    def test_data_client_constructor_resolves_same_runtime_class(self):
        # Do not instantiate DataClientMain here because the production P4PC
        # constructor starts database and worker-thread infrastructure.  The
        # imported class identity is the startup routing contract.
        self.assertIn("ProcessForMapConsole", DataClientMain.__init__.__code__.co_names)
        self.assertIs(
            DataClientMain.__init__.__globals__["ProcessForMapConsole"],
            UnifiedMFACProcessForMapConsole,
        )


if __name__ == "__main__":
    unittest.main()
