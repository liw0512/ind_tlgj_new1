import unittest

import numpy as np
import pandas as pd

from system.model.map_control.mfac_model.historical_model_based_gain_adapter import (
    HistoricalModelBasedGainAdapterConfig,
    adapt_historical_episodes_for_model_based_gain,
)
from system.model.map_control.mfac_model.historical_sensitivity_training_pipeline import (
    build_historical_sensitivity_training_report,
)
from system.model.map_control.mfac_model.model_based_local_gain_trainer import (
    ModelBasedLocalGainTrainerConfig,
)


class Scheme2HistoricalSensitivityTrainingPipelineTest(unittest.TestCase):
    @staticmethod
    def adapter_config():
        return HistoricalModelBasedGainAdapterConfig(tower_id="xst")

    @staticmethod
    def trainer_config():
        return ModelBasedLocalGainTrainerConfig(
            event_time_column="event_time",
            delta_q_column="delta_q",
            so2_response_column="so2_response",
            ph_response_column="ph_response",
            surface_feature_columns=(),
            nuisance_columns=(
                "duration_s",
                "inlet_pretrend",
                "so2_pretrend",
                "extra_volume_m3",
            ),
            minimum_event_count=20,
            minimum_independent_days=3,
            bootstrap_iterations=30,
            minimum_physical_sign_probability=0.80,
            minimum_relative_delta_q_scale=0.03,
            huber_epsilon=1.35,
            huber_alpha=0.2,
            random_seed=13,
            confidence_reference_event_count=30,
            confidence_reference_day_count=5,
        )

    @staticmethod
    def episodes():
        rng = np.random.default_rng(21)
        rows = []
        for group, physical in (("C13", True), ("C12", False)):
            grid = "P13-S1" if physical else "P12-S1"
            for i in range(35):
                dq = float(rng.uniform(45.0, 90.0))
                inlet_pretrend = float(rng.normal(0.0, 0.3))
                so2_pretrend = float(rng.normal(0.0, 0.2))
                duration_m = float(rng.uniform(5.0, 12.0))
                extra_volume = float(dq * duration_m / 60.0)
                if physical:
                    so2 = -0.055 * dq + 0.3 * so2_pretrend + rng.normal(0.0, 0.15)
                    ph = 0.0065 * dq + rng.normal(0.0, 0.012)
                else:
                    so2 = 0.045 * dq + rng.normal(0.0, 0.15)
                    ph = -0.005 * dq + rng.normal(0.0, 0.012)
                rows.append(
                    {
                        "episode_id": "%s-%03d" % (group, i),
                        "condition_snapshot_version": "v1",
                        "condition_label": group,
                        "base_condition_id": "B-%s" % group,
                        "anchor_grid_id": grid,
                        "start_grid_id": grid,
                        "policy_region_id": "R-%s" % grid,
                        "action_start_time": pd.Timestamp("2026-07-01")
                        + pd.Timedelta(days=i % 7)
                        + pd.Timedelta(minutes=i * 10),
                        "valid": True,
                        "flow_effect_complete": True,
                        "mfac_dynamic_evidence_eligible": True,
                        "mfac_safety_evidence": False,
                        "condition_valid": True,
                        "followup_action_in_response": False,
                        "condition_remapped": False,
                        "grid_change_count": 0,
                        "condition_label_change_count": 0,
                        "flow_shape": "PULSE",
                        "flow_event_final_delta_flow": dq,
                        "delta_outlet_so2": float(so2),
                        "before_condition_axis_1": 1700.0 + rng.normal(0.0, 30.0),
                        "before_outlet_so2": 10.0 + rng.normal(0.0, 0.5),
                        "flow_event_active_duration_minutes": duration_m,
                        "before_condition_axis_1_rate": inlet_pretrend,
                        "before_outlet_so2_rate": so2_pretrend,
                        "flow_event_extra_slurry_volume": extra_volume,
                        "before_ph__xst": 6.2 + rng.normal(0.0, 0.02),
                        "delta_ph__xst": float(ph),
                        "evidence_weight": 1.0,
                    }
                )
        # A physically shaped event with a pH safety excursion must still be
        # excluded from model-based LOCAL_GAIN training.
        unsafe = dict(rows[0])
        unsafe["episode_id"] = "UNSAFE-001"
        unsafe["mfac_safety_evidence"] = True
        rows.append(unsafe)
        return pd.DataFrame(rows)

    def test_adapter_uses_dynamic_non_safety_canonical_events_only(self):
        frame, summary = adapt_historical_episodes_for_model_based_gain(
            self.episodes(),
            self.adapter_config(),
        )
        self.assertEqual(summary.input_episode_count, 71)
        self.assertEqual(summary.accepted_event_count, 70)
        self.assertEqual(
            summary.rejection_counts[
                "SAFETY_EVIDENCE_EXCLUDED_FROM_LOCAL_GAIN_MODEL"
            ],
            1,
        )
        self.assertEqual(set(frame["mfac_context_id"]), {"MFAC-COND-C12", "MFAC-COND-C13"})
        self.assertEqual(set(frame["grid_id"]), {"P12-S1", "P13-S1"})

    def test_pipeline_keeps_good_grid_and_rejects_wrong_direction_grid(self):
        report = build_historical_sensitivity_training_report(
            self.episodes(),
            adapter_config=self.adapter_config(),
            trainer_config=self.trainer_config(),
            include_pooled_fallback=True,
        )
        by_grid = {item.grid_id: item for item in report.grid_candidates}
        self.assertEqual(by_grid["P13-S1"].status, "MODEL_BASED_LOCAL_GAIN_REVIEW_CANDIDATE")
        self.assertLess(by_grid["P13-S1"].phi_so2_center, 0.0)
        self.assertGreater(by_grid["P13-S1"].phi_ph_center, 0.0)
        self.assertEqual(by_grid["P12-S1"].status, "MODEL_BASED_LOCAL_GAIN_REJECTED")
        self.assertEqual(len(report.pooled_candidates), 1)
        payload = report.to_dict()
        self.assertEqual(payload["activation_status"], "NOT_ACTIVATABLE")
        self.assertFalse(payload["learning_permission"])
        self.assertFalse(payload["residual_control_permission"])
        self.assertFalse(payload["dcs_write_permission"])
        self.assertFalse(payload["metadata"]["publish_runtime_map"])


if __name__ == "__main__":
    unittest.main()
