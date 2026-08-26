import unittest

from system.model.map_control.mfac_model.mfac_schema import (
    MFAC_SEMANTICS_VERSION,
    ActionResponseEvent,
    DelayProfile,
    MFACBootstrapProfile,
    MFACRuntimeState,
)


class MFACSchemaTest(unittest.TestCase):
    def test_action_response_event_round_trip(self):
        event = ActionResponseEvent(
            event_id="E-1",
            condition_snapshot_version="v001",
            condition_label="17",
            base_condition_id="17",
            grid_id="P2-S3",
            policy_region_id="R_0017",
            mfac_context_id="MFAC-COND-17",
            delta_q_actual=2.0,
            delta_so2=-10.0,
            learning_eligible=True,
            phi_event=-5.0,
        )
        restored = ActionResponseEvent.from_dict(event.to_dict())

        self.assertEqual(restored.event_id, "E-1")
        self.assertEqual(restored.phi_event, -5.0)
        self.assertTrue(restored.learning_eligible)
        self.assertEqual(restored.semantics_version, MFAC_SEMANTICS_VERSION)

    def test_bootstrap_profile_round_trip(self):
        profile = MFACBootstrapProfile(
            condition_snapshot_version="v001",
            mfac_context_id="MFAC-COND-17",
            phi_prior=-5.4,
            phi_live0=-5.2,
            confidence=0.8,
            valid_event_count=50,
            independent_days=12,
            delay_profile=DelayProfile(
                onset_p50_seconds=60.0,
                response_p50_seconds=120.0,
            ),
            condition_labels=["17"],
            base_condition_ids=["17"],
        )
        restored = MFACBootstrapProfile.from_dict(profile.to_dict())

        self.assertEqual(restored.phi_live0, -5.2)
        self.assertEqual(restored.delay_profile.onset_p50_seconds, 60.0)
        self.assertEqual(restored.valid_event_count, 50)

    def test_runtime_state_round_trip(self):
        state = MFACRuntimeState(
            condition_snapshot_version="v001",
            mfac_context_id="MFAC-COND-17",
            phi_live=-4.9,
            confidence_live=0.9,
            bias_live=1.2,
            valid_event_count=80,
            last_event_id="E-80",
        )
        restored = MFACRuntimeState.from_dict(state.to_dict())

        self.assertEqual(restored.phi_live, -4.9)
        self.assertEqual(restored.bias_live, 1.2)
        self.assertEqual(restored.last_event_id, "E-80")


if __name__ == "__main__":
    unittest.main()
