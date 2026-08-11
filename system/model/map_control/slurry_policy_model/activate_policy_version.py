from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from _engine.utils import write_json
from slurry_policy_online.config_loader import load_online_config
from slurry_policy_online.policy_snapshot_loader import PolicySnapshotLoader


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def activate_version(
    version: str,
    config_spec: Optional[str] = None,
) -> Path:
    """验证并原子发布第一、第二模块同版本对。

    第一模块增量训练完成后不会调用本函数。只有第二模块同版本训练也完成，
    且 condition snapshot、映射哈希、manifest、PKL 和厂级配置全部通过验证后，
    才更新正式 ``active_version.json``。
    """

    plant, _training, online = load_online_config(config_spec)
    version = str(version).strip()
    if not version.startswith("v") or not version[1:].isdigit():
        raise ValueError("版本必须是 v### 格式: %r" % version)

    output_root = Path(plant["paths"]["output_root"])
    policy_snapshot = output_root / "snapshots" / version
    condition_snapshot = (
        Path(plant["paths"]["condition_snapshots_dir"])
        / version
        / "condition_snapshot.json"
    )

    # 第一次准备：从实际快照中读取可信哈希和版本关系。
    base_pointer = {
        "integrated_version": version,
        "policy_version": version,
        "condition_snapshot_version": version,
        "policy_snapshot_path": str(policy_snapshot),
        "condition_snapshot_path": str(condition_snapshot),
        "source_condition_version": version,
    }
    loader = PolicySnapshotLoader(plant, online)
    prepared = loader.prepare_pointer(base_pointer)

    pointer = {
        "schema_version": "2.0",
        "integrated_version": version,
        "condition": {
            "version": version,
            "snapshot_path": str(condition_snapshot),
            "snapshot_sha256": prepared["condition_snapshot_sha256"],
            "grid_condition_mapping_sha256": prepared[
                "grid_condition_mapping_sha256"
            ],
        },
        "slurry_policy": {
            "version": version,
            "snapshot_path": str(policy_snapshot),
            "manifest_sha256": prepared["manifest_sha256"],
            "source_condition_version": version,
            "source_condition_snapshot_sha256": prepared[
                "condition_snapshot_sha256"
            ],
            "grid_condition_mapping_sha256": prepared[
                "grid_condition_mapping_sha256"
            ],
        },
        "activated_at": _utc_now_iso(),
        # 扁平兼容字段，便于旧工具读取；正式校验以嵌套字段为准。
        "policy_version": version,
        "condition_snapshot_version": version,
        "policy_snapshot_path": str(policy_snapshot),
        "condition_snapshot_path": str(condition_snapshot),
        "condition_snapshot_sha256": prepared[
            "condition_snapshot_sha256"
        ],
        "grid_condition_mapping_sha256": prepared[
            "grid_condition_mapping_sha256"
        ],
        "policy_manifest_sha256": prepared["manifest_sha256"],
        "source_condition_version": version,
    }

    # 第二次准备：使用最终将要发布的完整指针再次校验，防止字段组装错误。
    loader.prepare_pointer(pointer)

    active_path = Path(plant["paths"]["active_policy_version_file"])
    active_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path = active_path.with_suffix(active_path.suffix + ".candidate")
    write_json(candidate_path, pointer)

    # 从候选文件重新读取并验证。只有成功后才原子替换正式指针。
    verify_plant = dict(plant)
    verify_plant["paths"] = dict(plant["paths"])
    verify_plant["paths"]["active_policy_version_file"] = str(
        candidate_path
    )
    verify_loader = PolicySnapshotLoader(verify_plant, online)
    verify_loader.load_active(force=True)
    os.replace(candidate_path, active_path)
    return active_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="验证并原子激活第一、第二模块同版本在线模型"
    )
    parser.add_argument(
        "--version",
        required=True,
        help="第一、第二模块一致的版本，例如 v006",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="可选统一配置文件路径或模块名",
    )
    args = parser.parse_args()

    active_path = activate_version(args.version, args.config)
    print("第一、第二模块同版本对已激活: %s" % args.version)
    print("active_version.json: %s" % active_path)


if __name__ == "__main__":
    main()
