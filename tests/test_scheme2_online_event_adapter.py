import unittest

from system.model.map_control.mfac_model.mfac_eligibility import (
    MFACEligibilityConfig,
    StrictMFACEligibilityGate,
)
from system.model.map_control.mfac_model.online_event_adapter import (
    OnlineResponseToMFACAdapter,
)
from system.model.map_control.mfac_model.process_response import ProcessResponseEvent


class OnlineResponseToMFACAdapterTest(unittest.TestCase):
    def setUp(self):
        self.adapter = OnlineResponseToMFACAdapter(
            StrictMFACEligibilityGate(
                MFACEligibilityConfig(max_abs_qbase_drift=0.5)
            )
        )

    @staticmethod
    def response(delta_so2=-10.0, status="COMPLETED"):
        return ProcessResponseEvent(
            response_event_id="S2-RESP-00000001",
            tracking_event_id="S2-FLOW-00000001",
            status=status,
            condition_snapshot_version="v001",
            mfac_context_id="MFAC-BASE-17",
            target_change_time="2026-08-26T10:00:00+08:00",
            actual_flow_reached_time="2026-08-26T10:00:20+08:00",
            response_start_time="2026-08-26T10:00:30+08:00",
            response_end_time="2026-08-26T10:01:00+08:00",
            q_before=30.0,
            q_after=32.0,
            delta_q_actual=2.0,
            so2_target=35.0,
            so2_before=50.0,
            so2_after=50.0 + delta_so2,
            delta_so2=delta_so2,
            qbase_before=30.0,
            qbase_after=30.2,
            qbase_drift=0.2,
            inlet_so2_change=5.0,
            ph_before=6.2,
            ph_after=6.2,
            delta_ph=0.0,
            data_quality_ok=True,
            metadata={
                "execution_delay_seconds": 20.0,
                "delay_onset_seconds": 10.0,
            },
        )

    def test_completed_response_becomes_eligible_action_response_event(self):
        result = self.adapter.adapt(
            self.response(),
            condition_label="17",
            base_condition_id="17",
            grid_id="P1-S17",
            policy_region_id="R_0017",
        )

        self.assertTrue(result.learning_eligible)
        self.assertEqual(result.action_source, "ONLINE_DCS_APPLIED_TARGET")
        self.assertEqual(result.delta_q_actual, 2.0)
        self.assertEqual(result.delta_so2, -10.0)
        self.assertEqual(result.phi_event, -5.0)
        self.assertEqual(result.mfac_context_id, "MFAC-BASE-17")
        self.assertEqual(
            result.metadata["tracking_event_id"],
            "S2-FLOW-00000001",
        )

    def test_positive_phi_response_is_rejected(self):
        result = self.adapter.adapt(
            self.response(delta_so2=10.0),
            condition_label="17",
            base_condition_id="17",
        )

        self.assertFalse(result.learning_eligible)
        self.assertIn("PHI_DIRECTION_NOT_NEGATIVE", result.reject_reason)

    def test_censored_response_is_not_learned(self):
        response = self.response(status="CENSORED")
        response.censor_reason = "FAST_OVERLAP"
        response.fast_overlap = True
        result = self.adapter.adapt(
            response,
            condition_label="17",
            base_condition_id="17",
        )

        self.assertFalse(result.learning_eligible)
        self.assertIn("SCHEME1_EPISODE_INVALID", result.reject_reason)

    def test_missing_qbase_evidence_is_not_silently_approved(self):
        response = self.response()
        response.qbase_before = None
        response.qbase_after = None
        response.qbase_drift = None
        result = self.adapter.adapt(
            response,
            condition_label="17",
            base_condition_id="17",
        )

        self.assertFalse(result.learning_eligible)
        self.assertIn("MISSING_QBASE_STABILITY_EVIDENCE", result.reject_reason)


if __name__ == "__main__":
    unittest.main()
