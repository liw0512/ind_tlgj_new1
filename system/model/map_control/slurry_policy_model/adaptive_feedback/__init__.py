"""Non-predictive adaptive slurry-flow control foundation.

The package intentionally contains no DMC/MPC/future-trajectory predictor.
Its control contract is based on a physical Qbase, measured disturbance
feedforward, delayed actual-response learning, and bounded SO2/pH feedback.
"""

from .qbase import (
    BaselineSlurryResult,
    calculate_baseline_slurry_flow,
    cas_from_ph_table,
    solids_fraction_from_density,
)

__all__ = [
    "BaselineSlurryResult",
    "calculate_baseline_slurry_flow",
    "cas_from_ph_table",
    "solids_fraction_from_density",
]
