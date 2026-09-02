from __future__ import annotations

import unittest

import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG
from system.model.map_control.mfac_model.historical_evidence_role_v2_revalidation import (
    build_role_v2_summary,
    revalidate_historical_evidence_roles,
)


class Scheme2HistoricalEvidenceRoleV2RevalidationTest(unittest.TestCase):
    @staticmethod
    def _base_episode(episode_id: str, *, valid: bool, complete: bool = True):
        return {
            "episode_id": episode_id,
            "valid": valid,
            "flow_effect_complete": complete,
            "flow_shape": "STEP" if valid else "PULSE",
            "active_tower_ids": "xst",
            "flow_event_tower_id": "xst",
            "flow_event_final_delta_flow": 4.0 if valid else 0.0,
            "flow_event_extra_slurry_volume": 1.0 if valid else 8.0,
            "delta_outlet_so2": -8.0,
            "before_ph__xst": 6.10,
            "after_ph__xst": 6.20,
            "delta_ph__xst": 0.10,
            "flow_effect_response_tower_ph_min": 6.10,
            "flow_effect_response_tower_ph_max": 6.30,
            "ph_out_of_range__xst": False,
            "flow_context_reason": (
                "ISOLATED_SUPPLY_FLOW_EVENT"
                if valid
                else "PROCESS_STATE_CHANGED_DURING_EVENT"
            ),
            "invalid_reason": (
                ""
                if valid
                else "FLOW_CONTEXT_NOT_CLEAN:PROCESS_STATE_CHANGED_DURING_EVENT"
            ),
        }

    def _episodes(self):
        clean = self._base_episode("CLEAN", valid=True)
        disturbance = self._base_episode("DIST", valid=False)
        incomplete = self._base_episode("INCOMPLETE", valid=False, complete=False)
        filtered = self._base_episode("FILTERED", valid=False)
        return pd.DataFrame([clean, disturbance, incomplete, filtered])

    @staticmethod
    def _replay():
        return pd.DataFrame(
            [
                {
                    "episode_id": "CLEAN",
                    "majority_condition_changed": False,
                    "formal_online_switched_count": 0,
                },
                {
                    "episode_id": "DIST",
                    "majority_condition_changed": True,
                    "formal_online_switched_count": 1,
                },
                {
                    "episode_id": "INCOMPLETE",
                    "majority_condition_changed": True,
                    "formal_online_switched_count": 1,
                },
                {
                    "episode_id": "FILTERED",
                    "majority_condition_changed": False,
                    "formal_online_switched_count": 0,
                },
            ]
        )

    def test_overlay_separates_clean_and_confounded_dynamic_without_phi_leak(self):
        result = revalidate_historical_evidence_roles(
            self._episodes(), self._replay(), plant=PLANT_CONFIG, routing_config={}
        ).set_index("episode_id")

        clean = result.loc["CLEAN"]
        self.assertTrue(bool(clean["mfac_dynamic_clean_eligible"]))
        self.assertTrue(bool(clean["mfac_dynamic_observation_eligible"]))
        self.assertFalse(bool(clean["mfac_disturbance_coupled_dynamic_eligible"]))
        # Direct LOCAL_GAIN remains fail-closed with uncalibrated limits.
        self.assertFalse(bool(clean["mfac_independent_local_gain_eligible"]))
        self.assertTrue(pd.isna(clean["mfac_phi_so2_event"]))
        self.assertTrue(pd.isna(clean["mfac_phi_ph_event"]))

        disturbance = result.loc["DIST"]
        self.assertFalse(bool(disturbance["mfac_dynamic_clean_eligible"]))
        self.assertTrue(bool(disturbance["mfac_disturbance_coupled_dynamic_eligible"]))
        self.assertTrue(bool(disturbance["mfac_dynamic_observation_eligible"]))
        self.assertFalse(bool(disturbance["mfac_independent_local_gain_eligible"]))
        self.assertTrue(pd.isna(disturbance["mfac_phi_so2_event"]))
        self.assertTrue(pd.isna(disturbance["mfac_phi_ph_event"]))

        incomplete = result.loc["INCOMPLETE"]
        self.assertFalse(bool(incomplete["mfac_dynamic_observation_eligible"]))

        filtered = result.loc["FILTERED"]
        self.assertFalse(bool(filtered["mfac_canonical_condition_changed"]))
        self.assertFalse(bool(filtered["mfac_dynamic_observation_eligible"]))

    def test_summary_audits_role_counts_and_phi_firewall(self):
        result = revalidate_historical_evidence_roles(
            self._episodes(), self._replay(), plant=PLANT_CONFIG, routing_config={}
        )
        summary = build_role_v2_summary(result)
        self.assertEqual(summary["input_episode_count"], 4)
        self.assertEqual(summary["original_valid_count"], 1)
        self.assertEqual(summary["original_invalid_count"], 3)
        self.assertEqual(summary["dynamic_clean_count"], 1)
        self.assertEqual(summary["disturbance_coupled_dynamic_count"], 1)
        self.assertEqual(summary["dynamic_observation_count"], 2)
        self.assertEqual(summary["local_gain_count"], 0)
        self.assertEqual(summary["disturbance_coupled_phi_non_null_count"], 0)
        self.assertEqual(summary["non_local_gain_phi_non_null_count"], 0)
        self.assertEqual(
            summary["disturbance_coupled_semantics"],
            "TEMPORAL_CONFOUNDED_OVERLAP_NOT_CAUSAL_GAIN",
        )

    def test_missing_process_transition_replay_evidence_fails_closed(self):
        replay = self._replay().loc[lambda frame: frame["episode_id"] != "DIST"]
        with self.assertRaises(KeyError):
            revalidate_historical_evidence_roles(
                self._episodes(), replay, plant=PLANT_CONFIG, routing_config={}
            )


if __name__ == "__main__":
    unittest.main()
