import unittest

import numpy as np
import pandas as pd

from system.model.map_control.mfac_model.historical_sensitivity_validation import (
    HistoricalSensitivityBlockedValidationConfig,
    HistoricalSensitivityModelSpec,
    select_blocked_validated_model,
    validate_model_based_local_gain_blocked,
)
from system.model.map_control.mfac_model.model_based_local_gain_trainer import (
    ModelBasedLocalGainTrainerConfig,
)


class Scheme2HistoricalSensitivityBlockedValidationTest(unittest.TestCase):
    @staticmethod
    def trainer_config(*, surface=False):
        return ModelBasedLocalGainTrainerConfig(
            event_time_column="event_time",
            delta_q_column="delta_q",
            so2_response_column="so2_response",
            ph_response_column="ph_response",
            surface_feature_columns=(("inlet_so2", "inlet0"),) if surface else (),
            nuisance_columns=(),
            minimum_event_count=20,
            minimum_independent_days=4,
            bootstrap_iterations=30,
            minimum_physical_sign_probability=0.80,
            minimum_relative_delta_q_scale=0.03,
            huber_epsilon=1.35,
            huber_alpha=0.2,
            random_seed=17,
            confidence_reference_event_count=40,
            confidence_reference_day_count=8,
        )

    @staticmethod
    def validation_config():
        return HistoricalSensitivityBlockedValidationConfig(
            fold_count=5,
            minimum_train_event_count=20,
            minimum_holdout_event_count=6,
            minimum_evaluated_folds=5,
            minimum_so2_holdout_direction_rate=0.90,
            minimum_ph_holdout_direction_rate=0.90,
            minimum_so2_center_fold_rate=0.80,
            minimum_ph_center_fold_rate=0.80,
            minimum_median_so2_zero_effect_skill=0.05,
            minimum_median_ph_zero_effect_skill=0.05,
            maximum_mean_extrapolation_rate=0.60,
        )

    @staticmethod
    def frame(*, physical=True):
        rng = np.random.default_rng(41 if physical else 42)
        rows = []
        for day in range(10):
            for i in range(8):
                dq = float(rng.uniform(35.0, 85.0))
                inlet0 = float(1700.0 + 20.0 * day + rng.normal(0.0, 10.0))
                so2 = (-0.06 if physical else 0.05) * dq + rng.normal(0.0, 0.08)
                ph = (0.006 if physical else -0.005) * dq + rng.normal(0.0, 0.008)
                rows.append(
                    {
                        "event_time": pd.Timestamp("2026-07-01")
                        + pd.Timedelta(days=day)
                        + pd.Timedelta(minutes=i * 30),
                        "delta_q": dq,
                        "so2_response": float(so2),
                        "ph_response": float(ph),
                        "inlet0": inlet0,
                    }
                )
        return pd.DataFrame(rows)

    def test_stable_physical_scalar_gain_passes_date_blocked_validation(self):
        result = validate_model_based_local_gain_blocked(
            self.frame(),
            self.trainer_config(surface=False),
            self.validation_config(),
            condition_snapshot_version="v1",
            mfac_context_id="MFAC-COND-C13",
            grid_id="P13-S1",
            model_label="GRID_SCALAR",
        )
        self.assertEqual(result.status, "BLOCKED_VALIDATION_REVIEW_CANDIDATE")
        self.assertEqual(result.evaluated_fold_count, 5)
        self.assertGreaterEqual(result.so2_holdout_direction_rate, 0.90)
        self.assertGreaterEqual(result.ph_holdout_direction_rate, 0.90)
        self.assertGreater(result.median_so2_zero_effect_skill, 0.05)
        self.assertGreater(result.median_ph_zero_effect_skill, 0.05)
        payload = result.to_dict()
        self.assertEqual(payload["activation_status"], "NOT_ACTIVATABLE")
        self.assertFalse(payload["learning_permission"])
        self.assertFalse(payload["residual_control_permission"])
        self.assertFalse(payload["dcs_write_permission"])

    def test_wrong_physical_direction_is_rejected_across_date_blocks(self):
        result = validate_model_based_local_gain_blocked(
            self.frame(physical=False),
            self.trainer_config(surface=False),
            self.validation_config(),
            condition_snapshot_version="v1",
            mfac_context_id="MFAC-COND-C12",
            grid_id="P12-S1",
            model_label="GRID_SCALAR",
        )
        self.assertEqual(result.status, "BLOCKED_VALIDATION_REJECTED")
        self.assertIn("SO2_HOLDOUT_DIRECTION_UNSTABLE", result.reason_codes)
        self.assertIn("PH_HOLDOUT_DIRECTION_UNSTABLE", result.reason_codes)

    def test_model_selection_chooses_simplest_passing_spec(self):
        specs = (
            HistoricalSensitivityModelSpec(
                label="GRID_SCALAR",
                complexity_rank=0,
                trainer_config=self.trainer_config(surface=False),
            ),
            HistoricalSensitivityModelSpec(
                label="GRID_INLET_SURFACE",
                complexity_rank=1,
                trainer_config=self.trainer_config(surface=True),
            ),
        )
        result = select_blocked_validated_model(
            self.frame(),
            specs,
            self.validation_config(),
            condition_snapshot_version="v1",
            mfac_context_id="MFAC-COND-C13",
            grid_id="P13-S1",
        )
        self.assertEqual(result.status, "BLOCKED_VALIDATED_MODEL_REVIEW_CANDIDATE")
        self.assertEqual(result.selected_model_label, "GRID_SCALAR")
        self.assertEqual(result.selected_complexity_rank, 0)
        self.assertEqual(result.to_dict()["selection_policy"], "SIMPLEST_PASSING")


if __name__ == "__main__":
    unittest.main()
