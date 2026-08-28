import unittest

import numpy as np
import pandas as pd

from system.model.map_control.mfac_model.model_based_local_gain_trainer import (
    ModelBasedLocalGainTrainerConfig,
    fit_model_based_local_gain,
)


class Scheme2ModelBasedLocalGainTrainerTest(unittest.TestCase):
    @staticmethod
    def config():
        return ModelBasedLocalGainTrainerConfig(
            event_time_column="event_time",
            delta_q_column="delta_q",
            so2_response_column="so2_response",
            ph_response_column="ph_response",
            surface_feature_columns=(
                ("inlet_so2", "inlet0"),
                ("ph", "ph0"),
                ("outlet_so2", "out0"),
                ("gas_flow", "gas0"),
            ),
            nuisance_columns=(
                "duration_s",
                "inlet_change",
                "gas_change",
                "so2_pretrend",
                "ph_pretrend",
            ),
            minimum_event_count=20,
            minimum_independent_days=3,
            bootstrap_iterations=50,
            minimum_physical_sign_probability=0.80,
            minimum_relative_delta_q_scale=0.05,
            huber_epsilon=1.35,
            huber_alpha=0.2,
            random_seed=7,
            confidence_reference_event_count=40,
            confidence_reference_day_count=5,
        )

    @staticmethod
    def frame(*, physical=True):
        rng = np.random.default_rng(11)
        n = 80
        day = np.arange(n) % 8
        delta_q = rng.uniform(45.0, 90.0, n)
        inlet0 = rng.normal(1700.0, 80.0, n)
        ph0 = rng.normal(6.20, 0.06, n)
        out0 = rng.normal(10.0, 1.2, n)
        gas0 = rng.normal(850000.0, 35000.0, n)
        duration = rng.uniform(320.0, 780.0, n)
        inlet_change = rng.normal(0.0, 70.0, n)
        gas_change = rng.normal(0.0, 25000.0, n)
        so2_pretrend = rng.normal(0.0, 0.4, n)
        ph_pretrend = rng.normal(0.0, 0.015, n)

        inlet_z = (inlet0 - np.median(inlet0)) / 80.0
        ph_z = (ph0 - np.median(ph0)) / 0.06
        if physical:
            phi_so2 = -0.055 - 0.010 * inlet_z
            phi_ph = 0.007 + 0.0015 * ph_z
        else:
            phi_so2 = 0.045 + 0.005 * inlet_z
            phi_ph = -0.006 - 0.001 * ph_z

        so2_response = (
            delta_q * phi_so2
            + 0.003 * inlet_change
            + 0.00001 * gas_change
            + 0.4 * so2_pretrend
            + rng.normal(0.0, 0.35, n)
        )
        ph_response = (
            delta_q * phi_ph
            - 0.25 * ph_pretrend
            + rng.normal(0.0, 0.025, n)
        )
        return pd.DataFrame(
            {
                "event_time": pd.Timestamp("2026-07-01")
                + pd.to_timedelta(day, unit="D")
                + pd.to_timedelta(np.arange(n) * 10, unit="m"),
                "delta_q": delta_q,
                "so2_response": so2_response,
                "ph_response": ph_response,
                "inlet0": inlet0,
                "ph0": ph0,
                "out0": out0,
                "gas0": gas0,
                "duration_s": duration,
                "inlet_change": inlet_change,
                "gas_change": gas_change,
                "so2_pretrend": so2_pretrend,
                "ph_pretrend": ph_pretrend,
            }
        )

    def test_physical_historical_events_build_review_candidate_surface(self):
        result = fit_model_based_local_gain(
            self.frame(physical=True),
            self.config(),
            condition_snapshot_version="v1",
            mfac_context_id="MFAC-COND-C17",
            grid_id="P17-S1",
        )
        self.assertTrue(result.publishable_for_review)
        self.assertLess(result.phi_so2_center, 0.0)
        self.assertGreater(result.phi_ph_center, 0.0)
        self.assertGreaterEqual(result.so2_physical_sign_probability, 0.80)
        self.assertGreaterEqual(result.ph_physical_sign_probability, 0.80)
        self.assertIn("inlet_so2", result.phi_so2_surface_coefficients)
        self.assertIn("ph", result.phi_ph_surface_coefficients)
        payload = result.to_dict()
        self.assertEqual(payload["activation_status"], "NOT_ACTIVATABLE")
        self.assertFalse(payload["learning_permission"])
        self.assertFalse(payload["residual_control_permission"])
        self.assertFalse(payload["dcs_write_permission"])

    def test_operator_confounded_wrong_direction_is_rejected_not_published(self):
        result = fit_model_based_local_gain(
            self.frame(physical=False),
            self.config(),
            condition_snapshot_version="v1",
            mfac_context_id="MFAC-COND-C12",
            grid_id="P12-S1",
        )
        self.assertFalse(result.publishable_for_review)
        self.assertEqual(result.status, "MODEL_BASED_LOCAL_GAIN_REJECTED")
        self.assertTrue(
            "SO2_MARGINAL_DIRECTION_NOT_STABLE" in result.reason_codes
            or "PH_MARGINAL_DIRECTION_NOT_STABLE" in result.reason_codes
        )

    def test_too_few_events_remain_explicitly_insufficient(self):
        result = fit_model_based_local_gain(
            self.frame(physical=True).head(10),
            self.config(),
            condition_snapshot_version="v1",
            mfac_context_id="MFAC-COND-C1",
            grid_id="P1-S1",
        )
        self.assertEqual(result.status, "INSUFFICIENT_EVIDENCE")
        self.assertIn("INSUFFICIENT_EVENT_COUNT", result.reason_codes)
        self.assertIsNone(result.phi_so2_center)
        self.assertIsNone(result.phi_ph_center)


if __name__ == "__main__":
    unittest.main()
