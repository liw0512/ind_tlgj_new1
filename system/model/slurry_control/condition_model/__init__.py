"""Route legacy condition-model imports to the real map_control package."""
from pathlib import Path

_REAL_PACKAGE = (
    Path(__file__).resolve().parents[2]
    / "map_control"
    / "condition_model"
)
__path__ = [str(_REAL_PACKAGE)]
