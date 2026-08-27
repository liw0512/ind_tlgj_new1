import unittest

from system.model.map_control.mfac_model.bootstrap_trainer import MFACReplayConfig
from system.model.map_control.mfac_model.dual_response_bootstrap import (
    build_dual_response_bootstrap_evidence,
)
from system.model.map_control.mfac_model.mfac_schema import ActionResponseEvent
from system.model.map_control.mfac_model.ph_bootstrap_trainer import PHReplayConfig


class Scheme2DualResponseBootstrapBundleTest(unittest.TestCase):
    @staticmethod
    def event(
        index,
        *,
        day,
        phi_so2=-4.0,
        phi_ph=0.05,
        approved=True,
        action_start_time=None,
    ):
        delta_q = 2.0
        return ActionResponseEvent(
            event_id="E%d" % index,
            condition_snapshot_version="v001",
            condition_label="17",
            base_condition_id="17",
            grid_id="P1-S17",
            policy_region_id="R_0017",
            mfac_context_id="CTX",
            action_start_time=(
                action_start_time
                or "2026-08-%02dT10:00:00+08:00" % day
            ),
            action_source="MANUAL_LOCAL_STEP_IDENTIFICATION_REVIEWED",
            delta_q_actual=delta_q,
            delta_so2=phi_so2 * delta_q,
            delta_ph=phi_ph * delta_q,
            phi_event=phi_so2,
            learning_eligible=True,
            metadata={
                "evidence_role": "LOCAL_GAIN",
                "manual_evidence_review_approved": True,
                "cohort_bootstrap_review_approved": bool(approved),
                "offline_bootstrap_evidence_allowed": bool(approved),
                "automatic_online_adaptation_allowed": False,
            },
        )

    @staticmethod
    def so2_config():
        return MFACReplayConfig(eta=0.1, mu=1.0)

    @staticmethod
    def ph_config():
        return PHReplayConfig(eta=0.1, mu=1.0)

    def test_same_approved_cohort_builds_one_dual_bundle(self):
        events = [
            self.event(1, day=27, phi_so2=-4.0, phi_ph=0.050),
            self.event(2, day=27, phi_so2=-4.1, phi_ph=0.051),
            self.event(3, day=28, phi_so2=-3.9, phi_ph=0.049),
        ]
        result = build_dual_response_bootstrap_evidence(
            events,
            so2_replay_config=self.so2_config(),
            ph_replay_config=self.ph_config(),
        )
        self.assertEqual(len(result.rejections), 0)
        self.assertEqual(len(result.bundles), 1)
        bundle = result.bundles[0]
        self.assertEqual(bundle.event_ids, ("E1", "E2", "E3"))
        self.assertEqual(bundle.valid_event_count, 3)
        self.assertEqual(bundle.independent_days, 2)
        self.assertEqual(tuple(bundle.so2.event_ids), bundle.event_ids)
        self.assertEqual(tuple(bundle.ph.event_ids), bundle.event_ids)
        self.assertLess(bundle.so2.phi_seed, 0.0)
        self.assertGreater(bundle.ph.phi_seed, 0.0)
        self.assertFalse(bundle.learning_permission)
        self.assertFalse(bundle.residual_control_permission)
        self.assertFalse(bundle.dcs_write_enabled)

    def test_input_string_order_does_not_override_physical_timestamp_order(self):
        # Chronologically E1 (02:00 UTC) is before E2 (03:00 UTC), but a raw
        # ISO-string sort would put E2's "03:00+00" before E1's "10:00+08".
        e1 = self.event(
            1,
            day=27,
            action_start_time="2026-08-27T10:00:00+08:00",
        )
        e2 = self.event(
            2,
            day=27,
            action_start_time="2026-08-27T03:00:00+00:00",
        )
        result = build_dual_response_bootstrap_evidence(
            [e2, e1],
            so2_replay_config=self.so2_config(),
            ph_replay_config=self.ph_config(),
        )
        self.assertEqual(len(result.rejections), 0)
        self.assertEqual(len(result.bundles), 1)
        self.assertEqual(result.bundles[0].event_ids, ("E1", "E2"))
        self.assertEqual(
            tuple(result.bundles[0].so2.event_ids),
            tuple(result.bundles[0].ph.event_ids),
        )

    def test_missing_cohort_approval_is_explicitly_rejected(self):
        result = build_dual_response_bootstrap_evidence(
            [self.event(1, day=27, approved=False)],
            so2_replay_config=self.so2_config(),
            ph_replay_config=self.ph_config(),
        )
        self.assertEqual(len(result.bundles), 0)
        self.assertEqual(len(result.rejections), 1)
        self.assertIn(
            "MANUAL_COHORT_BOOTSTRAP_APPROVAL_MISSING",
            result.rejections[0].reasons,
        )

    def test_channel_specific_invalid_event_rejects_whole_dual_bundle(self):
        events = [
            self.event(1, day=27, phi_so2=-4.0, phi_ph=0.050),
            self.event(2, day=27, phi_so2=-4.1, phi_ph=0.051),
            # SO2 remains physically valid, while pH direction is invalid.
            self.event(3, day=28, phi_so2=-3.9, phi_ph=-0.049),
        ]
        result = build_dual_response_bootstrap_evidence(
            events,
            so2_replay_config=self.so2_config(),
            ph_replay_config=self.ph_config(),
        )
        self.assertEqual(len(result.bundles), 0)
        self.assertEqual(len(result.rejections), 1)
        rejection = result.rejections[0]
        self.assertTrue(
            "PH_EVENT_SET_DIFFERS_FROM_INPUT_COHORT" in rejection.reasons
            or "SO2_PH_EVENT_ORDER_MISMATCH" in rejection.reasons
        )

    def test_replay_rejection_blocks_bundle_even_if_seed_direction_exists(self):
        event = self.event(1, day=27)
        # A direct negative phi_event can let the SO2 evidence builder group the
        # event, but replay still requires a valid delta-Q/delta-SO2 pair.
        event.delta_so2 = None
        result = build_dual_response_bootstrap_evidence(
            [event],
            so2_replay_config=self.so2_config(),
            ph_replay_config=self.ph_config(),
        )
        self.assertEqual(len(result.bundles), 0)
        self.assertEqual(len(result.rejections), 1)
        self.assertIn("SO2_REPLAY_REJECTED_EVENT", result.rejections[0].reasons)


if __name__ == "__main__":
    unittest.main()
