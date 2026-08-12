"""Process4MapControl integration view of the slurry-policy config.

The base ``slurry_policy_config.py`` already derives project paths from the
current repository root and derives all plant facts from the single central
``system/model/config/plant_config.py``.  Therefore P4PC no longer maintains a
second set of path or plant overrides here; this module is only a compatibility
entry point for the existing bridge configuration.
"""
from __future__ import annotations

import copy

from system.model.map_control.slurry_policy_model import slurry_policy_config as _base


PLANT_CONFIG = copy.deepcopy(_base.PLANT_CONFIG)
TRAINING_CONFIG = copy.deepcopy(_base.TRAINING_CONFIG)
ONLINE_POLICY_CONFIG = copy.deepcopy(_base.ONLINE_POLICY_CONFIG)
