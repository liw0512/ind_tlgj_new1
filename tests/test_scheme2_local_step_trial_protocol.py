import unittest

from system.model.map_control.mfac_model.local_step_identification import (
    LocalStepIdentificationProposal,
)
from system.model.map_control.mfac_model.local_step_trial_protocol import (
    LocalStepTrialProtocolConfig,
    LocalStepTrialSafetyMonitor,
    approve_local_step_proposal,
    evaluate_local_step_trial,
    promote_local_step_evidence,
)
from system.model.map_control.mfac_model.ph_arbitration import (
    PHResidualArbitrationConfig,
)
from system.model.map_control.mfac_model.ph_response import PHResponseEvent
from system.model.map_control.mfac_model.process_response import ProcessResponseEvent


class Scheme2LocalStepTrialProtocolTest(unittest.TestCase):
    @staticmethod
    def proposal():
        return LocalStepIdentificationProposal(
            status="REVIEW_CANDIDATE",
            proposal_id="LOCAL-STEP-CTX-1",
            proposed_test_target_supply_flow=32.0,
            step_up_m3_h=2.0,
            actual_supply_flow=30.0,
            qbase_effective=30.0,
            ph_value=6.20,
            outlet_so2=15.0,
            metadata={
                "condition_snapshot_version": "v001",
                "mfac_context_id": "CTX",
            },
        )

    @staticmethod
    def config():
        # Numeric values are test fixtures only, not production calibration.
        return LocalStepTrialProtocolConfig(
            max_sample_gap_seconds=30.0,
            max_abs_step_error_m3_h=0.5,
            max_abs_qbase_drift=2.0,
            max_relative_qbase_drift=0.06,
            max_abs_inlet_so2_change=50.0,
            min_abs_delta_so2=0.5,
            min_abs_delta_ph=0.02,
            minimum_so2_observation_seconds=600.0,
            minimum_ph_observation_seconds=900.0,
            outlet_so2_abort_headroom_to_safe_max=5.0,
        )

    @staticmethod
    def envelope():
        return PHResidualArbitrationConfig(
            operating_min=6.0,
            operating_max=6.4,
            safe_min=5.6,
            safe_max=6.8,
            guard_band=0.15,
            min_confidence=0.5,
        )

    @classmethod
    def plan(cls):
        return approve_local_step_proposal(
            cls.proposal(),
            human_approved=True,
            reviewer_id="operator-review",
            approval_time="2026-08-27T10:00:00+08:00",
        )

    @staticmethod
    def so2_response(**overrides):
        values = dict(
            response_event_id="SO2-R1",
            tracking_event_id="TRACK-1",
            status="COMPLETED",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            target_change_time="2026-08-27T10:01:00+08:00",
            actual_flow_reached_time="2026-08-27T10:02:00+08:00",
            response_start_time="2026-08-27T10:07:00+08:00",
            response_end_time="2026-08-27T10:13:00+08:00",
            q_before=30.0,
            q_after=32.0,
            delta_q_actual=2.0,
            so2_target=15.0,
            so2_before=16.0,
            so2_after=14.0,
            delta_so2=-2.0,
            qbase_before=30.0,
            qbase_after=30.5,
            qbase_drift=0.5,
            inlet_so2_before=1700.0,
            inlet_so2_after=1720.0,
            inlet_so2_change=20.0,
            ph_before=6.20,
            ph_after=6.25,
            delta_ph=0.05,
            fast_overlap=False,
            condition_changed=False,
            target_changed=False,
            data_quality_ok=True,
        )
        values.update(overrides)
        return ProcessResponseEvent(**values)

    @staticmethod
    def ph_response(**overrides):
        values = dict(
            response_event_id="PH-R1",
            tracking_event_id="TRACK-1",
            status="COMPLETED",
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
            target_change_time="2026-08-27T10:01:00+08:00",
            actual_flow_reached_time="2026-08-27T10:02:00+08:00",
            response_start_time="2026-08-27T10:05:10+08:00",
            response_end_time="2026-08-27T10:17:00+08:00",
            q_before=30.0,
            q_after=32.0,
            delta_q_actual=2.0,
            ph_before=6.20,
            ph_after=6.25,
            delta_ph=0.05,
            qbase_before=30.0,
            qbase_after=30.5,
            qbase_drift=0.5,
            so2_target=15.0,
            fast_overlap=False,
            condition_changed=False,
            target_changed=False,
            data_quality_ok=True,
        )
        values.update(overrides)
        return PHResponseEvent(**values)

    def safety_clear(self):
        monitor = LocalStepTrialSafetyMonitor(
            self.plan(),
            self.config(),
            self.envelope(),
            outlet_so2_safe_max=35.0,
        )
        return monitor.update(
            timestamp="2026-08-27T10:10:00+08:00",
            ph_value=6.25,
            outlet_so2=14.0,
            qbase_drift_from_pretrial=0.5,
            inlet_so2_change_from_pretrial=20.0,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
        )

    def test_proposal_requires_explicit_human_approval(self):
        with self.assertRaises(ValueError):
            approve_local_step_proposal(
                self.proposal(),
                human_approved=False,
                reviewer_id="operator-review",
                approval_time="2026-08-27T10:00:00+08:00",
            )

    def test_safety_monitor_recommends_manual_abort_on_ph_exit(self):
        monitor = LocalStepTrialSafetyMonitor(
            self.plan(),
            self.config(),
            self.envelope(),
            outlet_so2_safe_max=35.0,
        )
        result = monitor.update(
            timestamp="2026-08-27T10:10:00+08:00",
            ph_value=6.41,
            outlet_so2=14.0,
            qbase_drift_from_pretrial=0.5,
            inlet_so2_change_from_pretrial=20.0,
            condition_snapshot_version="v001",
            mfac_context_id="CTX",
        )
        self.assertTrue(result.abort_recommended)
        self.assertIn("PH_LEFT_OPERATING_ENVELOPE", result.reasons)
        self.assertEqual(result.manual_return_target_supply_flow, 30.0)
        self.assertFalse(monitor.dcs_write_enabled)
        self.assertFalse(hasattr(monitor, "execute"))

    def test_completed_dual_response_becomes_evidence_candidate_only(self):
        outcome = evaluate_local_step_trial(
            self.plan(),
            self.config(),
            self.so2_response(),
            self.ph_response(),
            self.safety_clear(),
        )
        self.assertEqual(outcome.status, "LOCAL_GAIN_EVIDENCE_CANDIDATE")
        self.assertTrue(outcome.eligible_for_local_gain_promotion)
        self.assertLess(outcome.phi_so2_event, 0.0)
        self.assertGreater(outcome.phi_ph_event, 0.0)
        self.assertFalse(outcome.learning_permission)

    def test_wrong_ph_direction_rejects_trial(self):
        outcome = evaluate_local_step_trial(
            self.plan(),
            self.config(),
            self.so2_response(),
            self.ph_response(ph_after=6.17, delta_ph=-0.03),
            self.safety_clear(),
        )
        self.assertEqual(outcome.status, "REJECTED")
        self.assertIn("PHI_PH_DIRECTION_NOT_POSITIVE", outcome.reasons)

    def test_mismatched_tracking_event_rejects_trial(self):
        outcome = evaluate_local_step_trial(
            self.plan(),
            self.config(),
            self.so2_response(),
            self.ph_response(tracking_event_id="TRACK-OTHER"),
            self.safety_clear(),
        )
        self.assertEqual(outcome.status, "REJECTED")
        self.assertIn("DUAL_RESPONSE_TRACKING_EVENT_MISMATCH", outcome.reasons)

    def test_mismatched_safety_summary_rejects_trial(self):
        safety = self.safety_clear()
        safety.trial_id = "TRIAL-OTHER"
        outcome = evaluate_local_step_trial(
            self.plan(),
            self.config(),
            self.so2_response(),
            self.ph_response(),
            safety,
        )
        self.assertEqual(outcome.status, "REJECTED")
        self.assertIn("SAFETY_TRIAL_ID_MISMATCH", outcome.reasons)

    def test_short_ph_observation_rejects_trial(self):
        outcome = evaluate_local_step_trial(
            self.plan(),
            self.config(),
            self.so2_response(),
            self.ph_response(response_end_time="2026-08-27T10:12:00+08:00"),
            self.safety_clear(),
        )
        self.assertEqual(outcome.status, "REJECTED")
        self.assertIn("PH_OBSERVATION_TOO_SHORT", outcome.reasons)

    def test_second_human_review_is_required_before_learning_eligible_event(self):
        plan = self.plan()
        outcome = evaluate_local_step_trial(
            plan,
            self.config(),
            self.so2_response(),
            self.ph_response(),
            self.safety_clear(),
        )
        with self.assertRaises(ValueError):
            promote_local_step_evidence(
                plan,
                outcome,
                human_evidence_approved=False,
                reviewer_id="evidence-reviewer",
                condition_label="P10",
                base_condition_id="P10",
            )
        event = promote_local_step_evidence(
            plan,
            outcome,
            human_evidence_approved=True,
            reviewer_id="evidence-reviewer",
            condition_label="P10",
            base_condition_id="P10",
        )
        self.assertTrue(event.learning_eligible)
        self.assertEqual(event.action_source, "MANUAL_LOCAL_STEP_IDENTIFICATION_REVIEWED")
        self.assertEqual(event.metadata["evidence_role"], "LOCAL_GAIN")
        self.assertFalse(event.metadata["automatic_online_adaptation_allowed"])
        self.assertFalse(event.metadata["return_to_baseline_learning_allowed"])
        self.assertEqual(event.so2_target, 15.0)
        self.assertEqual(event.inlet_so2_change, 20.0)
        self.assertGreater(event.metadata["phi_ph_event"], 0.0)


if __name__ == "__main__":
    unittest.main()
