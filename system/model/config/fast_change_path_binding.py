# -*- coding: utf-8 -*-
"""Scheme-2 integration binding for locked Scheme1 FAST artifact paths.

The FAST implementation is blob-locked to the reviewed Scheme1 baseline, so
Scheme2 must not edit ``fast_change_history_manager.py`` merely to relocate
artifacts.  Instead, the public Scheme2 runtime binds that module's default
output/runtime roots to the canonical module-local topology before any formal
``FastChangeHistoryManager`` instance is created.

This module owns path binding only. It does not change FAST algorithm settings,
detector semantics, or runtime permissions.
"""
from pathlib import Path
from typing import Dict

from system.model.config.mfac_paths import FAST_OUTPUT_ROOT, FAST_RUNTIME_ROOT
from system.model.map_control.fast_change_mode import fast_change_history_manager


FAST_PATH_BINDING_VERSION = "SCHEME2_FAST_PATH_BINDING_V1"


def bind_fast_change_artifact_paths() -> Dict[str, str]:
    """Bind locked FAST defaults to Scheme2's canonical module-local paths."""
    fast_change_history_manager.DEFAULT_OUTPUT_ROOT = Path(FAST_OUTPUT_ROOT)
    fast_change_history_manager.DEFAULT_RUNTIME_ROOT = Path(FAST_RUNTIME_ROOT)
    return {
        "version": FAST_PATH_BINDING_VERSION,
        "output_root": str(FAST_OUTPUT_ROOT),
        "runtime_root": str(FAST_RUNTIME_ROOT),
    }


__all__ = [
    "FAST_PATH_BINDING_VERSION",
    "bind_fast_change_artifact_paths",
]
