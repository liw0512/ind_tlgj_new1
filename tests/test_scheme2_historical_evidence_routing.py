import unittest

import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG
from system.model.map_control.mfac_model.historical_episode_engine.historical_evidence import (
    DYNAMIC_EVIDENCE,
    LOCAL_GAIN_EVIDENCE,
    SAFETY_EVIDENCE,
    HistoricalEvidenceRoutingConfig,
    enrich_historical_episode_frame,
)


class HistoricalEvidenceRoutingTest(unittest.TestCase):
    @staticmethod
    def episode(*, shape="STEP", delta_q=4.0, dose=1.0, ph_max=6.3, ph_out=False):
        return {
            "episode_id": "E1",
            "valid": True,
            "flow_effect_complete": True,
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

    def test_uncalibrated_local_limits_fail_closed_but_keep_dynamic_role(self):
        result = enrich_historical_episode_frame(
            pd.DataFrame([self.episode()]),
            self.history(),
            PLANT_CONFIG,
        ).iloc[0]
        self.assertFalse(bool(result["mfac_local_gain_eligible"]))
        self.assertTrue(bool(result["mfac_dynamic_evidence_eligible"]))
        self.assertIn(DYNAMIC_EVIDENCE, result["mfac_evidence_roles"])
        self.assertNotIn(LOCAL_GAIN_EVIDENCE, result["mfac_evidence_roles"])
        self.assertIn("LOCAL_GAIN_MAGNITUDE_LIMITS_UNCALIBRATED", result["mfac_evidence_reasons"])
        self.assertIn("dose_3m_m3", result.index)
        self.assertIn("dose_10m_m3", result.index)

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
        self.assertIn(LOCAL_GAIN_EVIDENCE, result["mfac_evidence_roles"])
        self.assertIn(DYNAMIC_EVIDENCE, result["mfac_evidence_roles"])

    def test_pulse_stays_dynamic_and_never_local_gain(self):
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
        self.assertTrue(bool(result["mfac_dynamic_evidence_eligible"]))

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
        self.assertIn("LOCAL_GAIN_PH_NOT_IN_OPERATING_ENVELOPE", result["mfac_evidence_reasons"])


if __name__ == "__main__":
    unittest.main()
