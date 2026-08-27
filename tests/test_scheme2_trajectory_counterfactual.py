import unittest

from system.model.map_control.mfac_model.trajectory_counterfactual import (
    HistoricalTrajectorySupport,
    TrajectoryCounterfactualComparison,
    TrajectoryCounterfactualMetrics,
    assess_historical_support,
    build_equal_dose_candidate,
)


class Scheme2TrajectoryCounterfactualTest(unittest.TestCase):
    @staticmethod
    def historical_support():
        return HistoricalTrajectorySupport(
            sustained_extra_flow_p05_m3_h=57.877,
            sustained_extra_flow_p95_m3_h=88.106,
            action_duration_p05_seconds=320.0,
            action_duration_p95_seconds=730.0,
            max_observed_proactive_advance_seconds=0.0,
            source_event_count=725,
        )

    def test_equal_dose_candidate_matches_reference_without_runtime_authority(self):
        reference = 7.5
        candidate = build_equal_dose_candidate(
            "ST3",
            [20.0, 30.0, 40.0],
            300.0,
            reference_extra_volume_m3=reference,
            advance_seconds=600.0,
            dose_match_tolerance_m3=1e-9,
        )
        self.assertAlmostEqual(candidate.total_extra_volume_m3, reference)
        self.assertEqual(candidate.peak_extra_flow_m3_h, 40.0)
        self.assertEqual(candidate.total_duration_seconds, 900.0)
        self.assertTrue(candidate.shadow_only)

    def test_equal_dose_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            build_equal_dose_candidate(
                "BAD",
                [20.0, 30.0, 40.0],
                300.0,
                reference_extra_volume_m3=8.0,
                advance_seconds=600.0,
                dose_match_tolerance_m3=0.05,
            )

    def test_current_low_flow_proactive_staircase_is_out_of_support(self):
        candidate = build_equal_dose_candidate(
            "ST3_OOD",
            [20.0, 30.0, 40.0],
            300.0,
            reference_extra_volume_m3=7.5,
            advance_seconds=600.0,
            dose_match_tolerance_m3=1e-9,
        )
        result = assess_historical_support(candidate, self.historical_support())
        self.assertEqual(result.stage_level_support_fraction, 0.0)
        self.assertFalse(result.sustained_level_supported)
        self.assertFalse(result.duration_supported)
        self.assertFalse(result.proactive_advance_supported)
        self.assertTrue(result.extrapolation_required)
        self.assertFalse(result.eligible_for_step_calibration_evidence)
        self.assertIn(
            "SUSTAINED_EXTRA_FLOW_OUT_OF_HISTORICAL_SUPPORT",
            result.reasons,
        )
        self.assertIn("TOTAL_DURATION_OUT_OF_HISTORICAL_SUPPORT", result.reasons)
        self.assertIn("PROACTIVE_ADVANCE_OUT_OF_HISTORICAL_SUPPORT", result.reasons)

    def test_synthetic_historically_supported_shape_can_pass_support_gate(self):
        candidate = build_equal_dose_candidate(
            "SUPPORTED_SHAPE",
            [60.0],
            360.0,
            reference_extra_volume_m3=6.0,
            advance_seconds=0.0,
            dose_match_tolerance_m3=1e-9,
        )
        result = assess_historical_support(candidate, self.historical_support())
        self.assertTrue(result.sustained_level_supported)
        self.assertTrue(result.duration_supported)
        self.assertTrue(result.proactive_advance_supported)
        self.assertFalse(result.extrapolation_required)
        self.assertTrue(result.eligible_for_step_calibration_evidence)
        self.assertEqual(
            result.metadata["support_flow_semantics"],
            "EXTRA_FLOW_ABOVE_EVENT_BASELINE",
        )

    def test_counterfactual_comparison_is_never_activatable(self):
        metrics = TrajectoryCounterfactualMetrics(
            candidate_id="ST3",
            model_id="MODEL-AUDIT",
            outlet_so2_peak=20.0,
            outlet_so2_exceedance_seconds=0.0,
            outlet_so2_integral_error=1.0,
            ph_peak=6.35,
            ph_over_operating_max_seconds=0.0,
            ph_over_safe_max_seconds=0.0,
            max_supply_flow_m3_h=40.0,
            total_extra_volume_m3=7.5,
            valid=True,
        )
        comparison = TrajectoryCounterfactualComparison(
            reference_id="HUMAN_PULSE",
            candidate_metrics=(metrics,),
        )
        self.assertFalse(comparison.activatable)
        self.assertEqual(comparison.ranked_valid_candidates()[0].candidate_id, "ST3")
        with self.assertRaises(ValueError):
            TrajectoryCounterfactualComparison(
                reference_id="HUMAN_PULSE",
                candidate_metrics=(metrics,),
                activatable=True,
            )

    def test_safety_first_ranking(self):
        safe = TrajectoryCounterfactualMetrics(
            candidate_id="SAFE",
            model_id="M",
            outlet_so2_peak=25.0,
            outlet_so2_exceedance_seconds=60.0,
            outlet_so2_integral_error=2.0,
            ph_peak=6.35,
            ph_over_operating_max_seconds=0.0,
            ph_over_safe_max_seconds=0.0,
            max_supply_flow_m3_h=40.0,
            total_extra_volume_m3=7.5,
            valid=True,
        )
        unsafe = TrajectoryCounterfactualMetrics(
            candidate_id="UNSAFE",
            model_id="M",
            outlet_so2_peak=10.0,
            outlet_so2_exceedance_seconds=0.0,
            outlet_so2_integral_error=0.2,
            ph_peak=6.9,
            ph_over_operating_max_seconds=300.0,
            ph_over_safe_max_seconds=30.0,
            max_supply_flow_m3_h=35.0,
            total_extra_volume_m3=7.5,
            valid=True,
        )
        comparison = TrajectoryCounterfactualComparison(
            reference_id="HUMAN",
            candidate_metrics=(unsafe, safe),
        )
        self.assertEqual(
            [item.candidate_id for item in comparison.ranked_valid_candidates()],
            ["SAFE", "UNSAFE"],
        )


if __name__ == "__main__":
    unittest.main()
