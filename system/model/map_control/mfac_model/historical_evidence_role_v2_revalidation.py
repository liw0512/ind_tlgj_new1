# -*- coding: utf-8 -*-
"""Offline Evidence Role V2 revalidation overlay.

This module intentionally does not modify HistoricalEpisodeEngine extraction or
ConditionSnapshot generation.  It consumes already-produced episode artifacts
plus the canonical MAJORITY/formal-switch replay audit and reroutes evidence
under ``SCHEME2_HISTORICAL_EVIDENCE_V2``.

The overlay is read-only with respect to runtime authority: it cannot enable
online LEARN, Residual control, or DCS write.  In particular,
``DISTURBANCE_COUPLED_DYNAMIC`` means temporal/confounded overlap with a
canonical process transition.  It is never a causal local-gain sample and never
publishes ``phi_so2``/``phi_ph``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from system.model.config.plant_config import PLANT_CONFIG

from .historical_episode_engine.historical_evidence import (
    DISTURBANCE_COUPLED_DYNAMIC_EVIDENCE,
    DYNAMIC_CLEAN_EVIDENCE,
    HISTORICAL_EVIDENCE_SEMANTICS_VERSION,
    LOCAL_GAIN_EVIDENCE,
    SAFETY_EVIDENCE,
    HistoricalEvidenceRoutingConfig,
    _role_decision,
    attach_canonical_condition_transition_evidence,
)


PROCESS_STATE_CHANGED_REASON = "PROCESS_STATE_CHANGED_DURING_EVENT"
PROCESS_STATE_ONLY_INVALID_REASON = (
    "FLOW_CONTEXT_NOT_CLEAN:PROCESS_STATE_CHANGED_DURING_EVENT"
)


def _read_csv(path: str | Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _read_episode_files(paths: Sequence[str | Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in paths:
        frame = _read_csv(path)
        if frame.empty:
            continue
        frame = frame.copy()
        frame["mfac_role_v2_source_file"] = str(path)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True, sort=False)
    if "episode_id" not in result.columns:
        raise KeyError("episode artifacts are missing required column 'episode_id'")
    ids = result["episode_id"].dropna().astype(str)
    if ids.duplicated().any():
        duplicate = ids.loc[ids.duplicated()].iloc[0]
        raise ValueError(f"duplicate episode_id across input artifacts: {duplicate}")
    return result


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y", "on"}


def _truth_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index, dtype=bool)
    return frame[column].map(_bool_value).astype(bool)


def _process_transition_only_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(False, index=frame.index, dtype=bool)
    context = frame.get("flow_context_reason", pd.Series("", index=frame.index)).fillna("").astype(str)
    invalid = frame.get("invalid_reason", pd.Series("", index=frame.index)).fillna("").astype(str)
    return context.eq(PROCESS_STATE_CHANGED_REASON) & invalid.eq(
        PROCESS_STATE_ONLY_INVALID_REASON
    )


def _require_replay_coverage(
    episodes: pd.DataFrame,
    replay_detail: pd.DataFrame,
) -> None:
    if episodes.empty:
        return
    if "episode_id" not in replay_detail.columns:
        raise KeyError("replay detail is missing required column 'episode_id'")
    process_mask = _process_transition_only_mask(episodes)
    target_ids = set(episodes.loc[process_mask, "episode_id"].dropna().astype(str))
    replay_ids = set(replay_detail["episode_id"].dropna().astype(str))
    missing = sorted(target_ids - replay_ids)
    if missing:
        preview = ", ".join(missing[:5])
        raise KeyError(
            f"canonical replay detail is missing {len(missing)} process-transition episodes: {preview}"
        )


def revalidate_historical_evidence_roles(
    episodes: pd.DataFrame,
    replay_detail: pd.DataFrame,
    *,
    plant: Mapping[str, Any] = PLANT_CONFIG,
    routing_config: HistoricalEvidenceRoutingConfig | Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Apply Evidence Role V2 to existing episode artifacts without re-extraction."""
    if episodes.empty:
        return episodes.copy()
    _require_replay_coverage(episodes, replay_detail)
    attached = attach_canonical_condition_transition_evidence(episodes, replay_detail)
    config = (
        routing_config
        if isinstance(routing_config, HistoricalEvidenceRoutingConfig)
        else HistoricalEvidenceRoutingConfig.from_mapping(routing_config)
    )

    records: list[dict[str, Any]] = []
    for record in attached.to_dict(orient="records"):
        enriched = dict(record)
        decision = _role_decision(enriched, plant, config)
        enriched["mfac_evidence_roles"] = "|".join(decision.roles)
        enriched["mfac_local_gain_eligible"] = bool(decision.local_gain_eligible)
        enriched["mfac_independent_local_gain_eligible"] = bool(
            decision.local_gain_eligible
        )
        enriched["mfac_dynamic_evidence_eligible"] = bool(
            decision.dynamic_observation_eligible
        )
        enriched["mfac_dynamic_observation_eligible"] = bool(
            decision.dynamic_observation_eligible
        )
        enriched["mfac_dynamic_clean_eligible"] = bool(
            decision.dynamic_clean_eligible
        )
        enriched["mfac_disturbance_coupled_dynamic_eligible"] = bool(
            decision.disturbance_coupled_dynamic_eligible
        )
        enriched["mfac_safety_evidence"] = bool(decision.safety_evidence)
        enriched["mfac_evidence_reasons"] = "|".join(decision.reasons)
        enriched["mfac_phi_so2_event"] = decision.metrics.get("phi_so2_event")
        enriched["mfac_phi_ph_event"] = decision.metrics.get("phi_ph_event")
        enriched["mfac_evidence_metrics"] = json.dumps(
            decision.metrics,
            ensure_ascii=False,
            sort_keys=True,
        )
        enriched["mfac_evidence_semantics_version"] = decision.semantics_version
        records.append(enriched)
    return pd.DataFrame(records)


