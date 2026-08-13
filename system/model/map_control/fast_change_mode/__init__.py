"""独立 FAST_CHANGE 风险识别模块。"""

from .fast_change_config import FAST_CHANGE_CONFIG
from .fast_change_mode_detector import (
    FAST_CHANGE,
    FAST_RECOVERY,
    REGULAR,
    FastChangeConfigurationError,
    FastChangeModeDetector,
)
from .fast_change_history_manager import (
    FAST_CONTEXT_COLUMNS,
    FastChangeHistoryManager,
)

__all__ = [
    "FAST_CHANGE_CONFIG",
    "FastChangeModeDetector",
    "FastChangeConfigurationError",
    "REGULAR",
    "FAST_CHANGE",
    "FAST_RECOVERY",
    "FAST_CONTEXT_COLUMNS",
    "FastChangeHistoryManager",
]
