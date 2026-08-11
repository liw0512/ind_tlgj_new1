from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict

try:
    from _engine.utils import read_json, strict_json_value, write_json
except ImportError:  # pragma: no cover
    from .._engine.utils import read_json, strict_json_value, write_json


class RuntimeStore:
    def __init__(self, plant_config: dict, online_config: dict) -> None:
        root = Path(plant_config["paths"]["online_runtime_dir"])
        root.mkdir(parents=True, exist_ok=True)
        logging_cfg = online_config["logging"]
        self.enabled = bool(logging_cfg.get("enabled", True))
        self.state_path = root / logging_cfg["runtime_state_filename"]
        self.decision_log = root / logging_cfg["decision_log_filename"]
        self.execution_log = root / logging_cfg["execution_log_filename"]
        self.lock = threading.RLock()
        self.state: Dict[str, Any] = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {"schema_version": "1.0"}
        try:
            value = read_json(self.state_path)
            return value if isinstance(value, dict) else {"schema_version": "1.0"}
        except Exception:
            # 损坏状态不覆盖；保留旁路备份后从安全空状态启动。
            broken = self.state_path.with_suffix(self.state_path.suffix + ".broken")
            try:
                os.replace(self.state_path, broken)
            except OSError:
                pass
            return {"schema_version": "1.0", "state_recovered_from_error": True}

    def save(self) -> None:
        if not self.enabled:
            return
        with self.lock:
            write_json(self.state_path, self.state)

    def append_decision(self, value: Dict[str, Any]) -> None:
        self._append(self.decision_log, value)

    def append_execution(self, value: Dict[str, Any]) -> None:
        self._append(self.execution_log, value)

    def _append(self, path: Path, value: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        with self.lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(strict_json_value(value), ensure_ascii=False, allow_nan=False))
                handle.write("\n")
