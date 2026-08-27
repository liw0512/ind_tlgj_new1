# Scheme 2 calibration audit artifacts

Files in this directory are **historical evidence snapshots**, not runtime
configuration and not activation artifacts.

Required semantics:

```text
activation_status = NOT_ACTIVATABLE
review_status     = REVIEW_REQUIRED
```

No historical audit file may silently become production calibration.

## Source dataset

The current audit artifacts are bound to:

```text
new_data_train_10s.csv
SHA256 5553e529b1f222544b40b9e31e992584e87b4c4c1cbb51eea2bf460595b3ea23
298875 rows
2026-06-14 00:00:00 -> 2026-07-22 00:00:00
median cadence 10 s
```

## Timing ownership: do not collapse these values

The historical analysis now separates four different timing concepts:

```text
1. Pending pH rise
   response onset / response peak
   owner: PendingDoseGuard

2. SO2 feedback blind zone
   minimum HOLD before another staircase decision
   owner: FlowTrajectoryPlanner

3. Pulse recovery / identification quiet time
   time for pH to return close to its pre-action baseline
   owner: manual LOCAL_GAIN identification gate/session design

4. Identification session spacing
   minimum interval between supervised identification candidates
   owner: identification trial policy
```

They are intentionally **not** one generic `response_memory_seconds` value.

Current review candidates are:

```text
Pending pH onset       ~ 190 s   # pH onset P90
Pending pH peak        ~ 900 s   # rounded above pH peak P90 ~= 886 s
Planner HOLD           ~ 360 s   # rounded above SO2 improvement onset P90 ~= 310 s
Identification quiet   ~ 2700 s  # rounded above pulse recovery-band P95 ~= 2450 s
Candidate interval     ~ 3600 s  # separate conservative session-policy candidate
```

All values remain `REVIEW_CANDIDATE`, not reviewed production parameters.

## PendingDoseGuard owns onset -> peak only

A `phi_ph` value is a step sensitivity:

```text
phi_ph = delta pH / delta Q_actual
```

A sustained positive flow step does not decay because a timer expires.  The
future *unrealized* part of a step response decreases as the response approaches
its peak.  Once that individual delta-Q contribution reaches the calibrated
peak, its future incremental effect is zero.

Therefore the formal PendingDoseGuard runtime requires only:

```text
flow_change_deadband
response_onset_seconds
response_peak_seconds
max_sample_gap_seconds
```

Pulse half-decay/full recovery cannot be configured as PendingDoseGuard control
authority.  Legacy `response_memory_seconds` may be read only as an audit-volume
window and cannot change pending pH prediction.

## Separate pH pulse-recovery audit

`MFAC-TRAJ-AUDIT-5553E529-20260827.json` now uses V2 recovery-separated
semantics.  The original 725 validated dynamic events continue to provide
onset/peak evidence.  A separate recovery cohort is used only for pulse
recovery/quiet-time review.

Recovery cohort:

```text
analyzed events                  = 918
half-decay observed              = 897
recovery-to-band observed        = 741
```

Audit definition:

```text
pump duration            5..15 min
pre-action quiet         >= 10 min
baseline pH              6.0..6.4
sustained extra flow     >= 40 m3/h
sample gap               <= 30 s
pH peak increment        >= 0.05
next pump action         right-censors recovery
```

Observed distributions:

```text
pulse end -> pH peak
P50 ~ 220 s
P90 ~ 393 s
P95 ~ 450 s

peak -> half-decay
P50 ~ 590 s
P90 ~ 970 s
P95 ~ 1140 s

pulse end -> half-decay
P50 ~ 830 s
P90 ~ 1280 s
P95 ~ 1440 s

pulse end -> recovery band
P50 ~ 1030 s
P90 ~ 2020 s
P95 ~ 2450 s
```

Recovery band means:

```text
pH <= pre-pulse baseline + 0.05
sustained for approximately 120 s
```

The current engineering review candidate is:

```text
min_quiet_seconds = 2700 s  # 45 min
```

This is deliberately rounded above the recovery-band P95.  It still does not
authorize a trial: all normal baseline-stability gates must pass again.

## Local-step noise/stability audit

`MFAC-LOCAL-STEP-NOISE-AUDIT-5553E529-20260827.json` records the data-derived
noise/stability evidence.

Cadence:

```text
298874 adjacent intervals
298774 exactly 10 s
91 gaps > 30 s
```

Therefore `max_sample_gap_seconds = 30` is a review candidate.

Stable pH baseline evidence supports approximately:

```text
max_ph_baseline_range ~ 0.05
```

Very strict quiet-window natural changes establish a noise floor:

```text
pH half-window median difference
P95 ~ 0.0295
P99 ~ 0.0346

outlet SO2 half-window median difference
P95 ~ 1.4098 mg/Nm3
P99 ~ 2.1638 mg/Nm3
```

These values do **not** automatically choose `min_abs_delta_ph` or
`min_abs_delta_so2`; the engineering confidence multiplier above the noise floor
still requires review.

## Staircase counterfactual candidate set

`MFAC-STAIRCASE-CANDIDATES-5553E529-20260827.json` contains equal-dose Shadow
candidates only.  Equal dose isolates action-shape effects offline; it is not an
online slurry-volume obligation.

Counterfactual stages are extra supply flow above the event baseline.  The 725
validated dynamic pulse events provide this historical support:

```text
absolute running-flow P05/P95 = 57.8768 / 88.1057 m3/h  # audit only
sustained extra-flow P05/P95  = 51.0720 / 86.3891 m3/h  # support gate
action-duration P05/P95       = 320 / 730 s
proactive-advance support     = 0 s
```

Current low-flow proactive staircase candidates are outside that support:

```text
EXPLORATION_ONLY_OUT_OF_HISTORICAL_SUPPORT
eligible_for_step_calibration_evidence = false
```

Historical pulse data therefore cannot calibrate `max_step_up/down`.

## Controlled LOCAL_GAIN design

`MFAC-LOCAL-STEP-DESIGN-5553E529-20260827.json` keeps engineering/data-derived
review candidates separate from `reviewed_parameters`.

Phase 1 is intentionally limited to one candidate level:

```text
+2.0 m3/h manual identification stimulus
max Phase-1 stimulus = +2.0 m3/h
no automatic 2 -> 4 -> 6 escalation
```

It remains manual-only and not ready for execution until every required reviewed
field is approved.  A successful trial still requires a second human evidence
review before it can become one canonical LOCAL_GAIN event shared by SO2 and pH
bootstrap.

## Production permissions

Nothing in these audit artifacts changes production authority:

```text
LEARN = 0
Residual = 0
DCS write = off
```
