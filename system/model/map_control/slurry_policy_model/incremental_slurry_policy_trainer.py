"""供浆历史动作响应模型——增量离线训练入口。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from slurry_policy_core import inspect_config, run_incremental_training
from activate_policy_version import activate_version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将第一模块增量标注 CSV 合并到上一版供浆历史动作响应版本"
    )
    parser.add_argument(
        "--input",
        nargs="+",
        help=(
            "新增 CSV、目录或通配符；未传时读取 "
            "PLANT_CONFIG.paths.default_incremental_input"
        ),
    )
    parser.add_argument(
        "--output",
        help="输出根目录；未传时读取 PLANT_CONFIG.paths.output_root",
    )
    parser.add_argument(
        "--previous",
        help="上一版第二模块 v### 目录；未传时自动扫描 output_root/snapshots 下最新可用版本",
    )
    parser.add_argument(
        "--condition-snapshot",
        help=(
            "第一模块 condition_snapshot.json、v###目录或 snapshots根目录；"
            "未传时读取 PLANT_CONFIG.paths.condition_snapshots_dir 的最新版本"
        ),
    )
    parser.add_argument(
        "--config",
        help="统一配置文件路径或模块名；未传时使用同目录 slurry_policy_config.py",
    )
    parser.add_argument(
        "--recalibrate",
        action="store_true",
        help=(
            "使用旧+新边界上下文重新标定自适应参数。默认关闭，"
            "增量训练沿用上一版 effective_config，避免阈值随小批数据漂移。"
        ),
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="关闭终端训练进度条；默认按 TRAINING_CONFIG.progress.enabled 显示",
    )
    parser.add_argument(
        "--activate-after-success",
        action="store_true",
        help=(
            "第二模块增量训练和快照写入全部成功后，立即验证并原子激活"
            "同版本第一/第二模块。正式生产建议默认不传，先审查训练报告后再"
            "单独运行 activate_policy_version.py。"
        ),
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="只打印完成默认值合并和校验后的有效配置，不执行训练",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.show_config:
            print(
                json.dumps(
                    inspect_config(args.config),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        snapshot = run_incremental_training(
            input_paths=args.input,
            output_root=args.output,
            previous_snapshot=args.previous,
            condition_snapshot=args.condition_snapshot,
            config_spec=args.config,
            recalibrate=args.recalibrate,
            progress_enabled=False if args.no_progress else None,
        )
        snapshot_path = Path(snapshot).resolve()
        print(f"增量离线训练完成: {snapshot_path}")
        if args.activate_after_success:
            version = snapshot_path.name
            active_path = activate_version(version, args.config)
            print(f"同版本第一/第二模块已原子激活: {version}")
            print(f"active_version.json: {active_path}")
        return 0
    except Exception as exc:
        print(f"增量离线训练失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
