"""独立 FAST_CHANGE 风险识别模块。

FAST_CHANGE 的离线版本快照和在线短期运行状态统一放在
``slurry_policy_model/slurry_policy_model_output`` 下，但与第二模块正式策略
``snapshots/v###`` 分目录保存，避免同版本目录互相冲突。
"""

from pathlib import Path
from typing import Any, Dict, Optional

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
    FastChangeHistoryManager as _BaseFastChangeHistoryManager,
)


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SLURRY_POLICY_OUTPUT_ROOT = (
    PROJECT_ROOT
    / "system"
    / "model"
    / "map_control"
    / "slurry_policy_model"
    / "slurry_policy_model_output"
)
FAST_CHANGE_OUTPUT_ROOT = SLURRY_POLICY_OUTPUT_ROOT / "fast_change"
FAST_CHANGE_RUNTIME_ROOT = SLURRY_POLICY_OUTPUT_ROOT / "fast_change_runtime"


class FastChangeHistoryManager(_BaseFastChangeHistoryManager):
    """项目统一 FAST_CHANGE 历史管理器。

    默认目录：
    - 离线版本：``slurry_policy_model_output/fast_change/snapshots/v###``；
    - 在线状态：``slurry_policy_model_output/fast_change_runtime``。

    显式传入 ``output_root`` / ``runtime_root`` 时仍尊重调用方路径，便于独立测试。
    """

    def __init__(
        self,
        *,
        config: Optional[Dict[str, Any]] = None,
        plant_config: Optional[Dict[str, Any]] = None,
        output_root: Optional[str | Path] = None,
        runtime_root: Optional[str | Path] = None,
        persist_runtime: bool = False,
    ) -> None:
        super().__init__(
            config=config,
            plant_config=plant_config,
            output_root=output_root or FAST_CHANGE_OUTPUT_ROOT,
            runtime_root=runtime_root or FAST_CHANGE_RUNTIME_ROOT,
            persist_runtime=persist_runtime,
        )


__all__ = [
    "FAST_CHANGE_CONFIG",
    "FastChangeModeDetector",
    "FastChangeConfigurationError",
    "REGULAR",
    "FAST_CHANGE",
    "FAST_RECOVERY",
    "FAST_CONTEXT_COLUMNS",
    "FAST_CHANGE_OUTPUT_ROOT",
    "FAST_CHANGE_RUNTIME_ROOT",
    "FastChangeHistoryManager",
]
