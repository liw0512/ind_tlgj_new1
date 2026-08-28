import unittest

from system.model.map_control.mfac_model.mfac_schema import (
    ActionResponseEvent,
    MFACRuntimeState,
)
from system.model.map_control.mfac_model.online_adaptation import (
    MFACOnlineAdaptationConfig,
    MFACOnlineAdapter,
)


class MFACOnlineAdapterTest(unittest.TestCase):
    @staticmethod
    def config(max_update=0.5):
        return MFACOnlineAdaptationConfig(
            eta=0.1,
            mu=1.0,
            phi_lower_bound=-10.0,
            phi_upper_bound=-0.1,
            max_single_update_abs=max_update,
            confidence_reference_event_count=5.0,
        )

    @staticmethod
    def state():
        return MFACRuntimeState(
            condition_snapshot_version="v001",
            mfac_context_id="MFAC-BASE-17",
            phi_live=-5.0,
            confidence_live=0.8,
            valid_event_count=10,
        )

    @staticmethod
    def event(**overrides):
        values = dict(
            event_id="E-11",
            condition_snapshot_version="v001",
            condition_label="17",
            base_condition_id="17",
            grid_id="P1-S17",
            policy_region_id="R_0017",
            mfac_context_id="MFAC-BASE-17",
            response_end_time="2026-08-26T10:01:00+08:00",
            delta_q_actual=2.0,
            delta_so2=-8.0,
            learning_eligible=True,
            phi_event=-4.0,
        )
        values.update(overrides)
        return ActionResponseEvent(**values)

    def test_eligible_event_updates_live_phi_and_confidence(self):
        adapter = MFACOnlineAdapter(self.config())
        result = adapter.update(self.state(), self.event())

        self.assertTrue(result.updated)
        self.assertTrue(result.phi_updated)
        self.assertTrue(result.confidence_updated)
        self.assertEqual(result.reason, "UPDATED")
        self.assertAlmostEqual(result.old_phi, -5.0)
        self.assertAlmostEqual(result.new_phi, -4.92)
        self.assertAlmostEqual(result.applied_update, 0.08)
        self.assertGreater(result.new_confidence, 0.8)
        self.assertEqual(result.runtime_state.valid_event_count, 11)
        self.assertEqual(result.runtime_state.last_event_id, "E-11")
        self.assertEqual(
            result.runtime_state.last_update_time,
            "2026-08-26T10:01:00+08:00",
        )
        evidence = result.runtime_state.metadata["online_confidence_so2"]
        self.assertEqual(evidence["effective_event_count"], 1.0)
        self.assertEqual(evidence["direction_consistency"], 1.0)

    def test_clean_wrong_direction_downgrades_confidence_without_flipping_phi(self):
        adapter = MFACOnlineAdapter(self.config())
        state = self.state()
        result = adapter.update(
            state,
            self.event(delta_so2=8.0, phi_event=4.0),
        )

        self.assertTrue(result.updated)
        self.assertFalse(result.phi_updated)
        self.assertTrue(result.confidence_updated)
        self.assertEqual(result.reason, "CONFIDENCE_DOWNGRADED_PHYSICAL_CONFLICT")
        self.assertEqual(result.new_phi, -5.0)
        self.assertLess(result.new_confidence, 0.8)
        self.assertEqual(result.runtime_state.valid_event_count, 10)
        self.assertEqual(result.runtime_state.last_event_id, "E-11")
        self.assertEqual(
            result.runtime_state.metadata["online_confidence_so2"]["direction_consistency"],
            0.0,
        )

    def test_ineligible_event_cannot_update_phi_or_confidence(self):
        adapter = MFACOnlineAdapter(self.config())
        result = adapter.update(
            self.state(),
            self.event(learning_eligible=False),
        )

        self.assertFalse(result.updated)
        self.assertFalse(result.confidence_updated)
        self.assertEqual(result.reason, "EVENT_NOT_LEARNING_ELIGIBLE")
        self.assertEqual(result.new_phi, -5.0)
        self.assertEqual(result.new_confidence, 0.8)
        self.assertEqual(result.runtime_state.valid_event_count, 10)

    def test_context_mismatch_is_rejected(self):
        adapter = MFACOnlineAdapter(self.config())
        result = adapter.update(
            self.state(),
            self.event(mfac_context_id="MFAC-BASE-18"),
        )

        self.assertFalse(result.updated)
        self.assertEqual(result.reason, "MFAC_CONTEXT_MISMATCH")

    def test_snapshot_mismatch_is_rejected(self):
        adapter = MFACOnlineAdapter(self.config())
        result = adapter.update(
            self.state(),
            self.event(condition_snapshot_version="v002"),
        )

        self.assertFalse(result.updated)
        self.assertEqual(result.reason, "SNAPSHOT_VERSION_MISMATCH")

    def test_duplicate_event_is_idempotent(self):
        adapter = MFACOnlineAdapter(self.config())
        state = self.state()
        state.last_event_id = "E-11"
        result = adapter.update(state, self.event())

        self.assertFalse(result.updated)
        self.assertEqual(result.reason, "DUPLICATE_EVENT")
        self.assertEqual(result.new_phi, -5.0)
        self.assertEqual(result.new_confidence, 0.8)

    def test_single_update_limit_is_enforced(self):
        adapter = MFACOnlineAdapter(
            MFACOnlineAdaptationConfig(
                eta=1.0,
                mu=1.0,
                phi_lower_bound=-10.0,
                phi_upper_bound=-0.1,
                max_single_update_abs=0.2,
            )
        )
        result = adapter.update(
            self.state(),
            self.event(delta_so2=-2.0, phi_event=-1.0),
        )

        self.assertTrue(result.updated)
        self.assertAlmostEqual(result.applied_update, 0.2)
        self.assertAlmostEqual(result.new_phi, -4.8)

    def test_invalid_delta_q_is_rejected(self):
        adapter = MFACOnlineAdapter(self.config())
        result = adapter.update(
            self.state(),
            self.event(delta_q_actual=0.0),
        )

        self.assertFalse(result.updated)
        self.assertEqual(result.reason, "INVALID_DELTA_Q")

    def test_phi_bounds_must_remain_negative(self):
        with self.assertRaises(ValueError):
            MFACOnlineAdaptationConfig(
                eta=0.1,
                mu=1.0,
                phi_lower_bound=-10.0,
                phi_upper_bound=0.1,
                max_single_update_abs=0.5,
            )


if __name__ == "__main__":
    unittest.main()
