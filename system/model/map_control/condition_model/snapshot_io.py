# -*- coding: utf-8 -*-
"""JSON snapshot persistence with staged atomic publication.

New snapshots are strict JSON: NaN and Infinity are rejected before the staged
file replaces the current version.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Tuple

from system.model.slurry_control.condition_model.condition_schema import (
    ConditionSnapshot,
    GridCell,
    PolicyRegion,
)


def snapshot_to_dict(snapshot: ConditionSnapshot) -> Dict:
    return {
        "snapshot_version": snapshot.snapshot_version,
        "build_time": snapshot.build_time,
        "previous_snapshot_version": snapshot.previous_snapshot_version,
        "grid_config": snapshot.grid_config,
        "grid_catalog": {
            key: value.to_dict()
            for key, value in snapshot.grid_catalog.items()
        },
        "grid_adjacency": snapshot.grid_adjacency,
        "policy_regions": {
            key: value.to_dict()
            for key, value in snapshot.policy_regions.items()
        },
        "metadata": snapshot.metadata,
    }


def write_snapshot(snapshot: ConditionSnapshot, target_path: str) -> None:
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=target.name,
        suffix=".staging",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                snapshot_to_dict(snapshot),
                stream,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
        os.replace(temporary, target)
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _clean_legacy_metadata(metadata: Dict) -> Dict:
    result = dict(metadata or {})
    result.pop("action_event_registry", None)
    result["snapshot_schema_version"] = result.get(
        "snapshot_schema_version",
        "legacy",
    )
    return result


def read_snapshot(path: str) -> ConditionSnapshot:
    with open(path, "r", encoding="utf-8") as stream:
        value = json.load(stream)
    return ConditionSnapshot(
        snapshot_version=value["snapshot_version"],
        build_time=value["build_time"],
        previous_snapshot_version=value.get("previous_snapshot_version"),
        grid_config=value["grid_config"],
        grid_catalog={
            key: GridCell.from_dict(item)
            for key, item in value["grid_catalog"].items()
        },
        grid_adjacency=value["grid_adjacency"],
        policy_regions={
            key: PolicyRegion.from_dict(item)
            for key, item in value["policy_regions"].items()
        },
        metadata=_clean_legacy_metadata(value.get("metadata", {})),
    )


def _version_sort_key(path: Path) -> int:
    suffix = path.name[1:]
    return (
        int(suffix)
        if path.name.lower().startswith("v") and suffix.isdigit()
        else -1
    )


def _snapshot_candidates_from_dir(snapshots_dir: Path):
    if not snapshots_dir.exists():
        return []
    return [
        item / "condition_snapshot.json"
        for item in sorted(
            snapshots_dir.iterdir(),
            key=_version_sort_key,
            reverse=True,
        )
        if item.is_dir() and item.name.lower().startswith("v")
    ]


def read_latest_available_snapshot(
    snapshot_path: str,
) -> Tuple[ConditionSnapshot, str]:
    requested = Path(snapshot_path)
    if str(snapshot_path).strip().lower() == "latest":
        snapshots_dir = Path(__file__).resolve().parent / "snapshots"
        candidates = _snapshot_candidates_from_dir(snapshots_dir)
    elif requested.is_dir():
        candidates = _snapshot_candidates_from_dir(requested)
    else:
        snapshots_dir = (
            requested.parent.parent
            if requested.parent.name.lower().startswith("v")
            else requested.parent
        )
        candidates = [requested]
        candidates.extend(
            path
            for path in _snapshot_candidates_from_dir(snapshots_dir)
            if path != requested
        )

    errors = []
    for candidate in candidates:
        try:
            return read_snapshot(str(candidate)), str(candidate)
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    detail = (
        "; ".join(errors)
        if errors
        else f"No snapshot candidates found for {snapshot_path}"
    )
    raise FileNotFoundError(
        f"No readable condition snapshot found. {detail}"
    )


def cleanup_old_snapshot_versions(
    snapshot_path: str,
    max_versions_to_keep: int,
) -> None:
    if max_versions_to_keep <= 0:
        return
    version_dir = Path(snapshot_path).parent
    snapshots_dir = version_dir.parent
    if not snapshots_dir.exists():
        return
    version_dirs = [
        item
        for item in snapshots_dir.iterdir()
        if item.is_dir() and item.name.lower().startswith("v")
    ]
    if len(version_dirs) <= max_versions_to_keep:
        return

    for old_dir in sorted(
        version_dirs,
        key=_version_sort_key,
    )[:-max_versions_to_keep]:
        shutil.rmtree(old_dir)
