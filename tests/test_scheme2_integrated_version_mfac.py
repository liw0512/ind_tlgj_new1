import unittest

from system.model.map_control.condition_model.integrated_version_manager import (
    IntegratedVersionManager,
    normalize_pointer,
)


class Scheme2IntegratedVersionMFACPointerTest(unittest.TestCase):
    @staticmethod
    def canonical_pointer():
        return {
            "integrated_version": "v007",
            "backend": "MFAC",
            "condition": {
                "version": "v007",
                "snapshot_path": "/tmp/condition-v007.json",
                "snapshot_sha256": "abc",
            },
            "mfac": {
                "version": "v007",
                "source_condition_version": "v007",
                "snapshot_path": "/tmp/mfac-v007-manifest.json",
                "manifest_sha256": "def",
            },
        }

    def test_canonical_pointer_uses_mfac_as_second_module(self):
        pointer = normalize_pointer(self.canonical_pointer())
        self.assertEqual(pointer.integrated_version, "v007")
        self.assertEqual(pointer.condition_version, "v007")
        self.assertEqual(pointer.mfac_version, "v007")
        self.assertEqual(pointer.mfac_source_condition_version, "v007")
        self.assertEqual(
            str(pointer.mfac_snapshot_path),
            "/tmp/mfac-v007-manifest.json",
        )
        self.assertEqual(pointer.mfac_manifest_sha256, "def")
        # Migration properties remain read-only aliases.
        self.assertEqual(pointer.policy_version, pointer.mfac_version)
        self.assertEqual(pointer.policy_snapshot_path, pointer.mfac_snapshot_path)
        self.assertNotIn("slurry_policy", pointer.raw)

    def test_legacy_mfac_migration_pointer_normalizes_to_mfac(self):
        legacy = {
            "integrated_version": "v003",
            "condition": {
                "version": "v003",
                "snapshot_path": "/tmp/condition-v003.json",
            },
            "slurry_policy": {
                "version": "v003",
                "source_condition_version": "v003",
                "snapshot_path": "/tmp/mfac-v003-manifest.json",
                "backend": "MFAC_COMPAT_POINTER",
            },
        }
        pointer = normalize_pointer(legacy)
        self.assertEqual(pointer.mfac_version, "v003")
        self.assertEqual(
            str(pointer.mfac_snapshot_path),
            "/tmp/mfac-v003-manifest.json",
        )

    def test_status_fields_publish_canonical_and_legacy_alias(self):
        manager = IntegratedVersionManager(
            {
                "enabled": False,
                "active_version_file": "",
            }
        )
        manager._committed_version = "v009"
        fields = manager.status_fields(
            condition_loaded_version="v009",
            mfac_loaded_version="v009",
        )
        self.assertEqual(fields["mfac_loaded_version"], "v009")
        self.assertEqual(fields["slurry_policy_loaded_version"], "v009")
        self.assertTrue(fields["version_consistent"])
        self.assertEqual(fields["second_module_backend"], "MFAC")


if __name__ == "__main__":
    unittest.main()
