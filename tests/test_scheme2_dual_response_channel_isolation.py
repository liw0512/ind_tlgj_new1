import unittest

from system.model.map_control.mfac_model.mfac_schema import (
    ActionResponseEvent,
    MFACRuntimeState,
)
from system.model.map_control.mfac_model.online_adaptation import (
    MFACOnlineAdaptationConfig,
    MFACOnlineAdapter,
)


class Scheme2DualResponseChannelIsolationTest(unittest.TestCase):
    def test_so2_update_preserves_ph_runtime_state(self):
        state = MFACRuntimeState(
            condition_snapshot_version="v001",
            mfac_context_id="MFAC-BASE-17",
            phi_live=-4.0,
            confidence_live=0.9,
            phi_ph_live=0.065,
            confidence_ph_live=0.75,
            ph_valid_event_count=4,
            ph_last_event_id="S2-PH-RESP-00000004",
            ph_last_update_time="2026-08-26T10:00:00+08:00",
        )
        event = ActionResponseEvent(
            event_id="MFAC-ONLINE-S2-RESP-00000005",
            condition_snapshot_version="v001",
            condition_label="17",
            base_condition_id="17",
            grid_id="P1-S1",
            policy_region_id="R_P1_S1",
            mfac_context_id="MFAC-BASE-17",
            action_start_time="2026-08-26T10:01:00+08:00",
            action_reached_time="2026-08-26T10:01:20+08:00",
            response_start_time="2026-08-26T10:02:00+08:00",
            response_end_time="2026-08-26T10:03:00+08:00",
            q_before=30.0,
            q_after=32.0,
            delta_q_actual=2.0,
            so2_before=50.0,
            so2_after=42.0,
            delta_so2=-8.0,
            learning_eligible=True,
        )
        adapter = MFACOnlineAdapter(
            MFACOnlineAdaptationConfig(
                eta=0.2,
                mu=1.0,
                phi_lower_bound=-10.0,
                phi_upper_bound=-0.1,
                max_single_update_abs=1.0,
            )
        )
        result = adapter.update(state, event)
        self.assertTrue(result.updated)
        self.assertEqual(result.runtime_state.phi_ph_live, 0.065)
        self.assertEqual(result.runtime_state.confidence_ph_live, 0.75)
        self.assertEqual(result.runtime_state.ph_valid_event_count, 4)
        self.assertEqual(
            result.runtime_state.ph_last_event_id,
            "S2-PH-RESP-00000004",
        )


if __name__ == "__main__":
    unittest.main()
