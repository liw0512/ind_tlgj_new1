import json
import tempfile
import unittest
from pathlib import Path

from system.model.map_control.mfac_model.mfac_schema import MFACRuntimeState
from system.model.map_control.mfac_model.runtime_store import Scheme2RuntimeStore


class Scheme2RuntimeStoreTest(unittest.TestCase):
    @staticmethod
    def runtime_state(snapshot="v001", context="MFAC-BASE-17"):
        return MFACRuntimeState(
            condition_snapshot_version=snapshot,
            mfac_context_id=context,
            phi_live=-5.0,
            confidence_live=0.8,
            bias_live=0.0,
            valid_event_count=12,
            last_event_id="E-12",
            last_update_time="2026-08-26T10:00:00+08:00",
        )

    def test_round_trip_context_and_target_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Scheme2RuntimeStore(directory)
            store.set_last_valid_algorithm_target(42.0)
            store.upsert_context(
                self.runtime_state(),
                residual_mfac_hold=2.5,
            )
            store.save()

            restored_store = Scheme2RuntimeStore(directory)
            restored = restored_store.restore_context(
                condition_snapshot_version="v001",
                mfac_context_id="MFAC-BASE-17",
            )

            self.assertTrue(restored.restored)
            self.assertEqual(restored.reason, "RESTORED")
            self.assertEqual(restored.runtime_state.phi_live, -5.0)
            self.assertEqual(restored.runtime_state.last_event_id, "E-12")
            self.assertEqual(restored.residual_mfac_hold, 2.5)
            self.assertEqual(restored_store.last_valid_algorithm_target, 42.0)

    def test_snapshot_mismatch_does_not_restore_stale_context(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Scheme2RuntimeStore(directory)
            store.upsert_context(
                self.runtime_state(snapshot="v001"),
                residual_mfac_hold=1.0,
            )
            store.save()

            restored = Scheme2RuntimeStore(directory).restore_context(
                condition_snapshot_version="v002",
                mfac_context_id="MFAC-BASE-17",
            )

            self.assertFalse(restored.restored)
            self.assertEqual(restored.reason, "SNAPSHOT_VERSION_MISMATCH")

    def test_context_mismatch_does_not_cross_reuse_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Scheme2RuntimeStore(directory)
            store.upsert_context(
                self.runtime_state(context="MFAC-BASE-17"),
                residual_mfac_hold=1.0,
            )
            store.save()

            restored = Scheme2RuntimeStore(directory).restore_context(
                condition_snapshot_version="v001",
                mfac_context_id="MFAC-BASE-18",
            )

            self.assertFalse(restored.restored)
            self.assertEqual(restored.reason, "NO_CONTEXT_STATE")

    def test_corrupt_file_is_quarantined_and_safe_state_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scheme2_mfac_runtime_state.json"
            path.write_text("{broken-json", encoding="utf-8")

            store = Scheme2RuntimeStore(directory)

            self.assertTrue(store.state.get("state_recovered_from_error"))
            self.assertTrue(path.with_suffix(path.suffix + ".broken").exists())
            restored = store.restore_context(
                condition_snapshot_version="v001",
                mfac_context_id="MFAC-BASE-17",
            )
            self.assertFalse(restored.restored)

    def test_store_semantics_mismatch_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scheme2_mfac_runtime_state.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "SCHEME2_RUNTIME_STORE_V1",
                        "semantics_version": "OLD_SEMANTICS",
                        "contexts": {},
                    }
                ),
                encoding="utf-8",
            )

            store = Scheme2RuntimeStore(directory)
            restored = store.restore_context(
                condition_snapshot_version="v001",
                mfac_context_id="MFAC-BASE-17",
            )

            self.assertFalse(restored.restored)
            self.assertEqual(restored.reason, "STORE_SEMANTICS_MISMATCH")


if __name__ == "__main__":
    unittest.main()
