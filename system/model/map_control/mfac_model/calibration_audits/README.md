# Scheme 2 calibration audit artifacts

Files in this directory are **historical evidence snapshots**, not runtime
configuration and not activation artifacts.

Required semantics:

```text
activation_status = NOT_ACTIVATABLE
review_status     = REVIEW_REQUIRED
```

The typed boundary is `Scheme2TrajectoryCalibrationProfile`. Its
`to_runtime_config()` method intentionally raises: no historical audit file may
silently become production calibration.

## Current real-data timing audit

`MFAC-TRAJ-AUDIT-5553E529-20260827.json` is bound to:

```text
new_data_train_10s.csv
SHA256 5553e529b1f222544b40b9e31e992584e87b4c4c1cbb51eea2bf460595b3ea23
298875 rows
2026-06-14 00:00:00 -> 2026-07-22 00:00:00
median cadence 10 s
```

The current evidence supports review of timing only:

```text
pH onset P90              ~ 190 s
pH peak P90               ~ 886 s
SO2 improvement onset P90 ~ 310 s
planner HOLD candidate    ~ 360 s
```

The pH memory is right-censored. In 140 events with a clean 30-minute
post-action observation window, only 28 decayed to half peak amplitude inside
that window. Therefore:

```text
response_memory_lower_bound_seconds = 1800
response_memory_candidate_seconds   = null
```

Historical small-step evidence remains insufficient, so the audit deliberately
leaves these fields null:

```text
max_step_up_candidate
max_step_down_candidate
demand_deadband_candidate
```

Do not fill those values from the historical 60-70+ m3/h operator pulse shape.
Large pulses are DYNAMIC/SAFETY evidence, not LOCAL_GAIN action templates.

## Staircase counterfactual candidate set

`MFAC-STAIRCASE-CANDIDATES-5553E529-20260827.json` contains equal-dose Shadow
candidates only. Equal dose is used to isolate action-shape effects offline; it
is not an online slurry-volume obligation.

Counterfactual stages are defined as **extra supply flow above the event
baseline**. Historical support must therefore use the same physical quantity.
For the 725 validated dynamic pulse events:

```text
absolute running-flow P05/P95        = 57.8768 / 88.1057 m3/h  # audit only
sustained extra-flow P05/P95         = 51.0720 / 86.3891 m3/h  # support gate
action-duration P05/P95              = 320 / 730 s
observed proactive-advance support   = 0 s
```

The current staircase candidates use sustained extra-flow levels around
`10..40 m3/h`, total durations around `913..1096 s`, and proactive advance of
`300..600 s`. Therefore all current candidates are explicitly:

```text
EXPLORATION_ONLY_OUT_OF_HISTORICAL_SUPPORT
eligible_for_step_calibration_evidence = false
```

Reasons are checked independently:

```text
SUSTAINED_EXTRA_FLOW_OUT_OF_HISTORICAL_SUPPORT
TOTAL_DURATION_OUT_OF_HISTORICAL_SUPPORT
PROACTIVE_ADVANCE_OUT_OF_HISTORICAL_SUPPORT
```

A future response model may extrapolate those candidates for engineering
exploration, but an extrapolated score must never be used to calibrate
`max_step_up/down`. A candidate can become step-calibration evidence only after
its action shape is supported by real controlled observations.

## Consequence for next identification work

The historical pulse dataset is adequate for DYNAMIC and SAFETY evidence but
not for the low-flow proactive staircase shape we actually want to operate.
Therefore the next evidence gap is intentional:

```text
historical pulse data
-> timing / memory / risk evidence

controlled local-step identification
-> local phi_so2 / phi_ph
-> safe step magnitude support
-> low-flow staircase response support
```

Production permissions remain unchanged:

```text
LEARN = 0
Residual = 0
DCS write = off
```
