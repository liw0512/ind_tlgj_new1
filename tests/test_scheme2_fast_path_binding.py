import inspect
import unittest

from system.model.Process4MapControl import ProcessForMapConsole
from system.model.config.fast_change_path_binding import (
    FAST_PATH_BINDING_VERSION,
    bind_fast_change_artifact_paths,
)
from system.model.config.mfac_paths import FAST_OUTPUT_ROOT, FAST_RUNTIME_ROOT
from system.model.map_control.fast_change_mode import fast_change_history_manager
from system.model.map_control.fast_change_mode.fast_change_history_manager import (
    FastChangeHistoryManager,
)


class Scheme2FastPathBindingTest(unittest.TestCase):
    def test_binding_redirects_locked_fast_defaults_to_module_local_paths(self):
        previous_output = fast_change_history_manager.DEFAULT_OUTPUT_ROOT
        previous_runtime = fast_change_history_manager.DEFAULT_RUNTIME_ROOT
        try:
            result = bind_fast_change_artifact_paths()
            manager = FastChangeHistoryManager(persist_runtime=False)
            self.assertEqual(manager.output_root, FAST_OUTPUT_ROOT)
            self.assertEqual(manager.runtime_root, FAST_RUNTIME_ROOT)
            self.assertEqual(result["version"], FAST_PATH_BINDING_VERSION)
            self.assertEqual(result["output_root"], str(FAST_OUTPUT_ROOT))
            self.assertEqual(result["runtime_root"], str(FAST_RUNTIME_ROOT))
        finally:
            fast_change_history_manager.DEFAULT_OUTPUT_ROOT = previous_output
            fast_change_history_manager.DEFAULT_RUNTIME_ROOT = previous_runtime

    def test_process4_binds_fast_paths_before_runtime_shell_initialization(self):
        source = inspect.getsource(ProcessForMapConsole.__init__)
        bind_index = source.index("bind_fast_change_artifact_paths()")
        super_index = source.index("super().__init__(GLOBAL_DATA)")
        self.assertLess(bind_index, super_index)


if __name__ == "__main__":
    unittest.main()
