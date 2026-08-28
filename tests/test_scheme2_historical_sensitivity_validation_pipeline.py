import unittest

import numpy as np
import pandas as pd

from system.model.map_control.mfac_model.historical_model_based_gain_adapter import (
    HistoricalModelBasedGainAdapterConfig,
)
from system.model.map_control.mfac_model.historical_sensitivity_validation import (
    HistoricalSensitivityBlockedValidationConfig,
    HistoricalSensitivityModelSpec,
)
from system.model.map_control.mfac_model.historical_sensitivity_validation_pipeline import (
    build_historical_sensitivity_validation_report,
)
from system.model.map_control.mfac_model.model_based_local_gain_trainer import (
    ModelBasedLocalGainTrainerConfig,
)


class Scheme2HistoricalSensitivityValidationPipelineTest(unittest.TestCase):
    @staticmethod
    def trainer(surface=False):
        return ModelBasedLocalGainTrainerConfig(
            event_time_column="event_time",
            delta_q_column="delta_q",
            so2_response_column="so2_response",
            ph_response_column="ph_response",
            surface_feature_columns=(("inlet_so2", "inlet0"),) if surface else (),
            nuisance_columns=("duration_s", "inlet_pretrend", "so2_pretrend", "extra_volume_m3"),
            minimum_event_count=20,
            minimum_independent_days=4,
            bootstrap_iterations=30,
            minimum_physical_sign_probability=0.80,
            minimum_relative_delta_q_scale=0.03,
            huber_epsilon=1.35,
            huber_alpha=0.2,
            random_seed=19,
            confidence_reference_event_count=40,
            confidence_reference_day_count=8,
        )

    @classmethod
    def specs(cls):
        return (
            HistoricalSensitivityModelSpec("GRID_SCALAR", 0, cls.trainer(False)),
            HistoricalSensitivityModelSpec("GRID_INLET_SURFACE", 1, cls.trainer(True)),
        )

    @staticmethod
    def validation():
        return HistoricalSensitivityBlockedValidationConfig(
            fold_count=5,
            minimum_train_event_count=20,
            minimum_holdout_event_count=5,
            minimum_evaluated_folds=5,
            minimum_so2_holdout_direction_rate=0.90,
            minimum_ph_holdout_direction_rate=0.90,
            minimum_so2_center_fold_rate=0.80,
            minimum_ph_center_fold_rate=0.80,
            minimum_median_so2_zero_effect_skill=0.05,
            minimum_median_ph_zero_effect_skill=0.05,
            maximum_mean_extrapolation_rate=0.70,
        )

    @staticmethod
    def episodes():
        rng = np.random.default_rng(77)
        rows = []
        for label, grid, physical in (
            ("C13", "P13-S1", True),
            ("C12", "P12-S1", False),
        ):
            for day in range(10):
                for i in range(6):
                    dq = float(rng.uniform(35.0, 85.0))
                    duration_m = float(rng.uniform(5.0, 12.0))
                    inlet_pretrend = float(rng.normal(0.0, 0.2))
                    so2_pretrend = float(rng.normal(0.0, 0.1))
                    if physical:
                        so2 = -0.06 * dq + rng.normal(0.0, 0.08)
                        ph = 0.006 * dq + rng.normal(0.0, 0.008)
                    else:
                        so2 = 0.05 * dq + rng.normal(0.0, 0.08)
                        ph = -0.005 * dq + rng.normal(0.0, 0.008)
                    rows.append(
                        {
                            "episode_id": "%s-%02d-%02d" % (label, day, i),
                            "condition_snapshot_version": "v1",
                            "condition_label": label,
                            "base_condition_id": "B-%s" % label,
                            "anchor_grid_id": grid,
                            "start_grid_id": grid,
                            "policy_region_id": "R-%s" % grid,
                            "action_start_time": pd.Timestamp("2026-07-01")
                            + pd.Timedelta(days=day)
                            + pd.Timedelta(minutes=i * 60),
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
                            "before_condition_axis_1": 1700.0 + day * 15.0 + rng.normal(0.0, 8.0),
                            "before_outlet_so2": 10.0 + rng.normal(0.0, 0.4),
                            "flow_event_active_duration_minutes": duration_m,
                            "before_condition_axis_1_rate": inlet_pretrend,
                            "before_outlet_so2_rate": so2_pretrend,
                            "flow_event_extra_slurry_volume": dq * duration_m / 60.0,
                            "before_ph__xst": 6.2 + rng.normal(0.0, 0.02),
                            "delta_ph__xst": float(ph),
                            "evidence_weight": 1.0,
                        }
                    )
        return pd.DataFrame(rows)

    def test_pipeline_selects_only_blocked_validated_grid_model(self):
        report = build_historical_sensitivity_validation_report(
            self.episodes(),
            adapter_config=HistoricalModelBasedGainAdapterConfig(tower_id="xst"),
            model_specs=self.specs(),
            validation_config=self.validation(),
            include_pooled_fallback=True,
        )
        by_grid = {item.grid_id: item for item in report.grid_selections}
        self.assertEqual(
            by_grid["P13-S1"].status,
            "BLOCKED_VALIDATED_MODEL_REVIEW_CANDIDATE",
        )
        self.assertEqual(by_grid["P13-S1"].selected_model_label, "GRID_SCALAR")
        self.assertEqual(by_grid["P12-S1"].status, "NO_BLOCKED_VALIDATED_MODEL")
        self.assertEqual(report.selected_grid_model_count, 1)
        self.assertEqual(report.no_validated_grid_model_count, 1)
        payload = report.to_dict()
        self.assertFalse(payload["metadata"]["publish_runtime_map"])
        self.assertEqual(payload["activation_status"], "NOT_ACTIVATABLE")
        self.assertFalse(payload["learning_permission"])
        self.assertFalse(payload["residual_control_permission"])
        self.assertFalse(payload["dcs_write_permission"])


if __name__ == "__main__":
    unittest.main()
