# -*- coding: utf-8 -*-
"""Single source of truth for Scheme-2 offline-training cadence.

This module intentionally owns only lifecycle timing semantics shared by
Process4MapControl and the MFAC second module.  Data-source details, record
cadence and completeness ratios remain Process4 concerns; MFAC response updates
remain event-driven and are not scheduled by these day counts.
"""

from __future__ import annotations


INITIAL_OFFLINE_TRAINING_DAYS = 7
INCREMENTAL_OFFLINE_TRAINING_DAYS = 3
OFFLINE_TRAINING_ORDER = ("CONDITION", "MFAC")
ONLINE_MFAC_UPDATE_TRIGGER = "VALID_COMPLETED_CAUSAL_RESPONSE_EVENT"


def training_days_for_mode(mode: str) -> int:
    """Return the required offline accumulation window for one train mode."""
    normalized = str(mode or "").strip().upper()
    if normalized == "INITIAL":
        return INITIAL_OFFLINE_TRAINING_DAYS
    if normalized == "INCREMENTAL":
        return INCREMENTAL_OFFLINE_TRAINING_DAYS
    raise ValueError("mode must be INITIAL or INCREMENTAL")


__all__ = [
    "INITIAL_OFFLINE_TRAINING_DAYS",
    "INCREMENTAL_OFFLINE_TRAINING_DAYS",
    "OFFLINE_TRAINING_ORDER",
    "ONLINE_MFAC_UPDATE_TRIGGER",
    "training_days_for_mode",
]
