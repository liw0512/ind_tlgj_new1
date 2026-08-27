# Scheme 2 LOCAL_GAIN multi-trial cohort review

## Purpose

A single successful supervised `+2 m3/h` identification trial is not enough to
establish a trustworthy local MFAC sensitivity.  Scheme 2 therefore separates
three different evidence stages:

```text
one physical trial
-> individual dual-response validation
-> individual human evidence review
-> same-context multi-trial cohort review
-> offline bootstrap review
```

The learned quantities remain:

```text
phi_so2 = delta_SO2 / delta_Q_actual < 0
phi_ph  = delta_pH  / delta_Q_actual > 0
```

SO2 and pH always come from the same reviewed physical action, but their
cross-trial consistency is checked independently.

## Why one trial cannot seed MFAC

One trial may be valid and still be unrepresentative because of unobserved plant
state, measurement noise, actuator tracking error or a local transient.  The
bootstrap trainer must therefore not treat one manually reviewed action as a
stable plant gain.

For supervised manual local-step evidence, bootstrap now requires:

```text
individual evidence review approved
AND
same condition snapshot
AND
same MFAC context
AND
reviewed Trial Matrix count requirement met
AND
reviewed Trial Matrix independent-day requirement met
AND
delta_Q_actual cross-trial consistency passed
AND
phi_so2 cross-trial consistency passed
AND
phi_ph cross-trial consistency passed
AND
explicit human cohort bootstrap review approved
```

Only then may copies of the reviewed events be consumed by the SO2 and pH
offline bootstrap trainers.

## Parameter ownership

The cohort gate deliberately does not duplicate trial-count policy.

```text
LocalStepTrialMatrix owns:
    required_valid_trials
    required_independent_days

LocalGainCohortReviewProfile owns:
    max_relative_mad_delta_q
    max_relative_mad_phi_so2
    max_relative_mad_phi_ph
    max_relative_deviation_delta_q
    max_relative_deviation_phi_so2
    max_relative_deviation_phi_ph
```

No site defaults are provided for the six consistency limits.

The current audit artifact is:

```text
calibration_audits/MFAC-LOCAL-GAIN-COHORT-DESIGN-5553E529-20260827.json
```

All six reviewed values remain null because the historical CSV contains no
sufficient controlled local-gain cohort from which those limits could honestly
be inferred.

## Robust consistency checks

For each same-context cohort the gate calculates, separately for
`delta_Q_actual`, `phi_so2` and `phi_ph`:

```text
median
P10 / P25 / P75 / P90
MAD
relative_MAD = MAD / abs(median)
max_relative_deviation = max(abs(x - median)) / abs(median)
```

Both relative MAD and maximum relative deviation are required.  This is
intentional: with a small Phase-1 cohort, two similar trials plus one strong
outlier may still produce a deceptively small MAD.

The quantitative statuses are:

```text
REJECTED_INVALID_COHORT
INSUFFICIENT_EVIDENCE
INCONSISTENT_LOCAL_GAIN
ADEQUATE_FOR_BOOTSTRAP_REVIEW
```

`ADEQUATE_FOR_BOOTSTRAP_REVIEW` is not approval and grants no learning
permission by itself.

## Compatibility boundary

Older V2 `promote_local_step_evidence()` records may carry:

```text
learning_eligible = true
```

from the pre-cohort design.  For manual local-step events this field is no
longer sufficient authority.  Both bootstrap trainers now additionally require:

```text
cohort_bootstrap_review_approved = true
offline_bootstrap_evidence_allowed = true
automatic_online_adaptation_allowed = false
```

Therefore an old single-trial event cannot bypass the new cohort gate even if it
still contains the legacy boolean.

The cohort evaluator records such migrated records under:

```text
legacy_premature_learning_event_ids
```

for audit visibility.

## Explicit cohort approval

After the quantitative cohort reaches:

```text
ADEQUATE_FOR_BOOTSTRAP_REVIEW
```

`approve_local_gain_cohort_for_bootstrap()` still requires explicit human
approval and reviewer/time metadata.  It creates new event copies with cohort
approval metadata; it does not mutate the original reviewed trial records.

The approved copies remain:

```text
automatic_online_adaptation_allowed = false
normal_runtime_activation_allowed   = false
```

Their only new permission is offline bootstrap evidence consumption.

## Resulting dual-channel bootstrap

The same cohort-approved event set is then passed to:

```text
build_bootstrap_evidence(...)      -> SO2 phi seed / replay
build_ph_bootstrap_evidence(...)   -> pH phi seed / replay
```

This preserves physical action identity while keeping the two response channels
independent.

Even after bootstrap profiles exist, production activation is a separate
reviewed lifecycle.  The current production state remains:

```text
LEARN = 0
Residual = 0
DCS write = off
```
