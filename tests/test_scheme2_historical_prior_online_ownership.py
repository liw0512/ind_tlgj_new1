import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from system.model.map_control.mfac_model.historical_prior_artifact import (
    CANDIDATE_FILENAME,
    REVIEWED_FILENAME,
    approve_candidate_prior_map,
    build_candidate_prior_map,
    load_reviewed_prior_map_for_snapshot,
)
from system.model.map_control.mfac_model.historical_runtime_prior import (
    resolve_reviewed_scalar_runtime_prior,
)
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

    @staticmethod
    def _candidate_reports(root: Path):
        training = {
            "grid_candidates": [
                {
                    "status": "MODEL_BASED_LOCAL_GAIN_REVIEW_CANDIDATE",
                    "condition_snapshot_version": "v1",
                    "mfac_context_id": "MFAC-COND-C1",
                    "grid_id": "P10-S1",
                    "event_count": 30,
                    "independent_days": 5,
                    "phi_so2_center": -0.4,
                    "phi_ph_center": 0.02,
                    "phi_so2_surface_coefficients": {},
                    "phi_ph_surface_coefficients": {},
                    "confidence_so2_candidate": 0.8,
                    "confidence_ph_candidate": 0.7,
                }
            ],
            "pooled_candidates": [],
        }
        validation = {
            "snapshot_versions": ["v1"],
            "grid_selections": [
                {
                    "condition_snapshot_version": "v1",
                    "mfac_context_id": "MFAC-COND-C1",
                    "grid_id": "P10-S1",
                    "selected_model_label": "GRID_SCALAR",
                    "status": "BLOCKED_VALIDATED_MODEL_REVIEW_CANDIDATE",
                    "validations": [
                        {
                            "model_label": "GRID_SCALAR",
                            "status": "BLOCKED_VALIDATION_REVIEW_CANDIDATE",
                            "evaluated_fold_count": 5,
                            "evaluated_holdout_event_count": 30,
                            "so2_holdout_direction_rate": 1.0,
                            "ph_holdout_direction_rate": 0.9,
                            "median_so2_zero_effect_skill": 0.2,
                            "median_ph_zero_effect_skill": 0.3,
                        }
                    ],
                }
            ],
            "pooled_selections": [],
        }
        training_path = root / "training.json"
        validation_path = root / "validation.json"
        training_path.write_text(json.dumps(training), encoding="utf-8")
        validation_path.write_text(json.dumps(validation), encoding="utf-8")
        return training_path, validation_path

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

    def test_offline_candidate_requires_review_before_runtime_loading(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot_dir = root / "snapshots" / "v1"
            snapshot_dir.mkdir(parents=True)
            training_path, validation_path = self._candidate_reports(root)
            candidate_path = snapshot_dir / CANDIDATE_FILENAME
            candidate = build_candidate_prior_map(
                training_report_path=training_path,
                validation_report_path=validation_path,
                output_path=candidate_path,
                tower_id="xst",
            )
            candidate_map = HistoricalSensitivityMap.from_dict(
                json.loads(candidate_path.read_text(encoding="utf-8"))
            )
            query = HistoricalSensitivityQuery(
                condition_snapshot_version="v1",
                mfac_context_id="MFAC-COND-C1",
                grid_id="P10-S1",
            )
            decision = resolve_reviewed_scalar_runtime_prior(candidate_map, query)
            self.assertFalse(decision.available)
            self.assertFalse(candidate["runtime_prior_reviewed"])
            self.assertFalse(candidate["runtime_prior_allowed"])

            manifest_path = snapshot_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "condition_snapshot_version": "v1",
                        "historical_prior_candidate_map_path": str(candidate_path),
                        "historical_prior_candidate_map_sha256": candidate[
                            "artifact_sha256"
                        ],
                        "historical_prior_reviewed_map_path": "",
                        "historical_prior_reviewed_map_sha256": "",
                        "historical_prior_map_reviewed": False,
                        "historical_prior_map_allowed": False,
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "system.model.map_control.mfac_model.historical_prior_artifact.MFAC_OUTPUT_ROOT",
                root,
            ):
                self.assertIsNone(load_reviewed_prior_map_for_snapshot("v1"))

    def test_reviewed_candidate_is_bound_to_manifest_and_loadable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot_dir = root / "snapshots" / "v1"
            snapshot_dir.mkdir(parents=True)
            training_path, validation_path = self._candidate_reports(root)
            candidate_path = snapshot_dir / CANDIDATE_FILENAME
            candidate = build_candidate_prior_map(
                training_report_path=training_path,
                validation_report_path=validation_path,
                output_path=candidate_path,
                tower_id="xst",
            )
            manifest_path = snapshot_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "condition_snapshot_version": "v1",
                        "historical_prior_candidate_map_path": str(candidate_path),
                        "historical_prior_candidate_map_sha256": candidate[
                            "artifact_sha256"
                        ],
                        "historical_prior_reviewed_map_path": "",
                        "historical_prior_reviewed_map_sha256": "",
                        "historical_prior_map_reviewed": False,
                        "historical_prior_map_allowed": False,
                    }
                ),
                encoding="utf-8",
            )
            reviewed_path = snapshot_dir / REVIEWED_FILENAME
            approval = approve_candidate_prior_map(
                candidate_path=candidate_path,
                reviewed_path=reviewed_path,
                manifest_path=manifest_path,
                reviewer_id="TEST-REVIEWER",
                review_id="TEST-PRIOR-V1",
                review_time="2026-08-28T16:00:00+08:00",
                map_config=HistoricalSensitivityMapConfig(
                    max_neighbor_grid_distance=1,
                    neighbor_confidence_penalty=0.8,
                    pooled_confidence_penalty=0.5,
                    max_profile_extrapolation_distance=0.0,
                ),
            )
            self.assertEqual(
                approval["status"],
                "REVIEWED_PRIOR_BOUND_TO_VERSION_MANIFEST",
            )
            self.assertTrue(approval["activation_required"])

            with patch(
                "system.model.map_control.mfac_model.historical_prior_artifact.MFAC_OUTPUT_ROOT",
                root,
            ):
                mapping = load_reviewed_prior_map_for_snapshot("v1")
            self.assertIsNotNone(mapping)
            decision = resolve_reviewed_scalar_runtime_prior(
                mapping,
                HistoricalSensitivityQuery(
                    condition_snapshot_version="v1",
                    mfac_context_id="MFAC-COND-C1",
                    grid_id="P10-S1",
                ),
            )
            self.assertTrue(decision.available)
            self.assertAlmostEqual(decision.phi_so2, -0.4)
            self.assertAlmostEqual(decision.phi_ph, 0.02)


if __name__ == "__main__":
    unittest.main()