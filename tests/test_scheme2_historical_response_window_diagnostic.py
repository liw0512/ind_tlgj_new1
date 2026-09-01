# -*- coding: utf-8 -*-

from __future__ import annotations

import unittest

import pandas as pd

from system.model.map_control.mfac_model import historical_response_window_diagnostic as diag


def _frame(start: str = "2026-01-01 00:00:00", points: int = 181) -> pd.DataFrame:
    timestamps = pd.date_range(start, periods=points, freq="10s")
    return pd.DataFrame({"date": timestamps})


class _Event:
    def __init__(
        self,
        *,
        baseline_flow: float,
        final_flow: float,
        start: str = "2026-01-01 00:05:00",
        end: str = "2026-01-01 00:06:00",
        complete: bool = True,
        transition_count: int = 1,
        duration_minutes: float = 1.0,
    ) -> None:
        self.start_time = pd.Timestamp(start)
        self.end_time = pd.Timestamp(end)
        self.baseline_flow = float(baseline_flow)
        self.final_flow = float(final_flow)
        self.final_delta_flow = float(final_flow - baseline_flow)
        self.active_duration_minutes = float(duration_minutes)
        self.transition_count = int(transition_count)
        self.complete = bool(complete)

        # Optional detector-classifier fields. They make this fixture a valid
        # SupplyFlowEvent-like object for classify_supply_flow_event().
        self.peak_flow = max(self.baseline_flow, self.final_flow)
        self.trough_flow = min(self.baseline_flow, self.final_flow)
        self.peak_delta_flow = self.peak_flow - self.baseline_flow
        self.max_abs_delta_flow = abs(self.final_delta_flow)
        self.extra_slurry_volume = 0.0
        self.deficit_slurry_volume = 0.0
        self.signed_slurry_volume = 0.0
        self.time_to_extreme_minutes = self.active_duration_minutes
        self.time_from_extreme_to_end_minutes = 0.0
        self.baseline_noise_sigma = 0.01
        self.trigger_deadband = 0.1


def _audit_frame() -> pd.DataFrame:
    frame = _frame()
    frame["yyq_SO2"] = 1500.0
    return frame


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
        self.assertIs(diag.physical_direction_ok("PH", result["corrected_phi"]), True)

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
        self.assertIs(diag.physical_direction_ok("SO2", result["corrected_phi"]), True)

    def _audit_one(self, event: _Event) -> pd.Series:
        audit = diag.build_event_audit(
            _audit_frame(),
            [event],
            timestamp_column="date",
            baseline_minutes=5.0,
            max_response_end_minutes=13.0,
            min_abs_delta_q=2.0,
            min_operating_flow=5.0,
            max_timing_action_duration_minutes=20.0,
            max_timing_transition_count=3,
        )
        self.assertEqual(len(audit), 1)
        return audit.iloc[0]

    def test_startup_step_is_timing_evidence_but_not_local_gain(self):
        row = self._audit_one(_Event(baseline_flow=0.0, final_flow=60.0))
        self.assertEqual(row["flow_evidence_class"], "STARTUP_STEP")
        self.assertIs(bool(row["timing_eligible"]), True)
        self.assertIs(bool(row["local_gain_eligible"]), False)

    def test_shutdown_step_is_timing_evidence_but_not_local_gain(self):
        row = self._audit_one(_Event(baseline_flow=60.0, final_flow=0.0))
        self.assertEqual(row["flow_evidence_class"], "SHUTDOWN_STEP")
        self.assertIs(bool(row["timing_eligible"]), True)
        self.assertIs(bool(row["local_gain_eligible"]), False)

    def test_operating_to_operating_step_can_be_local_gain_candidate(self):
        row = self._audit_one(_Event(baseline_flow=20.0, final_flow=23.0))
        self.assertEqual(row["flow_evidence_class"], "LOCAL_STEP")
        self.assertIs(bool(row["timing_eligible"]), True)
        self.assertIs(bool(row["local_gain_eligible"]), True)

    def test_low_flow_noise_is_not_timing_evidence(self):
        row = self._audit_one(_Event(baseline_flow=0.0, final_flow=3.0))
        self.assertEqual(row["flow_evidence_class"], "LOW_FLOW_TRANSITION")
        self.assertIs(bool(row["timing_eligible"]), False)

    def test_long_multistage_action_is_not_timing_evidence(self):
        row = self._audit_one(
            _Event(
                baseline_flow=60.0,
                final_flow=0.0,
                transition_count=6,
                duration_minutes=50.0,
            )
        )
        self.assertIs(bool(row["timing_eligible"]), False)
        self.assertIn("ACTION_DURATION_TOO_LONG_FOR_TIMING", row["timing_reasons"])
        self.assertIn("TOO_MANY_TRANSITIONS_FOR_TIMING", row["timing_reasons"])

    def test_yyq_ll_is_not_used_as_formal_feature_in_event_audit(self):
        row = self._audit_one(_Event(baseline_flow=20.0, final_flow=23.0))
        self.assertIs(bool(row["yyq_ll_used_as_formal_feature"]), False)


if __name__ == "__main__":
    unittest.main()
