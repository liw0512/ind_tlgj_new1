import unittest

from system.model.map_control.mfac_model.bootstrap_trainer import (
    MFACReplayConfig,
    build_bootstrap_evidence,
    finalize_bootstrap_profile,
)
from system.model.map_control.mfac_model.mfac_schema import ActionResponseEvent


class MFACBootstrapTrainerTest(unittest.TestCase):
    @staticmethod
    def event(event_id, day, delta_q, delta_so2, delay, stable, eligible=True):
        return ActionResponseEvent(
            event_id=event_id,
            condition_snapshot_version="v001",
            condition_label="17",
            base_condition_id="17",
            grid_id="P1-S17",
            policy_region_id="R_0017",
            mfac_context_id="MFAC-COND-17",
            action_start_time=f"2026-08-{day:02d}T10:00:00",
            delta_q_actual=delta_q,
            delta_so2=delta_so2,
            phi_event=delta_so2 / delta_q,
            learning_eligible=eligible,
            metadata={
                "observed_response_delay_minutes": delay,
                "time_to_stable_minutes": stable,
            },
        )

    def test_builds_robust_context_evidence(self):
        events = [
            self.event("E1", 1, 2.0, -10.0, 1.0, 2.0),
            self.event("E2", 2, 2.0, -8.0, 2.0, 3.0),
            self.event("E3", 3, 2.0, -9.0, 1.5, 2.5),
        ]
        result = build_bootstrap_evidence(
            events,
            MFACReplayConfig(eta=0.1, mu=1.0),
        )

        self.assertEqual(len(result), 1)
        evidence = result[0]
        self.assertEqual(evidence.mfac_context_id, "MFAC-COND-17")
        self.assertEqual(evidence.valid_event_count, 3)
        self.assertEqual(evidence.independent_days, 3)
        self.assertEqual(evidence.phi_seed, -4.5)
        self.assertLess(evidence.phi_replayed, 0.0)
        self.assertEqual(evidence.delay_profile.onset_p50_seconds, 90.0)
        self.assertEqual(evidence.delay_profile.response_p50_seconds, 150.0)
        self.assertEqual(evidence.metadata["confidence_status"], "NOT_CALIBRATED")

    def test_ignores_ineligible_event(self):
        events = [
            self.event("E1", 1, 2.0, -10.0, 1.0, 2.0, eligible=False),
        ]
        result = build_bootstrap_evidence(
            events,
            MFACReplayConfig(eta=0.1, mu=1.0),
        )
        self.assertEqual(result, [])

    def test_confidence_is_explicit_not_invented_by_estimator(self):
        evidence = build_bootstrap_evidence(
            [self.event("E1", 1, 2.0, -10.0, 1.0, 2.0)],
            MFACReplayConfig(eta=0.1, mu=1.0),
        )[0]
        profile = finalize_bootstrap_profile(evidence, confidence=0.4)

        self.assertEqual(profile.confidence, 0.4)
        self.assertEqual(
            profile.metadata["confidence_status"],
            "EXPLICITLY_ASSIGNED",
        )


if __name__ == "__main__":
    unittest.main()
