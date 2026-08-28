import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from system.model.config import operator_settings


class Scheme2OperatorSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.temp_dir.name) / "operator_settings.json"
        self.path_patch = patch.object(
            operator_settings,
            "OPERATOR_SETTINGS_FILE",
            self.settings_path,
        )
        self.path_patch.start()
        operator_settings._CACHE = {}
        operator_settings._CACHE_MTIME_NS = None

    def tearDown(self):
        operator_settings._CACHE = {}
        operator_settings._CACHE_MTIME_NS = None
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_default_target_and_allowed_range_use_scheme2_config(self):
        self.assertEqual(operator_settings.so2_target_allowed_range(), (5.0, 30.0))
        self.assertEqual(operator_settings.effective_so2_target(), 8.0)
        self.assertEqual(operator_settings.so2_target_source(), "默认配置")

    def test_operator_override_roundtrip_does_not_require_legacy_second_module(self):
        operator_settings.set_operator_so2_target(9.5)
        self.assertEqual(operator_settings.effective_so2_target(), 9.5)
        self.assertEqual(operator_settings.so2_target_source(), "现场设置")

        operator_settings.reset_operator_so2_target()
        self.assertEqual(operator_settings.effective_so2_target(), 8.0)
        self.assertEqual(operator_settings.so2_target_source(), "默认配置")

    def test_out_of_range_target_is_rejected(self):
        with self.assertRaises(ValueError):
            operator_settings.set_operator_so2_target(4.9)
        with self.assertRaises(ValueError):
            operator_settings.set_operator_so2_target(30.1)


if __name__ == "__main__":
    unittest.main()
