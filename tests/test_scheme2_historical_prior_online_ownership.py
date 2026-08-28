import unittest

from system.model.map_control.mfac_model.historical_sensitivity_map import (
    HistoricalSensitivityMap,
    HistoricalSensitivityMapConfig,
    HistoricalSensitivityQuery,
    HistoricalSensitivitySurface,
)
from system.model.map_control.mfac_model.mfac_schema import MFACRuntimeState
from system.model.map_control.mfac_model.trajectory_coordinator import (
    Scheme2TrajectoryShadowCoordinator,
)


class Scheme2HistoricalPriorOnlineOwnershipTest(unittest.TestCase):
    @staticmethod
    def mapping():
        surface = HistoricalSensitivitySurface(
            profile_id="REVIEWED-SCALAR",
            condition_snapshot_version="v1",
            mfac_context_id="MFAC-COND-C1",
            grid_id="P10-S1",
            phi_so2_prior=-0.4,
            phi_ph_prior=0.02,
            confidence_so2=0.8,
            confidence_ph=0.7,
            event_count=30,
            independent_days=5,
            metadata={
                "runtime_prior_reviewed": True,
                "runtime_prior_allowed": True,
                "model_complexity": "SCALAR",
            },
        )
        return HistoricalSensitivityMap(
            "v1",
            [surface],
            HistoricalSensitivityMapConfig(
                max_neighbor_grid_distance=2,
                neighbor_confidence_penalty=0.8,
                pooled_confidence_penalty=0.35,
                max_profile_extrapolation_distance=3.0,
            ),
        )

    @staticmethod
    def mapper():
        coordinator = object.__new__(Scheme2TrajectoryShadowCoordinator)
        coordinator._historical_sensitivity_map = (
            Scheme2HistoricalPriorOnlineOwnershipTest.mapping()
        )
        coordinator._historical_query = HistoricalSensitivityQuery(
            condition_snapshot_version="v1",
            mfac_context_id="MFAC-COND-C1",
            grid_id="P10-S1",
        )
        coordinator._last_historical_mapping = None
        return coordinator

    def test_so2_confidence_only_evidence_prevents_historical_reseed(self):
        existing = MFACRuntimeState(
            condition_snapshot_version="v1",
            mfac_context_id="MFAC-COND-C1",
            phi_live=-0.8,
            confidence_live=0.35,
            valid_event_count=0,
            phi_ph_live=0.03,
            confidence_ph_live=0.2,
            ph_valid_event_count=0,
            metadata={
                "online_confidence_so2": {
                    "effective_event_count": 1.0,
                    "direction_consistency": 0.0,
                }
            },
        )
        mapped = self.mapper()._mapped_state(
            "v1",
            "MFAC-COND-C1",
            existing=existing,
        )
        self.assertAlmostEqual(mapped.phi_live, -0.8)
        self.assertAlmostEqual(mapped.confidence_live, 0.35)
        # pH has no online evidence, so the reviewed scalar prior may still
        # initialize that independent channel.
        self.assertAlmostEqual(mapped.phi_ph_live, 0.02)
        self.assertAlmostEqual(mapped.confidence_ph_live, 0.7)

    def test_ph_confidence_only_evidence_prevents_historical_reseed(self):
        existing = MFACRuntimeState(
            condition_snapshot_version="v1",
            mfac_context_id="MFAC-COND-C1",
            phi_live=-0.6,
            confidence_live=0.2,
            valid_event_count=0,
            phi_ph_live=0.08,
            confidence_ph_live=0.3,
            ph_valid_event_count=0,
            metadata={
                "online_confidence_ph": {
                    "effective_event_count": 1.0,
                    "direction_consistency": 0.0,
                }
            },
        )
        mapped = self.mapper()._mapped_state(
            "v1",
            "MFAC-COND-C1",
            existing=existing,
        )
        # SO2 still has no online evidence and may use history.
        self.assertAlmostEqual(mapped.phi_live, -0.4)
        self.assertAlmostEqual(mapped.confidence_live, 0.8)
        self.assertAlmostEqual(mapped.phi_ph_live, 0.08)
        self.assertAlmostEqual(mapped.confidence_ph_live, 0.3)


if __name__ == "__main__":
    unittest.main()
