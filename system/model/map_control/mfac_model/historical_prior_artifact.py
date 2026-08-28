# -*- coding: utf-8 -*-
"""Artifact bridge from offline historical validation to runtime prior maps.

This module is intentionally *not* another trainer.  It consumes the canonical
full-sample training report and date-blocked validation report already produced
by ``offline_version_training`` and materializes one scalar-prior candidate map.

Candidate maps never have runtime authority.  ``approve_candidate_prior_map``
requires an explicit reviewer and mapping configuration, writes a separate
reviewed artifact, and binds that reviewed file into the existing MFAC version
manifest.  The version must then be (re)activated so the active pointer hashes
the updated manifest.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from system.model.config.mfac_paths import MFAC_OUTPUT_ROOT

from .historical_runtime_prior import is_reviewed_scalar_runtime_prior
from .historical_sensitivity_map import (
    HistoricalSensitivityMap,
    HistoricalSensitivityMapConfig,
    HistoricalSensitivitySurface,
)


HISTORICAL_PRIOR_ARTIFACT_VERSION = (
    "SCHEME2_HISTORICAL_PRIOR_ARTIFACT_V1_OFFLINE_TO_RUNTIME_BRIDGE"
)
CANDIDATE_ARTIFACT_TYPE = "MFAC_HISTORICAL_PRIOR_MAP_CANDIDATE"
REVIEWED_ARTIFACT_TYPE = "MFAC_HISTORICAL_PRIOR_MAP_REVIEWED"
CANDIDATE_FILENAME = "historical_prior_candidate_map.json"
REVIEWED_FILENAME = "historical_prior_reviewed_map.json"


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object: %s" % path)
    return dict(value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(dict(value), handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _candidate_key(value: Mapping[str, Any]) -> Tuple[str, str, str]:
    return (
        str(value.get("condition_snapshot_version") or ""),
        str(value.get("mfac_context_id") or ""),
        str(value.get("grid_id") or ""),
    )


def _selected_validation(selection: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    label = str(selection.get("selected_model_label") or "")
    for value in selection.get("validations") or []:
        if not isinstance(value, Mapping):
            continue
        if str(value.get("model_label") or "") == label:
            return dict(value)
    return None


def _surface_from_candidate(
    candidate: Mapping[str, Any],
    selection: Mapping[str, Any],
    *,
    tower_id: str,
    pooled: bool,
) -> HistoricalSensitivitySurface:
    if str(candidate.get("status") or "") != "MODEL_BASED_LOCAL_GAIN_REVIEW_CANDIDATE":
        raise ValueError("full-sample candidate is not review eligible")
    if str(selection.get("status") or "") != "BLOCKED_VALIDATED_MODEL_REVIEW_CANDIDATE":
        raise ValueError("blocked validation has not selected this candidate")
    if str(selection.get("selected_model_label") or "") != "GRID_SCALAR":
        raise ValueError("runtime prior candidate must remain scalar")

    phi_so2 = candidate.get("phi_so2_center")
    phi_ph = candidate.get("phi_ph_center")
    if phi_so2 is None or float(phi_so2) >= 0.0:
        raise ValueError("historical SO2 scalar prior must remain negative")
    if phi_ph is None or float(phi_ph) <= 0.0:
        raise ValueError("historical pH scalar prior must remain positive")
    if candidate.get("phi_so2_surface_coefficients"):
        raise ValueError("scalar prior cannot contain SO2 surface coefficients")
    if candidate.get("phi_ph_surface_coefficients"):
        raise ValueError("scalar prior cannot contain pH surface coefficients")

    validation = _selected_validation(selection)
    if validation is None:
        raise ValueError("selected blocked-validation result is missing")
    if str(validation.get("status") or "") != "BLOCKED_VALIDATION_REVIEW_CANDIDATE":
        raise ValueError("selected validation is not a review candidate")

    snapshot, context_id, grid_id = _candidate_key(candidate)
    profile_scope = "POOLED" if pooled else (grid_id or context_id or "CONTEXT")
    profile_id = "HIST-%s-%s-%s" % (
        snapshot,
        str(tower_id or "PRIMARY"),
        profile_scope,
    )
    return HistoricalSensitivitySurface(
        profile_id=profile_id,
        condition_snapshot_version=snapshot,
        mfac_context_id=("" if pooled else context_id),
        grid_id=("" if pooled else grid_id),
        phi_so2_prior=float(phi_so2),
        phi_ph_prior=float(phi_ph),
        confidence_so2=float(candidate.get("confidence_so2_candidate") or 0.0),
        confidence_ph=float(candidate.get("confidence_ph_candidate") or 0.0),
        event_count=int(candidate.get("event_count") or 0),
        independent_days=int(candidate.get("independent_days") or 0),
        feature_center={},
        feature_scale={},
        support_min={},
        support_max={},
        phi_so2_coefficients={},
        phi_ph_coefficients={},
        metadata={
            "artifact_semantics_version": HISTORICAL_PRIOR_ARTIFACT_VERSION,
            "tower_id": str(tower_id),
            "pooled": bool(pooled),
            "model_complexity": "SCALAR",
            "blocked_validation_passed": True,
            "blocked_validation_metrics": {
                "evaluated_fold_count": validation.get("evaluated_fold_count"),
                "evaluated_holdout_event_count": validation.get(
                    "evaluated_holdout_event_count"
                ),
                "so2_holdout_direction_rate": validation.get(
                    "so2_holdout_direction_rate"
                ),
                "ph_holdout_direction_rate": validation.get(
                    "ph_holdout_direction_rate"
                ),
                "median_so2_zero_effect_skill": validation.get(
                    "median_so2_zero_effect_skill"
                ),
                "median_ph_zero_effect_skill": validation.get(
                    "median_ph_zero_effect_skill"
                ),
            },
            "runtime_prior_reviewed": False,
            "runtime_prior_allowed": False,
            "candidate_only": True,
        },
    )


def build_candidate_prior_map(
    *,
    training_report_path: str | Path,
    validation_report_path: str | Path,
    output_path: str | Path,
    tower_id: str,
) -> Dict[str, Any]:
    """Materialize selected scalar candidates without granting runtime authority."""
    training_path = Path(training_report_path).resolve()
    validation_path = Path(validation_report_path).resolve()
    training = _read_json(training_path)
    validation = _read_json(validation_path)

    training_candidates = {}
    for value in list(training.get("grid_candidates") or []) + list(
        training.get("pooled_candidates") or []
    ):
        if isinstance(value, Mapping):
            training_candidates[_candidate_key(value)] = dict(value)

    profiles = []
    for selection in validation.get("grid_selections") or []:
        if not isinstance(selection, Mapping):
            continue
        if str(selection.get("status") or "") != "BLOCKED_VALIDATED_MODEL_REVIEW_CANDIDATE":
            continue
        key = _candidate_key(selection)
        candidate = training_candidates.get(key)
        if candidate is None:
            raise ValueError("selected grid validation has no full-sample candidate: %s" % (key,))
        profiles.append(
            _surface_from_candidate(
                candidate,
                selection,
                tower_id=tower_id,
                pooled=False,
            )
        )

    pooled_profile = None
    for selection in validation.get("pooled_selections") or []:
        if not isinstance(selection, Mapping):
            continue
        if str(selection.get("status") or "") != "BLOCKED_VALIDATED_MODEL_REVIEW_CANDIDATE":
            continue
        key = _candidate_key(selection)
        candidate = training_candidates.get(key)
        if candidate is None:
            raise ValueError("selected pooled validation has no full-sample candidate: %s" % (key,))
        pooled_profile = _surface_from_candidate(
            candidate,
            selection,
            tower_id=tower_id,
            pooled=True,
        )
        break

    snapshots = {
        item.condition_snapshot_version for item in profiles
    }
    if pooled_profile is not None:
        snapshots.add(pooled_profile.condition_snapshot_version)
    if not snapshots:
        # No blocked-validated model is a valid artifact state.  Keep the
        # candidate file auditable rather than inventing a prior.
        snapshot_versions = tuple(str(x) for x in validation.get("snapshot_versions") or [])
        snapshot = snapshot_versions[0] if len(snapshot_versions) == 1 else ""
    elif len(snapshots) == 1:
        snapshot = next(iter(snapshots))
    else:
        raise ValueError("candidate prior map spans multiple snapshots")
    if not snapshot:
        raise ValueError("candidate prior map requires one condition snapshot")

    # Candidate mapping parameters are deliberately non-authoritative.  Review
    # must supply the final HistoricalSensitivityMapConfig explicitly.
    candidate_config = HistoricalSensitivityMapConfig(
        max_neighbor_grid_distance=1,
        neighbor_confidence_penalty=1.0,
        pooled_confidence_penalty=1.0,
        max_profile_extrapolation_distance=0.0,
    )
    mapping = HistoricalSensitivityMap(
        snapshot,
        profiles,
        candidate_config,
        pooled_profile=pooled_profile,
    )
    payload = mapping.to_dict()
    payload.update(
        {
            "artifact_type": CANDIDATE_ARTIFACT_TYPE,
            "artifact_semantics_version": HISTORICAL_PRIOR_ARTIFACT_VERSION,
            "tower_id": str(tower_id),
            "review_status": "REVIEW_REQUIRED",
            "runtime_prior_reviewed": False,
            "runtime_prior_allowed": False,
            "candidate_mapping_config_has_runtime_authority": False,
            "source_training_report_path": str(training_path),
            "source_training_report_sha256": _sha256_file(training_path),
            "source_validation_report_path": str(validation_path),
            "source_validation_report_sha256": _sha256_file(validation_path),
            "profile_count": len(profiles),
            "pooled_profile_available": pooled_profile is not None,
        }
    )
    target = Path(output_path).resolve()
    _atomic_write_json(target, payload)
    payload["artifact_path"] = str(target)
    payload["artifact_sha256"] = _sha256_file(target)
    return payload


def approve_candidate_prior_map(
    *,
    candidate_path: str | Path,
    reviewed_path: str | Path,
    manifest_path: str | Path,
    reviewer_id: str,
    review_id: str,
    review_time: str,
    map_config: HistoricalSensitivityMapConfig,
) -> Dict[str, Any]:
    """Explicitly publish a blocked-validated scalar candidate for runtime seeding.

    This does not activate the integrated version and does not enable online
    learning or residual control.  It only allows the reviewed scalar map to be
    used as a cold-start prior when the runtime has no stronger online state.
    """
    candidate_file = Path(candidate_path).resolve()
    reviewed_file = Path(reviewed_path).resolve()
    manifest_file = Path(manifest_path).resolve()
    candidate_payload = _read_json(candidate_file)
    if candidate_payload.get("artifact_type") != CANDIDATE_ARTIFACT_TYPE:
        raise ValueError("historical prior candidate artifact_type mismatch")
    reviewer = str(reviewer_id or "").strip()
    review = str(review_id or "").strip()
    reviewed_at = str(review_time or "").strip()
    if not reviewer or not review or not reviewed_at:
        raise ValueError("reviewer_id, review_id and review_time are required")
    if not isinstance(map_config, HistoricalSensitivityMapConfig):
        raise TypeError("map_config must be HistoricalSensitivityMapConfig")

    candidate_map = HistoricalSensitivityMap.from_dict(candidate_payload)
    source_profiles = list(candidate_map.profiles)
    if candidate_map.pooled_profile is not None:
        source_profiles.append(candidate_map.pooled_profile)
    if not source_profiles:
        raise ValueError("candidate map contains no blocked-validated prior")

    def reviewed_surface(surface: HistoricalSensitivitySurface) -> HistoricalSensitivitySurface:
        metadata = dict(surface.metadata or {})
        if not bool(metadata.get("blocked_validation_passed", False)):
            raise ValueError("candidate surface lacks blocked-validation approval")
        if str(metadata.get("model_complexity") or "").upper() != "SCALAR":
            raise ValueError("only scalar historical priors may be reviewed")
        metadata.update(
            {
                "runtime_prior_reviewed": True,
                "runtime_prior_allowed": True,
                "candidate_only": False,
                "runtime_prior_review_id": review,
                "runtime_prior_reviewer_id": reviewer,
                "runtime_prior_review_time": reviewed_at,
            }
        )
        return replace(surface, metadata=metadata)

    profiles = tuple(reviewed_surface(item) for item in candidate_map.profiles)
    pooled = (
        reviewed_surface(candidate_map.pooled_profile)
        if candidate_map.pooled_profile is not None
        else None
    )
    reviewed_map = HistoricalSensitivityMap(
        candidate_map.condition_snapshot_version,
        profiles,
        map_config,
        pooled_profile=pooled,
    )
    for surface in list(reviewed_map.profiles) + (
        [reviewed_map.pooled_profile] if reviewed_map.pooled_profile is not None else []
    ):
        if surface is not None and not is_reviewed_scalar_runtime_prior(surface):
            raise ValueError("reviewed prior failed runtime scalar gate")

    reviewed_payload = reviewed_map.to_dict()
    reviewed_payload.update(
        {
            "artifact_type": REVIEWED_ARTIFACT_TYPE,
            "artifact_semantics_version": HISTORICAL_PRIOR_ARTIFACT_VERSION,
            "tower_id": str(candidate_payload.get("tower_id") or ""),
            "review_status": "REVIEWED_RUNTIME_PRIOR",
            "runtime_prior_reviewed": True,
            "runtime_prior_allowed": True,
            "review_id": review,
            "reviewer_id": reviewer,
            "review_time": reviewed_at,
            "candidate_artifact_path": str(candidate_file),
            "candidate_artifact_sha256": _sha256_file(candidate_file),
        }
    )
    _atomic_write_json(reviewed_file, reviewed_payload)
    reviewed_sha = _sha256_file(reviewed_file)

    manifest = _read_json(manifest_file)
    manifest_snapshot = str(manifest.get("condition_snapshot_version") or "")
    if manifest_snapshot != reviewed_map.condition_snapshot_version:
        raise ValueError("reviewed prior map snapshot does not match MFAC manifest")
    expected_candidate = str(manifest.get("historical_prior_candidate_map_path") or "")
    expected_candidate_sha = str(
        manifest.get("historical_prior_candidate_map_sha256") or ""
    )
    if expected_candidate and Path(expected_candidate).resolve() != candidate_file:
        raise ValueError("manifest references a different historical prior candidate")
    if expected_candidate_sha and expected_candidate_sha != _sha256_file(candidate_file):
        raise ValueError("historical prior candidate hash changed before review")

    manifest.update(
        {
            "historical_prior_reviewed_map_path": str(reviewed_file),
            "historical_prior_reviewed_map_sha256": reviewed_sha,
            "historical_prior_map_reviewed": True,
            "historical_prior_map_allowed": True,
            "historical_prior_review_id": review,
            "historical_prior_reviewer_id": reviewer,
            "historical_prior_review_time": reviewed_at,
        }
    )
    _atomic_write_json(manifest_file, manifest)
    return {
        "status": "REVIEWED_PRIOR_BOUND_TO_VERSION_MANIFEST",
        "reviewed_map_path": str(reviewed_file),
        "reviewed_map_sha256": reviewed_sha,
        "manifest_path": str(manifest_file),
        "manifest_sha256": _sha256_file(manifest_file),
        "activation_required": True,
        "learning_permission": False,
        "residual_control_permission": False,
    }


def load_reviewed_prior_map_for_snapshot(
    condition_snapshot_version: str,
) -> Optional[HistoricalSensitivityMap]:
    """Load the reviewed prior bound by the canonical MFAC version manifest."""
    snapshot = str(condition_snapshot_version or "").strip()
    if not snapshot:
        return None
    snapshot_dir = Path(MFAC_OUTPUT_ROOT).resolve() / "snapshots" / snapshot
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = _read_json(manifest_path)
    if str(manifest.get("condition_snapshot_version") or "") != snapshot:
        return None
    if not bool(manifest.get("historical_prior_map_reviewed", False)):
        return None
    if not bool(manifest.get("historical_prior_map_allowed", False)):
        return None
    map_path_text = str(manifest.get("historical_prior_reviewed_map_path") or "").strip()
    expected_sha = str(
        manifest.get("historical_prior_reviewed_map_sha256") or ""
    ).strip()
    if not map_path_text or not expected_sha:
        return None
    map_path = Path(map_path_text).resolve()
    if not map_path.is_file() or _sha256_file(map_path) != expected_sha:
        return None
    payload = _read_json(map_path)
    if payload.get("artifact_type") != REVIEWED_ARTIFACT_TYPE:
        return None
    if not bool(payload.get("runtime_prior_reviewed", False)):
        return None
    if not bool(payload.get("runtime_prior_allowed", False)):
        return None
    mapping = HistoricalSensitivityMap.from_dict(payload)
    if mapping.condition_snapshot_version != snapshot:
        return None
    eligible = [
        item for item in mapping.profiles
        if is_reviewed_scalar_runtime_prior(item)
    ]
    if mapping.pooled_profile is not None and is_reviewed_scalar_runtime_prior(
        mapping.pooled_profile
    ):
        eligible.append(mapping.pooled_profile)
    return mapping if eligible else None


__all__ = [
    "HISTORICAL_PRIOR_ARTIFACT_VERSION",
    "CANDIDATE_ARTIFACT_TYPE",
    "REVIEWED_ARTIFACT_TYPE",
    "CANDIDATE_FILENAME",
    "REVIEWED_FILENAME",
    "build_candidate_prior_map",
    "approve_candidate_prior_map",
    "load_reviewed_prior_map_for_snapshot",
]