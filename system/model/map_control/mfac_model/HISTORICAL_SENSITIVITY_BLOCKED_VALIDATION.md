# Scheme 2 Historical Sensitivity: Date-Blocked Validation Gate

## Why this gate exists

Scheme 2 still targets historical, condition-aware dual-response sensitivities:

```text
historical process data
    -> condition/context/grid evidence
    -> phi_so2 < 0
    -> phi_ph  > 0
    -> offline prior
    -> online recursive correction
```

A full-sample bootstrap is not enough to publish a historical sensitivity.
Operator interventions, chemistry, limestone quality and process state can drift
across days. A fit may look physically correct when all dates are mixed while
changing sign when a later/earlier date block is withheld.

Therefore historical LOCAL_GAIN now has two separate gates:

1. full-sample model/bootstrap review;
2. calendar-date blocked validation and model-complexity selection.

Only a candidate that survives both may proceed to a separate surface/map
publication review.

## Validation semantics

`historical_sensitivity_validation.py` splits events by complete calendar dates.
No event from a holdout date is used in that fold's fit.

For every holdout event it evaluates the learned local marginal sensitivity at
the holdout work point and checks:

```text
phi_so2(work point) < 0
phi_ph(work point)  > 0
```

It also evaluates the linearized local effect:

```text
predicted SO2 local effect = phi_so2(work point) * delta_q_actual
predicted pH  local effect = phi_ph(work point)  * delta_q_actual
```

against a zero-local-effect baseline. This is intentionally not a claim that the
local gain model predicts the whole plant response. It asks the narrower MFAC
question: does the inferred local sensitivity carry useful signal on unseen
calendar dates?

All outputs remain:

```text
REVIEW_ONLY
NOT_ACTIVATABLE
LEARN = 0
Residual = 0
DCS write = off
```

## Model complexity is evidence-selected

Historical mapping does not require exact-state lookup. However, continuous
within-grid surfaces are not automatically better.

The review ladder is:

```text
GRID_SCALAR
    -> GRID_INLET_SURFACE
    -> GRID_INLET_PH_SURFACE
    -> higher-dimensional surface
```

The selection policy is `SIMPLEST_PASSING`.

A scalar grid prior is still an online mapping: every work point inside that grid
uses the reviewed local prior, and missing grids may use reviewed neighboring
grid interpolation. A higher-dimensional work-point surface is added only when
blocked validation demonstrates that the extra complexity generalizes.

## Current CSV audit result

Source:

```text
new_data_train_10s.csv
sha256 = 5553e529b1f222544b40b9e31e992584e87b4c4c1cbb51eea2bf460595b3ea23
```

A reproducible audit extraction produced 493 isolated operator pulse events
across 36 dates. This audit is still grid-level and is not allowed to invent a
historical `condition_snapshot_version` or `condition_label` that is absent from
the CSV.

Using five calendar-date holdout blocks and an audit-only dual-channel review
threshold, the individual grids did **not** survive dual-response validation:

| Grid | SO2 holdout direction | pH holdout direction | Result |
| --- | ---: | ---: | --- |
| P13-S1 | 80.0% | 45.5% | reject |
| P14-S1 | 100.0% | 70.0% | reject |
| P15-S1 | 58.3% | 41.7% | reject |

P14 is the strongest individual grid, but the pH channel is still below the
review threshold when holdout events are weighted correctly.

The pooled scalar candidate was materially more stable:

```text
SO2 holdout physical direction = 100%
pH  holdout physical direction = 100%
SO2 center sign across folds    = 100%
pH  center sign across folds    = 100%
median SO2 zero-effect skill    = +0.128
median pH  zero-effect skill    = +0.356
```

It is therefore the only result from this audit allowed to move to the **next
review stage**, and only as a possible low-confidence pooled fallback. It is not
published to runtime.

The machine-readable audit is:

```text
calibration_audits/
MFAC-HISTORICAL-BLOCKED-VALIDATION-5553E529-20260828.json
```

## What this changes about earlier P13/P14/P15 candidates

Earlier full-sample fits showed attractive physical signs for P13/P14/P15.
Date-blocked validation is intentionally allowed to downgrade those candidates.
They must not be promoted merely because their full-sample bootstrap looked
stable.

This is not a failure of the historical route. It is evidence that the current
historical pulse data supports a broad plant-level marginal prior more strongly
than a stable dual-channel per-grid derivative.

## Online mapping remains hierarchical

The online resolution contract remains:

```text
EXACT_CONTEXT
    -> EXACT_GRID
    -> NEIGHBOR_INTERPOLATED
    -> POOLED_FALLBACK
    -> MFAC_PRIOR_UNAVAILABLE
```

`MFAC_PRIOR_UNAVAILABLE` never removes Dynamic Qbase. It only means there is no
reviewed historical MFAC correction prior for that state.

When a channel later accepts real online evidence, its online state becomes
authoritative and historical mapping must not overwrite it.

## Required next evidence

The next formal step is to run this same validation pipeline on canonical
HistoricalEpisodeEngine output so events carry real:

```text
condition_snapshot_version
mfac_context_id
grid_id
```

Then the system can answer, without invented history, which actual MFAC contexts
have stable `phi_so2`, stable `phi_ph`, and what model complexity each context can
support.

Manual small-step trials remain optional validation/supplement evidence for weak
contexts. They are not the prerequisite for the historical bootstrap route.
