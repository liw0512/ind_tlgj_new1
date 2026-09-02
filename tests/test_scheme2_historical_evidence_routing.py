import json
import unittest

import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG
from system.model.map_control.mfac_model.historical_episode_engine.historical_evidence import (
    DISTURBANCE_COUPLED_DYNAMIC_EVIDENCE,
    DYNAMIC_CLEAN_EVIDENCE,
    DYNAMIC_EVIDENCE,
    HISTORICAL_EVIDENCE_SEMANTICS_VERSION,
    LOCAL_GAIN_EVIDENCE,
    SAFETY_EVIDENCE,
    HistoricalEvidenceRoutingConfig,
    attach_canonical_condition_transition_evidence,
    enrich_historical_episode_frame,
)


class HistoricalEvidenceRoutingTest(unittest.TestCase):
    @staticmethod
    def episode(
        *,
        shape="STEP",
        delta_q=4.0,
        dose=1.0,
        ph_max=6.3,
        ph_out=False,
        valid=True,
        complete=True,
        canonical_changed=False,
        context_reason="ISOLATED_SUPPLY_FLOW_EVENT",
        invalid_reason="",
    ):
        return {
            "episode_id": "E1",
            "valid": valid,
            "flow_effect_complete": complete,
            "flow_shape": shape,
            "active_tower_ids": "xst",
            "flow_event_tower_id": "xst",
            "action_start_time": "2026-08-01T10:00:00",
            "flow_event_baseline_flow": 30.0,
            "flow_event_final_delta_flow": delta_q,
            "flow_event_extra_slurry_volume": dose,
            "delta_outlet_so2": -8.0,
            "before_ph__xst": 6.10,
            "after_ph__xst": 6.20,
            "delta_ph__xst": 0.10,
            "flow_effect_response_tower_ph_min": 6.10,
            "flow_effect_response_tower_ph_max": ph_max,
            "ph_out_of_range__xst": ph_out,
            "flow_context_reason": context_reason,
            "invalid_reason": invalid_reason,
            "mfac_canonical_condition_changed": canonical_changed,
        }

    @staticmethod
    def history():
        times = pd.date_range("2026-08-01T10:00:00", periods=181, freq="10s")
        return pd.DataFrame(
            {
                "date": times,
                "xstshsjy_LL": [34.0] * len(times),
                "xstjy_PH": [6.20] * len(times),
            }
        )

    @staticmethod
    def process_transition_episode(*, complete=True, canonical_changed=True):
        reason = "PROCESS_STATE_CHANGED_DURING_EVENT"
        return HistoricalEvidenceRoutingTest.episode(
            shape="PULSE",
            delta_q=0.0,
            dose=10.0,
            valid=False,
            complete=complete,
            canonical_changed=canonical_changed,
            context_reason=reason,
            invalid_reason=f"FLOW_CONTEXT_NOT_CLEAN:{reason}",
        )

    def test_uncalibrated_local_limits_fail_closed_but_keep_clean_dynamic_role(self):
        result = enrich_historical_episode_frame(
            pd.DataFrame([self.episode()]),
            self.history(),
            PLANT_CONFIG,
        ).iloc[0]
        self.assertFalse(bool(result["mfac_local_gain_eligible"]))
        self.assertFalse(bool(result["mfac_independent_local_gain_eligible"]))
        self.assertTrue(bool(result["mfac_dynamic_evidence_eligible"]))
        self.assertTrue(bool(result["mfac_dynamic_observation_eligible"]))
        self.assertTrue(bool(result["mfac_dynamic_clean_eligible"]))
        self.assertFalse(bool(result["mfac_disturbance_coupled_dynamic_eligible"]))
        self.assertIn(DYNAMIC_CLEAN_EVIDENCE, result["mfac_evidence_roles"])
        self.assertIn(DYNAMIC_EVIDENCE, result["mfac_evidence_roles"])
        self.assertNotIn(LOCAL_GAIN_EVIDENCE, result["mfac_evidence_roles"])
        self.assertIn("LOCAL_GAIN_MAGNITUDE_LIMITS_UNCALIBRATED", result["mfac_evidence_reasons"])
        self.assertIn("dose_3m_m3", result.index)
        self.assertIn("dose_10m_m3", result.index)
        self.assertEqual(
            result["mfac_evidence_semantics_version"],
            HISTORICAL_EVIDENCE_SEMANTICS_VERSION,
        )

    def test_reviewed_small_clean_step_can_be_local_gain(self):
        config = HistoricalEvidenceRoutingConfig(
            max_local_abs_delta_q=5.0,
            max_local_extra_slurry_volume=2.0,
        )
        result = enrich_historical_episode_frame(
            pd.DataFrame([self.episode()]),
            self.history(),
            PLANT_CONFIG,
            config,
        ).iloc[0]
        self.assertTrue(bool(result["mfac_local_gain_eligible"]))
        self.assertTrue(bool(result["mfac_independent_local_gain_eligible"]))
        self.assertIn(LOCAL_GAIN_EVIDENCE, result["mfac_evidence_roles"])
        self.assertIn(DYNAMIC_CLEAN_EVIDENCE, result["mfac_evidence_roles"])
        metrics = json.loads(result["mfac_evidence_metrics"])
        self.assertLess(float(metrics["phi_so2_event"]), 0.0)
        self.assertGreater(float(metrics["phi_ph_event"]), 0.0)

    def test_canonical_transition_overrides_legacy_valid_clean_and_blocks_phi(self):
        config = HistoricalEvidenceRoutingConfig(
            max_local_abs_delta_q=5.0,
            max_local_extra_slurry_volume=2.0,
        )
        result = enrich_historical_episode_frame(
            pd.DataFrame([self.episode(canonical_changed=True)]),
            self.history(),
            PLANT_CONFIG,
            config,
        ).iloc[0]
        self.assertFalse(bool(result["mfac_local_gain_eligible"]))
        self.assertFalse(bool(result["mfac_independent_local_gain_eligible"]))
        self.assertFalse(bool(result["mfac_dynamic_clean_eligible"]))
        self.assertTrue(bool(result["mfac_disturbance_coupled_dynamic_eligible"]))
        self.assertTrue(bool(result["mfac_dynamic_observation_eligible"]))
        self.assertNotIn(DYNAMIC_CLEAN_EVIDENCE, result["mfac_evidence_roles"])
        self.assertIn(
            DISTURBANCE_COUPLED_DYNAMIC_EVIDENCE,
            result["mfac_evidence_roles"],
        )
        self.assertNotIn(LOCAL_GAIN_EVIDENCE, result["mfac_evidence_roles"])
        self.assertIn(
            "CANONICAL_CONDITION_TRANSITION_OVERRIDES_LEGACY_CLEAN",
            result["mfac_evidence_reasons"],
        )
        self.assertIn(
            "LOCAL_GAIN_BLOCKED_BY_CANONICAL_CONDITION_TRANSITION",
            result["mfac_evidence_reasons"],
        )
        metrics = json.loads(result["mfac_evidence_metrics"])
        self.assertTrue(bool(metrics["legacy_valid_canonical_conflict"]))
        self.assertIsNone(metrics["phi_so2_event"])
        self.assertIsNone(metrics["phi_ph_event"])

    def test_pulse_stays_clean_dynamic_and_never_local_gain(self):
        config = HistoricalEvidenceRoutingConfig(
            max_local_abs_delta_q=100.0,
            max_local_extra_slurry_volume=100.0,
        )
        result = enrich_historical_episode_frame(
            pd.DataFrame([self.episode(shape="PULSE", delta_q=0.1)]),
            self.history(),
            PLANT_CONFIG,
            config,
        ).iloc[0]
        self.assertFalse(bool(result["mfac_local_gain_eligible"]))
        self.assertTrue(bool(result["mfac_dynamic_clean_eligible"]))
        self.assertTrue(bool(result["mfac_dynamic_observation_eligible"]))
        metrics = json.loads(result["mfac_evidence_metrics"])
        self.assertIsNone(metrics["phi_so2_event"])
        self.assertIsNone(metrics["phi_ph_event"])

    def test_canonical_process_transition_gets_confounded_dynamic_role_not_phi(self):
        config = HistoricalEvidenceRoutingConfig(
            max_local_abs_delta_q=100.0,
            max_local_extra_slurry_volume=100.0,
        )
        result = enrich_historical_episode_frame(
            pd.DataFrame([self.process_transition_episode()]),
            self.history(),
            PLANT_CONFIG,
            config,
        ).iloc[0]
        self.assertFalse(bool(result["mfac_independent_local_gain_eligible"]))
        self.assertFalse(bool(result["mfac_dynamic_clean_eligible"]))
        self.assertTrue(bool(result["mfac_disturbance_coupled_dynamic_eligible"]))
        self.assertTrue(bool(result["mfac_dynamic_observation_eligible"]))
        self.assertIn(
            DISTURBANCE_COUPLED_DYNAMIC_EVIDENCE,
            result["mfac_evidence_roles"],
        )
        self.assertNotIn(LOCAL_GAIN_EVIDENCE, result["mfac_evidence_roles"])
        self.assertIn(
            "DISTURBANCE_COUPLED_TEMPORAL_CONFOUNDING",
            result["mfac_evidence_reasons"],
        )
        metrics = json.loads(result["mfac_evidence_metrics"])
        self.assertIsNone(metrics["phi_so2_event"])
        self.assertIsNone(metrics["phi_ph_event"])
        self.assertTrue(bool(metrics["canonical_condition_changed"]))
        self.assertTrue(bool(metrics["process_transition_only"]))

    def test_majority_filtered_process_transition_is_not_reintroduced(self):
        result = enrich_historical_episode_frame(
            pd.DataFrame([self.process_transition_episode(canonical_changed=False)]),
            self.history(),
            PLANT_CONFIG,
        ).iloc[0]
        self.assertFalse(bool(result["mfac_dynamic_clean_eligible"]))
        self.assertFalse(bool(result["mfac_disturbance_coupled_dynamic_eligible"]))
        self.assertFalse(bool(result["mfac_dynamic_observation_eligible"]))
        self.assertNotIn(
            DISTURBANCE_COUPLED_DYNAMIC_EVIDENCE,
            result["mfac_evidence_roles"],
        )
        self.assertIn(
            "PROCESS_TRANSITION_NOT_CANONICAL_FORMAL_SWITCH",
            result["mfac_evidence_reasons"],
        )

    def test_incomplete_canonical_transition_does_not_get_dynamic_role(self):
        result = enrich_historical_episode_frame(
            pd.DataFrame([self.process_transition_episode(complete=False)]),
            self.history(),
            PLANT_CONFIG,
        ).iloc[0]
        self.assertFalse(bool(result["mfac_disturbance_coupled_dynamic_eligible"]))
        self.assertFalse(bool(result["mfac_dynamic_observation_eligible"]))
        self.assertIn("HISTORICAL_EFFECT_INCOMPLETE", result["mfac_evidence_reasons"])

    def test_canonical_replay_attachment_requires_majority_and_formal_switch(self):
        episodes = pd.DataFrame(
            [
                dict(self.process_transition_episode(), episode_id="FORMAL"),
                dict(self.process_transition_episode(), episode_id="FILTERED"),
                dict(self.process_transition_episode(), episode_id="NO_FORMAL"),
            ]
        ).drop(columns=["mfac_canonical_condition_changed"])
        replay = pd.DataFrame(
            [
                {
                    "episode_id": "FORMAL",
                    "majority_condition_changed": True,
                    "formal_online_switched_count": 2,
                },
                {
                    "episode_id": "FILTERED",
                    "majority_condition_changed": False,
                    "formal_online_switched_count": 0,
                },
                {
                    "episode_id": "NO_FORMAL",
                    "majority_condition_changed": True,
                    "formal_online_switched_count": 0,
                },
            ]
        )
        attached = attach_canonical_condition_transition_evidence(episodes, replay)
        flags = attached.set_index("episode_id")["mfac_canonical_condition_changed"].to_dict()
        self.assertEqual(flags, {"FORMAL": True, "FILTERED": False, "NO_FORMAL": False})

    def test_canonical_replay_attachment_fails_closed_on_any_missing_episode(self):
        episodes = pd.DataFrame(
            [
                dict(self.episode(), episode_id="CLEAN"),
                dict(self.process_transition_episode(), episode_id="DIST"),
            ]
        ).drop(columns=["mfac_canonical_condition_changed"])
        replay = pd.DataFrame(
            [
                {
                    "episode_id": "DIST",
                    "majority_condition_changed": True,
                    "formal_online_switched_count": 1,
                }
            ]
        )
        with self.assertRaises(KeyError):
            attach_canonical_condition_transition_evidence(episodes, replay)

    def test_operating_ph_excursion_becomes_safety_evidence_and_blocks_local_gain(self):
        config = HistoricalEvidenceRoutingConfig(
            max_local_abs_delta_q=5.0,
            max_local_extra_slurry_volume=2.0,
        )
        result = enrich_historical_episode_frame(
            pd.DataFrame([self.episode(ph_max=6.55)]),
            self.history(),
            PLANT_CONFIG,
            config,
        ).iloc[0]
        self.assertFalse(bool(result["mfac_local_gain_eligible"]))
        self.assertTrue(bool(result["mfac_safety_evidence"]))
        self.assertIn(SAFETY_EVIDENCE, result["mfac_evidence_roles"])
        self.assertIn(DYNAMIC_CLEAN_EVIDENCE, result["mfac_evidence_roles"])
        self.assertIn("LOCAL_GAIN_PH_NOT_IN_OPERATING_ENVELOPE", result["mfac_evidence_reasons"])


if __name__ == "__main__":
    unittest.main()
