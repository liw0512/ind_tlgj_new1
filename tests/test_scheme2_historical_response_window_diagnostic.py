# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest

import pandas as pd

from system.model.map_control.mfac_model import historical_response_window_diagnostic as diag


def _frame(start: str = "2026-01-01 00:00:00", points: int = 121) -> pd.DataFrame:
    timestamps = pd.date_range(start, periods=points, freq="10s")
    return pd.DataFrame({"date": timestamps})


class TestScheme2HistoricalResponseWindowDiagnostic(unittest.TestCase):
    def test_ph_and_so2_review_windows_are_independent(self):
        self.assertIn((5.0, 8.0), set(diag.PH_WINDOWS))
        self.assertIn((10.0, 12.0), set(diag.SO2_WINDOWS))
        self.assertNotEqual((5.0, 8.0), (10.0, 12.0))

    def test_legacy_shared_window_is_comparison_only_candidate(self):
        self.assertIn((3.0, 13.0), set(diag.PH_WINDOWS))
        self.assertIn((3.0, 13.0), set(diag.SO2_WINDOWS))

    def test_physical_direction_contract(self):
        self.assertIs(diag.physical_direction_ok("PH", 0.01), True)
        self.assertIs(diag.physical_direction_ok("PH", -0.01), False)
        self.assertIs(diag.physical_direction_ok("SO2", -0.5), True)
        self.assertIs(diag.physical_direction_ok("SO2", 0.5), False)

    def test_response_uses_window_median_and_resists_single_spike(self):
        frame = _frame(points=61)
        frame["xstjy_PH"] = 6.0
        baseline = frame.iloc[:31].copy()
        response = frame.iloc[31:].copy()
        response["xstjy_PH"] = 6.1
        response.loc[response.index[10], "xstjy_PH"] = 99.0

        result = diag.response_metrics(
            baseline=baseline,
            response=response,
            timestamp_column="date",
            signal_column="xstjy_PH",
            action_start=frame.iloc[30]["date"],
            delta_q=2.0,
        )

        self.assertAlmostEqual(float(result["response_median"]), 6.1, places=10)
        self.assertGreater(float(result["raw_phi"]), 0.0)

    def test_pretrend_correction_recovers_positive_ph_effect_from_falling_process(self):
        frame = _frame(points=91)
        action_start = frame.iloc[30]["date"]
        minutes = (frame["date"] - action_start).dt.total_seconds() / 60.0
        frame["xstjy_PH"] = 6.0 - 0.02 * minutes

        baseline = frame.iloc[:31].copy()
        response = frame.iloc[60:79].copy()
        response["xstjy_PH"] = response["xstjy_PH"] + 0.15

        result = diag.response_metrics(
            baseline=baseline,
            response=response,
            timestamp_column="date",
            signal_column="xstjy_PH",
            action_start=action_start,
            delta_q=2.0,
        )

        self.assertLess(float(result["raw_delta"]), 0.0)
        self.assertGreater(float(result["corrected_delta"]), 0.0)
        self.assertIs(
            diag.physical_direction_ok("PH", result["corrected_phi"]), True
        )

    def test_pretrend_correction_recovers_negative_so2_effect_from_rising_process(self):
        frame = _frame(points=121)
        action_start = frame.iloc[30]["date"]
        minutes = (frame["date"] - action_start).dt.total_seconds() / 60.0
        frame["jyq_SO2"] = 20.0 + 0.5 * minutes

        baseline = frame.iloc[:31].copy()
        response = frame.iloc[90:103].copy()
        response["jyq_SO2"] = response["jyq_SO2"] - 6.0

        result = diag.response_metrics(
            baseline=baseline,
            response=response,
            timestamp_column="date",
            signal_column="jyq_SO2",
            action_start=action_start,
            delta_q=3.0,
        )

        self.assertLess(float(result["corrected_delta"]), 0.0)
        self.assertIs(
            diag.physical_direction_ok("SO2", result["corrected_phi"]), True
        )

    def test_yyq_ll_is_not_used_as_formal_feature_in_event_audit(self):
        class Event:
            start_time = pd.Timestamp("2026-01-01 00:05:00")
            end_time = pd.Timestamp("2026-01-01 00:06:00")
            final_delta_flow = 3.0
            baseline_flow = 20.0
            final_flow = 23.0
            active_duration_minutes = 1.0
            transition_count = 1
            complete = True

        frame = pd.DataFrame(
            {
                "date": pd.date_range(
                    "2026-01-01 00:00:00", periods=121, freq="10s"
                ),
                "yyq_SO2": 1500.0,
                # Intentionally no yyq_LL column.
            }
        )

        audit = diag.build_event_audit(
            frame,
            [Event()],
            timestamp_column="date",
            baseline_minutes=5.0,
            max_response_end_minutes=13.0,
            min_abs_delta_q=2.0,
            min_operating_flow=5.0,
        )

        self.assertEqual(len(audit), 1)
        self.assertIs(
            bool(audit.iloc[0]["yyq_ll_used_as_formal_feature"]), False
        )


if __name__ == "__main__":
    unittest.main()
