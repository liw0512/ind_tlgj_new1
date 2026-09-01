"""Non-predictive adaptive slurry-flow control foundation.

The package intentionally contains no DMC/MPC/future-trajectory predictor.
Its control contract is based on a physical Qbase, delayed actual-response
learning, bounded SO2 feedback, pH reserve guarding, and explicit pending-state
management. Module-2 lifecycle is developed in order: Initial -> Incremental -> Online.
"""

from .config import InitialTrainingConfig
from .qbase import (
    BaselineSlurryResult,
    calculate_baseline_slurry_flow,
    cas_from_ph_table,
    solids_fraction_from_density,
)
from .qbase_calibration import KBaseCalibrationResult, calibrate_kbase

__all__ = [
    "InitialTrainingConfig",
    "KBaseCalibrationResult",
    "calibrate_kbase",
    "BaselineSlurryResult",
    "calculate_baseline_slurry_flow",
    "cas_from_ph_table",
    "solids_fraction_from_density",
]