def _sum_bool(frame: pd.DataFrame, column: str) -> int:
    if frame.empty:
        return 0
    return int(_truth_series(frame, column).sum())


def _count_role(frame: pd.DataFrame, role: str) -> int:
    if frame.empty or "mfac_evidence_roles" not in frame.columns:
        return 0
    return int(
        frame["mfac_evidence_roles"]
        .fillna("")
        .astype(str)
        .str.split("|")
        .map(lambda values: role in values)
        .sum()
    )


def build_role_v2_summary(frame: pd.DataFrame) -> dict[str, Any]:
    process_mask = _process_transition_only_mask(frame)
    non_local_mask = ~_truth_series(frame, "mfac_independent_local_gain_eligible")
    phi_so2 = pd.to_numeric(
        frame.get("mfac_phi_so2_event", pd.Series(index=frame.index, dtype=float)),
        errors="coerce",
    )
    phi_ph = pd.to_numeric(
        frame.get("mfac_phi_ph_event", pd.Series(index=frame.index, dtype=float)),
        errors="coerce",
    )
    disturbance_mask = _truth_series(
        frame, "mfac_disturbance_coupled_dynamic_eligible"
    )
    valid_count = _sum_bool(frame, "valid")

    return {
        "semantics_version": HISTORICAL_EVIDENCE_SEMANTICS_VERSION,
        "input_episode_count": int(len(frame)),
        "original_valid_count": valid_count,
        "original_invalid_count": int(len(frame) - valid_count),
        "canonical_condition_changed_count": _sum_bool(
            frame, "mfac_canonical_condition_changed"
        ),
        "process_transition_only_count": int(process_mask.sum()),
        "local_gain_count": _count_role(frame, LOCAL_GAIN_EVIDENCE),
        "dynamic_clean_count": _count_role(frame, DYNAMIC_CLEAN_EVIDENCE),
        "disturbance_coupled_dynamic_count": _count_role(
            frame, DISTURBANCE_COUPLED_DYNAMIC_EVIDENCE
        ),
        "dynamic_observation_count": _sum_bool(
            frame, "mfac_dynamic_observation_eligible"
        ),
        "safety_count": _count_role(frame, SAFETY_EVIDENCE),
        "unassigned_dynamic_count": int(
            len(frame) - _sum_bool(frame, "mfac_dynamic_observation_eligible")
        ),
        "disturbance_coupled_phi_non_null_count": int(
            ((phi_so2.notna() | phi_ph.notna()) & disturbance_mask).sum()
        ),
        "non_local_gain_phi_non_null_count": int(
            ((phi_so2.notna() | phi_ph.notna()) & non_local_mask).sum()
        ),
        "diagnostic_overlay_only": True,
        "changes_historical_episode_validity": False,
        "changes_runtime_permissions": False,
        "disturbance_coupled_semantics": "TEMPORAL_CONFOUNDED_OVERLAP_NOT_CAUSAL_GAIN",
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(dict(value), stream, ensure_ascii=False, indent=2, allow_nan=False)


def run_revalidation(
    *,
    episode_csvs: Sequence[str],
    replay_detail_csv: str,
    output_dir: str,
) -> dict[str, Any]:
    episodes = _read_episode_files(episode_csvs)
    replay_detail = _read_csv(replay_detail_csv)
    result = revalidate_historical_evidence_roles(
        episodes,
        replay_detail,
        plant=PLANT_CONFIG,
        routing_config={},
    )
    summary = build_role_v2_summary(result)

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    detail_path = root / "historical_evidence_role_v2_detail.csv"
    summary_path = root / "historical_evidence_role_v2_summary.json"
    result.to_csv(detail_path, index=False, encoding="utf-8-sig")
    _write_json(summary_path, summary)

    output = dict(summary)
    output["detail_csv"] = str(detail_path)
    output["summary_json"] = str(summary_path)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Revalidate existing historical episode artifacts under canonical-aware "
            "Scheme-2 MFAC Evidence Role V2."
        )
    )
    parser.add_argument(
        "--episodes",
        nargs="+",
        required=True,
        help="one or more valid/invalid historical episode CSV artifacts",
    )
    parser.add_argument("--replay-detail", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)

    result = run_revalidation(
        episode_csvs=args.episodes,
        replay_detail_csv=args.replay_detail,
        output_dir=args.output_dir,
    )
    print("========== HISTORICAL EVIDENCE ROLE V2 REVALIDATION ==========")
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
