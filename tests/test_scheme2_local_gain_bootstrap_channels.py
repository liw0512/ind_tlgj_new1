import unittest

from system.model.map_control.mfac_model.bootstrap_trainer import (
    MFACReplayConfig,
    build_bootstrap_evidence,
)
from system.model.map_control.mfac_model.mfac_schema import ActionResponseEvent
from system.model.map_control.mfac_model.ph_bootstrap_trainer import (
    PHReplayConfig,
    build_ph_bootstrap_evidence,
)


class LocalGainBootstrapChannelsTest(unittest.TestCase):
    @staticmethod
    def event(*, local, ph_out=False, source=None):
        action_source = source or (
            "HISTORICAL_ACTUAL_SUPPLY_FLOW_LOCAL_GAIN"
            if local
            else "HISTORICAL_ACTUAL_SUPPLY_FLOW"
        )
        return ActionResponseEvent(
            event_id="E1",
            condition_snapshot_version="v001",
            condition_label="17",
            base_condition_id="17",
            grid_id="P1-S17",
            policy_region_id="R_0017",
            mfac_context_id="MFAC-COND-17",
            action_start_time="2026-08-01T10:00:00",
            action_source=action_source,
            delta_q_actual=2.0,
            delta_so2=-8.0,
            delta_ph=0.10,
            phi_event=-4.0,
            learning_eligible=True,
            metadata={
                "historical_local_gain_eligible": bool(local),
                "ph_out_of_safe_range": bool(ph_out),
            },
        )

    def test_generic_historical_event_does_not_seed_either_phi(self):
        event = self.event(local=False)
        self.assertEqual(
            build_bootstrap_evidence([event], MFACReplayConfig(eta=0.1, mu=1.0)),
            [],
        )
        self.assertEqual(
            build_ph_bootstrap_evidence([event], PHReplayConfig(eta=0.1, mu=1.0)),
            [],
        )

    def test_local_gain_event_seeds_both_physical_channels(self):
        event = self.event(local=True)
        so2 = build_bootstrap_evidence(
            [event], MFACReplayConfig(eta=0.1, mu=1.0)
        )
        ph = build_ph_bootstrap_evidence(
            [event], PHReplayConfig(eta=0.1, mu=1.0)
        )
        self.assertEqual(len(so2), 1)
        self.assertEqual(len(ph), 1)
        self.assertLess(so2[0].phi_seed, 0.0)
        self.assertGreater(ph[0].phi_seed, 0.0)
        self.assertEqual(so2[0].metadata["evidence_role_required"], "LOCAL_GAIN")
        self.assertEqual(ph[0].metadata["evidence_role_required"], "LOCAL_GAIN")

    def test_ph_safe_excursion_is_defense_in_depth_rejected(self):
        event = self.event(local=True, ph_out=True)
        self.assertEqual(
            build_bootstrap_evidence([event], MFACReplayConfig(eta=0.1, mu=1.0)),
            [],
        )
        self.assertEqual(
            build_ph_bootstrap_evidence([event], PHReplayConfig(eta=0.1, mu=1.0)),
            [],
        )

    def test_online_nonhistorical_event_remains_backward_compatible(self):
        event = self.event(local=False, source="ONLINE_DCS_APPLIED_TARGET")
        so2 = build_bootstrap_evidence(
            [event], MFACReplayConfig(eta=0.1, mu=1.0)
        )
        ph = build_ph_bootstrap_evidence(
            [event], PHReplayConfig(eta=0.1, mu=1.0)
        )
        self.assertEqual(len(so2), 1)
        self.assertEqual(len(ph), 1)


if __name__ == "__main__":
    unittest.main()
