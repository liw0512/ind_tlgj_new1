import unittest

from system.model.map_control.mfac_model.observed_timing_extraction_profile import (
    ObservedTimingExtractionProfile,
)
from system.model.map_control.mfac_model.observed_timing_extractor import (
    OBSERVED_TIMING_EXTRACTOR_VERSION,
)


class Scheme2ObservedTimingExtractionProfileTest(unittest.TestCase):
    @staticmethod
    def reviewed_values():
        # Unit-test values only; not site calibration.
        return {
            "baseline_window_seconds": 300.0,
            "max_observation_seconds": 1200.0,
            "max_sample_gap_seconds": 30.0,
            "smoothing_window_samples": 3,
            "onset_abs_threshold": 0.05,
            "onset_sustain_samples": 3,
            "response_fraction_of_extremum": 0.8,
            "response_sustain_samples": 3,
            "min_response_abs_amplitude": 0.10,
            "min_baseline_samples": 12,
            "min_post_reach_samples": 30,
        }

    @classmethod
    def profile(cls, *, status="REVIEWED_MANUAL_ONLY", reviewed_so2=None, reviewed_ph=None, **overrides):
        values = {
            "design_id": "TIMING-DESIGN-1",
            "status": status,
            "activation_status": "NOT_ACTIVATABLE",
            "extractor_semantics": OBSERVED_TIMING_EXTRACTOR_VERSION,
            "review_candidate_so2": {"onset_abs_threshold": 0.03},
            "review_candidate_ph": {"onset_abs_threshold": 0.02},
            "reviewed_so2": cls.reviewed_values() if reviewed_so2 is None else reviewed_so2,
            "reviewed_ph": cls.reviewed_values() if reviewed_ph is None else reviewed_ph,
            "automatic_execution_allowed": False,
            "learning_permission": False,
            "dcs_write_enabled": False,
        }
        values.update(overrides)
        return ObservedTimingExtractionProfile(**values)

    def test_fully_reviewed_profile_builds_channel_config(self):
        profile = self.profile()
        self.assertTrue(profile.can_build_config("SO2"))
        self.assertTrue(profile.can_build_config("PH"))
        config = profile.build_config("SO2")
        self.assertEqual(config.baseline_window_seconds, 300.0)
        self.assertEqual(config.max_observation_seconds, 1200.0)
        self.assertEqual(config.smoothing_window_samples, 3)
        self.assertFalse(hasattr(profile, "execute"))
        with self.assertRaises(ValueError):
            profile.to_runtime_config()

    def test_incomplete_reviewed_parameters_cannot_borrow_candidates(self):
        reviewed = self.reviewed_values()
        reviewed["onset_abs_threshold"] = None
        profile = self.profile(
            status="INCOMPLETE_REVIEW_REQUIRED",
            reviewed_so2=reviewed,
        )
        self.assertFalse(profile.can_build_config("SO2"))
        self.assertIn("onset_abs_threshold", profile.missing_reviewed_keys("SO2"))
        self.assertEqual(profile.review_candidate_parameters("SO2")["onset_abs_threshold"], 0.03)
        with self.assertRaises(ValueError):
            profile.build_config("SO2")

    def test_reviewed_but_invalid_config_still_fails_canonical_validation(self):
        reviewed = self.reviewed_values()
        reviewed["response_fraction_of_extremum"] = 1.5
        profile = self.profile(reviewed_so2=reviewed)
        with self.assertRaises(ValueError):
            profile.build_config("SO2")

    def test_permission_flags_are_fail_closed(self):
        for field_name in (
            "automatic_execution_allowed",
            "learning_permission",
            "dcs_write_enabled",
        ):
            with self.assertRaises(ValueError):
                self.profile(**{field_name: True})

    def test_extractor_semantics_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.profile(extractor_semantics="OTHER")


if __name__ == "__main__":
    unittest.main()
