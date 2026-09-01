# First-module seeded V2 production hardening

## Status

The train -> v002 -> v003 -> v004 replay validated the 1.1 core lifecycle:

- fixed 100 mg/Nm3 base grid;
- fixed seeded operating regions 10001..10006;
- robust liquid/gas histogram evidence by base-grid + circulation-pump count;
- baseline warmup and promotion;
- STABLE absorption;
- WATCH / SUSPECTED_CONTEXT_SHIFT / STRONG_CONTEXT_SHIFT hold;
- insufficient-evidence pause without clearing pending state;
- supported-version confirmation;
- no automatic region merge/split.

Production hardening is layered in `seeded_region_hardening.py` as schema 1.2.
The replay-validated 1.1 classifier is intentionally not rewritten.

## Canonical seeded training entrypoint

Use:

```text
system/model/map_control/condition_model/seeded_training_v2.py
```

Do not use the legacy production helpers `build_initial_condition_csv()` or
`build_incremental_condition_csv()` for seeded V2 publication. Their AutoMerge
path is retained only for historical compatibility.

The seeded incremental entrypoint calls only the additive
`IncrementalConditionUpdater.update()` method and then the hardened seeded
manager. It does not call `AutoMergeManager.apply()`.

A hardened seeded snapshot publishes:

```text
condition_region_v2.schema_version = 1.2
condition_region_v2.legacy_auto_merge_bypassed = true
grid_config.merge.enabled = false
grid_config.merge.mode = disabled
```

## Context shift is not process drift

Liquid/gas is a derived operating-context quantity. A confirmed context shift
is therefore only a persistent distribution change and cannot by itself:

- merge or split operating regions;
- prove Q -> SO2 or Q -> pH dynamic drift;
- update the controller model;
- automatically replace the reference context baseline.

Quasi-free process evidence and module-2 dynamic evidence remain separate.

## Compact structure report

Schema 1.2 adds per-region fields:

```text
pending_context_shift_statuses
pending_context_shift_count
active_pending_context_shift_count
paused_pending_context_shift_count
confirmed_context_shift_count
requires_context_review
pending_continuity_states
```

Top-level report fields include:

```text
pending_context_shift_count
confirmed_context_shift_count
manual_context_review_required
context_resolution_history_count
legacy_auto_merge_bypassed
```

This makes pending/confirmed lifecycle visible without reading the full
condition snapshot.

## Confirmed-context resolution

Confirmation never replaces a reference automatically. An explicit review must
choose one of:

### KEEP_REFERENCE

The observed context is considered temporary or not suitable as the new normal.
Keep the current reference baseline and clear the reviewed pending item.
Future supported shifts may start a new pending sequence.

### ACCEPT_NEW_CONTEXT_BASELINE

The changed operating context is accepted as the new normal. This decision is
allowed only when:

- the pending context shift is confirmed; and
- held candidate evidence itself satisfies the baseline sample/day support gate.

The candidate distribution replaces the old context reference only for the
specific base-grid + pump stratum, and its
`context_reference_generation_by_grid_pump` increments by one. The old decision
remains traceable in `context_resolution_history`.

This decision still has no operating-region merge/split authority.

### SENSOR_OR_DATA_ISSUE

The shift is attributed to instrumentation/data-quality concerns. Keep the
current reference and clear the reviewed pending item. Sensor quarantine and
repair workflow are outside this first-module artifact.

## Resolution JSON

Pass an optional JSON object to incremental training:

```text
--context-resolutions path/to/context_resolutions.json
```

Example:

```json
{
  "P5-S1::XP3-AP0": {
    "decision": "KEEP_REFERENCE",
    "reviewer": "offline-review",
    "reason": "temporary gas-flow operating window",
    "reviewed_at": "2026-09-01T12:00:00+08:00"
  },
  "P15-S1::XP3-AP0": {
    "decision": "ACCEPT_NEW_CONTEXT_BASELINE",
    "reviewer": "offline-review",
    "reason": "new operating context validated against plant records",
    "reviewed_at": "2026-09-01T12:00:00+08:00"
  }
}
```

Unknown/non-pending strata and unsupported decisions fail closed. Accepting a
new context from an unconfirmed or under-supported candidate also fails closed.

## Cutover rule

Do not incrementally convert a legacy AutoMerge snapshot into seeded V2. The
seeded incremental entrypoint rejects a base snapshot without
`metadata.condition_region_v2`. Start the production seeded lifecycle from a
fresh seeded initial snapshot and then increment it chronologically.
