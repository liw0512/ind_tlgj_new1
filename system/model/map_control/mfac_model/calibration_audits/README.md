# Scheme 2 calibration audit artifacts

Files in this directory are **historical evidence snapshots or manual-test design
reviews**, not runtime configuration and not activation artifacts.

Required semantics:

```text
activation_status = NOT_ACTIVATABLE
```

Historical timing profiles additionally remain `REVIEW_REQUIRED`. Manual
identification designs remain manual-only and may never enable automatic
execution, automatic step escalation, DCS write, or runtime learning.

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

## Manual LOCAL_GAIN identification design

`MFAC-LOCAL-STEP-DESIGN-5553E529-20260827.json` addresses the missing low-flow
LOCAL_GAIN evidence. It deliberately separates:

```text
review_candidate_parameters
reviewed_parameters
```

The inherited Phase-1 engineering candidate is visible for review:

```text
step_up_m3_h      = 2.0
max_step_up_m3_h  = 2.0
reach_tolerance   = 0.5 m3/h
baseline          = 300 s
Qbase abs drift   = 2.0 m3/h
Qbase rel drift   = 6%
pH margins        = 0.05 / 0.05
SO2 observation   = 600 s
pH observation    = 900 s
effect timeout    = 1800 s
candidate interval= 3600 s
```

These are **review candidates**, not reviewed site parameters. `reviewed_parameters`
remain null, so the current design is:

```text
status            = INCOMPLETE_REVIEW_REQUIRED
activation_status = NOT_ACTIVATABLE
ready_for_manual_session = false
```

The following important fields still require explicit plant/engineering review
or additional data evidence:

```text
required_valid_trials
required_independent_days
min_quiet_seconds
max_abs_actual_minus_qbase
max_abs_inlet_so2_change
max_ph_baseline_range
max_sample_gap_seconds
outlet_so2_headroom_to_safe_max
min_abs_delta_so2
min_abs_delta_ph
```

The typed `LocalStepIdentificationDesignProfile` is intentionally fail-closed:

- candidate values never fill reviewed values;
- any required reviewed field left null blocks manual config construction;
- `activation_status` must stay `NOT_ACTIVATABLE`;
- `automatic_execution_allowed`, `automatic_escalation_allowed`,
  `dcs_write_enabled`, and `learning_permission` must all remain false;
- `to_runtime_config()` always raises.

A successful manually executed trial also does not update MFAC immediately. The
required chain is:

```text
proposal REVIEW_CANDIDATE
-> first human approval
-> manual action
-> real actual-flow REACHED event
-> independent SO2 + pH response completion
-> LOCAL_GAIN_EVIDENCE_CANDIDATE
-> second human evidence review
-> canonical reviewed ActionResponseEvent
```

The promoted event contains both `delta_so2` and `delta_ph` from the same
physical trial. The manual return-to-baseline action is recovery only and is not
learnable.

## Consequence for next identification work

The historical pulse dataset is adequate for DYNAMIC and SAFETY evidence but
not for the low-flow proactive staircase shape we actually want to operate.
Therefore the remaining work is intentionally split:

```text
historical pulse data
-> timing / memory / risk evidence

controlled local-step identification
-> local phi_so2 / phi_ph
-> safe step magnitude support
-> low-flow staircase response support
```

Before a Phase-1 manual session can even be considered, the remaining null
review fields above must be resolved. Data-derived quantities such as sample-gap
and baseline-noise thresholds may be estimated from the real CSV; policy/safety
choices such as required trial count/days and outlet-SO2 abort headroom require
explicit engineering review.

Production permissions remain unchanged:

```text
LEARN = 0
Residual = 0
DCS write = off
```
