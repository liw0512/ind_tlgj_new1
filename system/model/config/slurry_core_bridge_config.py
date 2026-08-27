"""Deprecated compatibility view of the canonical MFAC bridge config.

New code must import ``MFAC_CORE_BRIDGE_CONFIG`` from
``system.model.config.mfac_core_bridge_config``.  This module exists only
because methods inherited from the historical Process4MapControl shell still
request several ``slurry_policy_*`` key names.
"""
from copy import deepcopy

from system.model.config.mfac_core_bridge_config import MFAC_CORE_BRIDGE_CONFIG


SLURRY_CORE_BRIDGE_CONFIG = deepcopy(MFAC_CORE_BRIDGE_CONFIG)
SLURRY_CORE_BRIDGE_CONFIG.update(
    {
        "slurry_policy_initial_script": MFAC_CORE_BRIDGE_CONFIG["mfac_initial_script"],
        "slurry_policy_incremental_script": MFAC_CORE_BRIDGE_CONFIG["mfac_incremental_script"],
        "slurry_policy_activate_script": MFAC_CORE_BRIDGE_CONFIG["mfac_activate_script"],
        "slurry_policy_config": MFAC_CORE_BRIDGE_CONFIG["mfac_config"],
        "slurry_policy_output_root": MFAC_CORE_BRIDGE_CONFIG["mfac_output_root"],
    }
)


__all__ = ["MFAC_CORE_BRIDGE_CONFIG", "SLURRY_CORE_BRIDGE_CONFIG"]
