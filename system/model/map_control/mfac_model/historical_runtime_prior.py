# -*- coding: utf-8 -*-
"""Runtime gate for reviewed scalar historical MFAC priors.

The research/history layer may contain continuous sensitivity surfaces, rejected
candidates and audit-only fits.  The first formal runtime integration is
intentionally narrower: only explicitly reviewed scalar priors may seed a fresh
MFACRuntimeState.

This module filters an existing ``HistoricalSensitivityMap`` without changing
its research API.  Mapping order remains exact-context -> exact-grid -> neighbor
-> pooled, but unreviewed or non-scalar profiles are invisible to runtime.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Tuple

from .historical_sensitivity_map import (
    HistoricalSensitivityDecision,
    HistoricalSensitivityMap,
    HistoricalSensitivityQuery,
    HistoricalSensitivitySurface,
)


HISTORICAL_RUNTIME_PRIOR_VERSION = (
    "SCHEME2_HISTORICAL_RUNTIME_PRIOR_V1_REVIEWED_SCALAR_ONLY"
)


def _flag(metadata: Dict[str, Any], name: str) -> bool:
    value = metadata.get(name)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def is_reviewed_scalar_runtime_prior(surface: HistoricalSensitivitySurface) -> bool:
    metadata = dict(surface.metadata or {})
    if not _flag(metadata, "runtime_prior_reviewed"):
        return False
    if not _flag(metadata, "runtime_prior_allowed"):
        return False
    if str(metadata.get("model_complexity") or "").strip().upper() != "SCALAR":
        return False
    if surface.phi_so2_coefficients or surface.phi_ph_coefficients:
        return False
    return True


def resolve_reviewed_scalar_runtime_prior(
    mapping: HistoricalSensitivityMap,
    query: HistoricalSensitivityQuery,
) -> HistoricalSensitivityDecision:
    eligible = tuple(
        profile for profile in mapping.profiles
        if is_reviewed_scalar_runtime_prior(profile)
    )
    pooled = (
        mapping.pooled_profile
        if mapping.pooled_profile is not None
        and is_reviewed_scalar_runtime_prior(mapping.pooled_profile)
        else None
    )
    rejected_ids = tuple(
        profile.profile_id for profile in mapping.profiles
        if not is_reviewed_scalar_runtime_prior(profile)
    )
    if mapping.pooled_profile is not None and pooled is None:
        rejected_ids = rejected_ids + (mapping.pooled_profile.profile_id,)

    filtered = HistoricalSensitivityMap(
        mapping.condition_snapshot_version,
        eligible,
        mapping.config,
        pooled_profile=pooled,
    )
    decision = filtered.resolve(query)
    metadata = dict(decision.metadata or {})
    metadata["runtime_prior_filter"] = {
        "semantics_version": HISTORICAL_RUNTIME_PRIOR_VERSION,
        "reviewed_scalar_only": True,
        "eligible_profile_ids": [item.profile_id for item in eligible],
        "pooled_profile_id": pooled.profile_id if pooled is not None else "",
        "rejected_profile_ids": list(rejected_ids),
        "complex_surface_runtime_authority": False,
        "unreviewed_profile_runtime_authority": False,
    }
    return replace(decision, metadata=metadata)


__all__ = [
    "HISTORICAL_RUNTIME_PRIOR_VERSION",
    "is_reviewed_scalar_runtime_prior",
    "resolve_reviewed_scalar_runtime_prior",
]
