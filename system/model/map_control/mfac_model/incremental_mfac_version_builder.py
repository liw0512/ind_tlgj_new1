# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from system.model.map_control.mfac_model.version_artifacts import (
    build_mfac_version_artifact,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare incremental MFAC second-module version artifact"
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--previous", required=True)
    parser.add_argument("--condition-snapshot", required=True)
    parser.add_argument("--config", default="")
    args = parser.parse_args()
    del args.config
    manifest = build_mfac_version_artifact(
        input_csv=args.input,
        output_root=args.output,
        condition_snapshot=args.condition_snapshot,
        mode="INCREMENTAL",
        previous_snapshot=args.previous,
    )
    print(manifest["manifest_path"])


if __name__ == "__main__":
    main()
