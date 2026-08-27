import unittest

from system.model.map_control.mfac_model.trajectory_counterfactual import (
    StaircaseTrajectoryCandidate,
    TrajectoryCounterfactualComparison,
    TrajectoryCounterfactualMetrics,
    build_equal_dose_candidate,
)


class Scheme2TrajectoryCounterfactualTest(unittest.TestCase):
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
