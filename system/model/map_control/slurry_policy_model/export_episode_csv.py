from __future__ import annotations

"""把 V1.8B 快照中的内部 DataFrame pickle 导出为人工审计 CSV。"""

import argparse
from pathlib import Path

import pandas as pd


DATASETS = (
    ("valid_decision_episodes.pkl", "valid_decision_episodes.csv"),
    ("invalid_decision_episodes.pkl", "invalid_decision_episodes.csv"),
    ("context_tail.pkl", "context_tail.csv"),
)


def export_episode_csv(snapshot_dir: str | Path, overwrite: bool = False) -> list[Path]:
    snapshot = Path(snapshot_dir)
    datasets = snapshot / "datasets"
    if not datasets.is_dir():
        raise FileNotFoundError(f"快照缺少 datasets 目录: {datasets}")

    outputs: list[Path] = []
    for pickle_name, csv_name in DATASETS:
        source = datasets / pickle_name
        target = datasets / csv_name
        if not source.exists():
            continue
        if target.exists() and not overwrite:
            outputs.append(target)
            continue
        frame = pd.read_pickle(source)
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"{source} 不是 pandas DataFrame")
        frame.to_csv(target, index=False, encoding="utf-8-sig")
        outputs.append(target)
    if not outputs:
        raise FileNotFoundError(f"没有找到可导出的 pickle 数据集: {datasets}")
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 V1.8B episode/context CSV")
    parser.add_argument("snapshot_dir", help="第二模块 snapshots/v### 目录")
    parser.add_argument(
        "--overwrite", action="store_true", help="覆盖已经存在的 CSV"
    )
    args = parser.parse_args()
    for path in export_episode_csv(args.snapshot_dir, args.overwrite):
        print(path)


if __name__ == "__main__":
    main()
