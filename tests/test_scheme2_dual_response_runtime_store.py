import tempfile
import unittest

from system.model.map_control.mfac_model.mfac_schema import MFACRuntimeState
from system.model.map_control.mfac_model.runtime_store import Scheme2RuntimeStore


class Scheme2DualResponseRuntimeStoreTest(unittest.TestCase):
    def test_persists_and_restores_so2_and_ph_states_together(self):
        with tempfile.TemporaryDirectory() as root:
            store = Scheme2RuntimeStore(root)
            state = MFACRuntimeState(
                condition_snapshot_version="v001",
                mfac_context_id="MFAC-BASE-17",
                phi_live=-4.2,
                confidence_live=0.85,
                valid_event_count=8,
                last_event_id="MFAC-ONLINE-S2-RESP-8",
                last_update_time="2026-08-26T10:30:00+08:00",
                phi_ph_live=0.065,
                confidence_ph_live=0.72,
                ph_valid_event_count=5,
                ph_last_event_id="S2-PH-RESP-00000005",
                ph_last_update_time="2026-08-26T10:29:00+08:00",
            )
            store.upsert_context(state, residual_mfac_hold=1.25)
            store.save()

            restored_store = Scheme2RuntimeStore(root)
            restored = restored_store.restore_context(
                condition_snapshot_version="v001",
                mfac_context_id="MFAC-BASE-17",
            )
            self.assertTrue(restored.restored)
            self.assertEqual(restored.runtime_state.phi_live, -4.2)
            self.assertEqual(restored.runtime_state.phi_so2_live, -4.2)
            self.assertEqual(restored.runtime_state.phi_ph_live, 0.065)
            self.assertEqual(restored.runtime_state.ph_valid_event_count, 5)
            self.assertEqual(
                restored.runtime_state.ph_last_event_id,
                "S2-PH-RESP-00000005",
            )
            self.assertAlmostEqual(restored.residual_mfac_hold, 1.25)


if __name__ == "__main__":
    unittest.main()
