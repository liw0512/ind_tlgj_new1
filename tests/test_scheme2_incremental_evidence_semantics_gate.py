import unittest

import pandas as pd

from system.model.map_control.mfac_model.historical_episode_engine.historical_evidence import (
    HISTORICAL_EVIDENCE_SEMANTICS_VERSION,
)
from system.model.map_control.mfac_model.offline_version_training import (
    _validate_previous_episode_evidence_semantics,
)


class Scheme2IncrementalEvidenceSemanticsGateTest(unittest.TestCase):
    @staticmethod
    def _canonical_row(**overrides):
        row = {
            "episode_id": "episode-1",
            "mfac_evidence_semantics_version": HISTORICAL_EVIDENCE_SEMANTICS_VERSION,
            "mfac_canonical_condition_changed": False,
            "mfac_dynamic_clean_eligible": True,
            "mfac_disturbance_coupled_dynamic_eligible": False,
            "mfac_dynamic_observation_eligible": True,
        }
        row.update(overrides)
        return row

    def test_pre_v2_1_store_is_rejected_and_requires_initial_rebuild(self):
        legacy = pd.DataFrame(
            [
                {
                    "episode_id": "legacy-1",
                    "valid": True,
                    "mfac_dynamic_evidence_eligible": True,
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "INITIAL baseline"):
            _validate_previous_episode_evidence_semantics(legacy)

    def test_exact_v2_1_canonical_store_is_accepted(self):
        canonical = pd.DataFrame([self._canonical_row()])
        _validate_previous_episode_evidence_semantics(canonical)

    def test_mixed_or_wrong_semantics_are_rejected(self):
        mixed = pd.DataFrame(
            [
                self._canonical_row(episode_id="new"),
                self._canonical_row(
                    episode_id="old",
                    mfac_evidence_semantics_version="SCHEME2_HISTORICAL_EVIDENCE_V2",
                ),
            ]
        )
        with self.assertRaisesRegex(ValueError, "unsupported/mixed"):
            _validate_previous_episode_evidence_semantics(mixed)

    def test_empty_previous_store_is_safe_noop(self):
        _validate_previous_episode_evidence_semantics(pd.DataFrame())


if __name__ == "__main__":
    unittest.main()
