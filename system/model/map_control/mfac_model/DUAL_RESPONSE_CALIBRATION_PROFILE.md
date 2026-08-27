# Scheme 2 dual-response calibration profile

## Purpose

Scheme 2 now distinguishes three different engineering states that must not be
collapsed into one boolean:

```text
LOCAL_GAIN_READY
!= CALIBRATED
!= ACTIVATABLE
```

The physical response definitions remain:

```text
phi_so2 = delta_SO2 / delta_Q_actual < 0
phi_ph  = delta_pH  / delta_Q_actual > 0
```

SO2 is the only residual-producing control channel. pH remains an independently
learned response used for supervision and PASS/SCALE/BLOCK arbitration, not an
additive slurry controller.

## 1. LOCAL_GAIN_READY

A channel reaches `LOCAL_GAIN_READY` only after reviewed manual LOCAL_GAIN
trials pass the multi-trial cohort gate and the same cohort is accepted by the
dual bootstrap binding layer.

At this point the channel may have:

```text
phi_prior
phi_live0
valid_event_count
independent_days
evidence_event_ids
```

but this is still not full response calibration.

The channel may still lack:

```text
reviewed response delay/window
reviewed measurement window
reviewed sample-count rules
reviewed confidence
```

Therefore `build_calibration_profile_from_dual_bootstrap()` deliberately creates:

```text
SO2.status = LOCAL_GAIN_READY
pH.status  = LOCAL_GAIN_READY
```

not `CALIBRATED`.

## 2. CALIBRATED

SO2 and pH are independently calibrated. Valid channel statuses are:

```text
UNCONFIGURED
INSUFFICIENT_EVIDENCE
REVIEW_REQUIRED
LOCAL_GAIN_READY
CALIBRATED
```

This intentionally allows states such as:

```text
SO2 = CALIBRATED
pH  = INSUFFICIENT_EVIDENCE
```

or:

```text
SO2 = INSUFFICIENT_EVIDENCE
pH  = CALIBRATED
```

A channel may be marked `CALIBRATED` only when it has all of the following:

```text
physically valid phi_prior
physically valid phi_live0
positive reviewed event/day counts
bound LOCAL_GAIN event IDs
reviewed confidence
complete reviewed response config
```

The response config must contain:

```text
baseline_window_seconds
delay_onset_seconds
observation_seconds
measurement_window_seconds
max_sample_gap_seconds
target_change_tolerance
min_baseline_samples
min_response_samples
```

Merely providing all field names is not enough. The profile reuses the canonical
runtime validation classes:

```text
SO2 -> ProcessResponseConfig
pH  -> PHResponseConfig
```

so illegal values such as a measurement window longer than the observation
window fail during profile construction.

## 3. Same physical LOCAL_GAIN cohort

When both channels have local-gain evidence, the profile requires exact equality
of:

```text
evidence_event_ids
valid_event_count
independent_days
```

This is downstream defense in depth on top of `DualResponseBootstrapBundle`.

The dual bootstrap layer itself also requires the SO2 and pH trainers to accept
the same physical event set. Input-list order is not authority; both channel
trainers sort by physical timestamps, and their resulting event order must
match. This prevents different ISO timezone representations from creating false
cohort mismatch while still rejecting true SO2/pH sample divergence.

## 4. pH timing is not inferred from gain bootstrap

The current pH bootstrap trainer estimates local `phi_ph`, but does not itself
produce a reviewed pH delay profile. Therefore a profile created directly from
a dual bootstrap bundle explicitly records:

```text
ph_delay_profile_not_inferred_from_gain_bootstrap = true
```

and leaves pH timing/response config for a separate review step.

SO2 historical/bootstrap delay metadata may be retained as evidence, but
`LOCAL_GAIN_READY` still does not automatically elevate it to reviewed runtime
response configuration.

## 5. CALIBRATED still does not mean ACTIVATABLE

The current V1 calibration profile is permanently fail-closed:

```text
activation_status = NOT_ACTIVATABLE
learning_enabled = false
residual_control_enabled = false
dcs_write_enabled = false
```

and:

```text
can_enable_learning = false
can_enable_residual = false
can_enable_dcs = false
```

Even if:

```text
SO2 = CALIBRATED
pH  = CALIBRATED
```

`to_runtime_config()` still raises.

A later, separate activation-review artifact must decide whether a reviewed
calibration may initialize shadow/runtime state. Calibration evidence must never
self-authorize production control.

## 6. Current production state

Unchanged:

```text
LEARN = 0
Residual = 0
DCS write = off
```

Realtime pH remains excluded from Dynamic Qbase/Ca-S. It is used only in pH
response learning, supervision and residual arbitration.
