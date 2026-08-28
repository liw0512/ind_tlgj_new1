import json
import tempfile
import unittest
from pathlib import Path

from system.model.map_control.mfac_model.mfac_schema import MFACRuntimeState
from system.model.map_control.mfac_model.runtime_store import Scheme2RuntimeStore


class Scheme2RuntimeStoreTest(unittest.TestCase):
    @staticmethod
    def runtime_state(
        snapshot="v001",
        context="MFAC-BASE-17",
        *,
        grid_id=None,
    ):
        metadata = {}
        if grid_id is not None:
            metadata["runtime_grid_id"] = grid_id
        return MFACRuntimeState(
            condition_snapshot_version=snapshot,
            mfac_context_id=context,
            phi_live=-5.0,
            confidence_live=0.8,
            bias_live=0.0,
            valid_event_count=12,
            last_event_id="E-12",
            last_update_time="2026-08-26T10:00:00+08:00",
            phi_ph_live=0.04,
            confidence_ph_live=0.7,
            ph_valid_event_count=9,
            ph_last_event_id="PH-9",
            ph_last_update_time="2026-08-26T10:00:00+08:00",
            metadata=metadata,
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
                self.runtime_state(snapshot="v001", grid_id="P1-S1"),
                residual_mfac_hold=1.0,
            )
            store.save()

            restored_store = Scheme2RuntimeStore(directory)
            restored = restored_store.restore_context(
                condition_snapshot_version="v002",
                mfac_context_id="MFAC-BASE-17",
            )

            self.assertFalse(restored.restored)
            self.assertEqual(restored.reason, "SNAPSHOT_VERSION_MISMATCH")

    def test_same_context_and_grid_can_migrate_phi_without_residual(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Scheme2RuntimeStore(directory)
            store.upsert_context(
                self.runtime_state(snapshot="v001", grid_id="P1-S1"),
                residual_mfac_hold=3.5,
            )
            store.save()

            restored = Scheme2RuntimeStore(directory).restore_same_context_across_snapshot(
                condition_snapshot_version="v002",
                mfac_context_id="MFAC-BASE-17",
                grid_id="P1-S1",
            )

            self.assertTrue(restored.restored)
            self.assertEqual(
                restored.reason,
                "CROSS_SNAPSHOT_SAME_CONTEXT_GRID_MIGRATED",
            )
            self.assertEqual(
                restored.runtime_state.condition_snapshot_version,
                "v002",
            )
            self.assertEqual(restored.runtime_state.mfac_context_id, "MFAC-BASE-17")
            self.assertEqual(restored.runtime_state.phi_live, -5.0)
            self.assertEqual(restored.runtime_state.confidence_live, 0.8)
            self.assertEqual(restored.runtime_state.valid_event_count, 12)
            self.assertEqual(restored.runtime_state.phi_ph_live, 0.04)
            self.assertEqual(restored.runtime_state.confidence_ph_live, 0.7)
            self.assertEqual(restored.runtime_state.ph_valid_event_count, 9)
            # A seven-day offline version change is not a continuation of an
            # already-issued residual / delayed-control action.
            self.assertEqual(restored.residual_mfac_hold, 0.0)
            metadata = restored.runtime_state.metadata
            self.assertTrue(metadata["cross_snapshot_state_migrated"])
            self.assertEqual(metadata["cross_snapshot_source_version"], "v001")
            self.assertEqual(metadata["cross_snapshot_target_version"], "v002")
            self.assertEqual(
                metadata["cross_snapshot_migration_policy"],
                "SAME_MFAC_CONTEXT_AND_GRID_ONLY",
            )
            self.assertFalse(metadata["cross_snapshot_residual_reused"])

    def test_cross_snapshot_migration_rejects_different_grid(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Scheme2RuntimeStore(directory)
            store.upsert_context(
                self.runtime_state(snapshot="v001", grid_id="P1-S1"),
                residual_mfac_hold=1.0,
            )
            store.save()

            restored = Scheme2RuntimeStore(directory).restore_same_context_across_snapshot(
                condition_snapshot_version="v002",
                mfac_context_id="MFAC-BASE-17",
                grid_id="P2-S1",
            )

            self.assertFalse(restored.restored)
            self.assertEqual(restored.reason, "MIGRATION_GRID_MISMATCH")

    def test_cross_snapshot_migration_requires_persisted_grid_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Scheme2RuntimeStore(directory)
            store.upsert_context(
                self.runtime_state(snapshot="v001"),
                residual_mfac_hold=1.0,
            )
            store.save()

            restored = Scheme2RuntimeStore(directory).restore_same_context_across_snapshot(
                condition_snapshot_version="v002",
                mfac_context_id="MFAC-BASE-17",
                grid_id="P1-S1",
            )

            self.assertFalse(restored.restored)
            self.assertEqual(
                restored.reason,
                "MIGRATION_SOURCE_GRID_UNAVAILABLE",
            )

    def test_context_mismatch_does_not_cross_reuse_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Scheme2RuntimeStore(directory)
            store.upsert_context(
                self.runtime_state(context="MFAC-BASE-17", grid_id="P1-S1"),
                residual_mfac_hold=1.0,
            )
            store.save()

            restored = Scheme2RuntimeStore(directory).restore_same_context_across_snapshot(
                condition_snapshot_version="v002",
                mfac_context_id="MFAC-BASE-18",
                grid_id="P1-S1",
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