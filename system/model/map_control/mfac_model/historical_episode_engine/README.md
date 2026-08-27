# Historical Episode Engine

This directory is the MFAC-owned historical physical-response evidence engine.
It originated from the former second module, but the removed slurry-policy /
Q-learning controller is not present and is not authorized here.

## Purpose

```text
10 s production-equivalent history
-> cleaned actual slurry-flow event detection
-> STEP / PULSE / BOOST_STEP / COMPLEX shape description
-> SO2 / pH response profiling
-> fixed-window dose / peak / pH-excursion audit metrics
-> LOCAL_GAIN / DYNAMIC / SAFETY evidence routing
-> MFAC evidence adapters
```

Historical operator actions describe **how the plant responded**, not how the
algorithm should operate.

## Evidence roles

Roles are non-exclusive:

```text
LOCAL_GAIN
DYNAMIC
SAFETY
```

`LOCAL_GAIN` is the only historical route permitted to initialize local
`phi_so2` / `phi_ph`. It requires a reviewed small clean STEP envelope. The
repository has no default maximum local step/dose, so historical local-gain
eligibility fails closed until those limits are explicitly calibrated.

`DYNAMIC` preserves clean STEP/PULSE/BOOST_STEP events for delay/peak/settling
identification even when the action is too large for local-gain learning.

`SAFETY` preserves actions that push pH outside the configured operating/safe
envelope. Such events are useful risk evidence and must not be converted into
"good" MFAC demonstrations merely because outlet SO2 decreased.

## Dose metrics

Enriched episodes include audit fields such as:

```text
dose_3m_m3
dose_5m_m3
dose_10m_m3
dose_20m_m3
dose_30m_m3
flow_mean_* / flow_peak_*
ph_peak_response_horizon
ph_over_operating_max_minutes
ph_over_safe_max_minutes
```

When available, dose metrics use the same cleaned tower-flow basis used by the
event detector so event baseline/delta and dose integration do not drift because
of raw meter noise.

## Bootstrap boundary

Historical MFAC bootstrap is explicitly local-gain only:

```text
operator_action_imitation = false
```

Large manual pulses remain DYNAMIC/SAFETY evidence but do not seed either local
sensitivity channel.

Runtime control remains owned by the formal `mfac_model` runtime. The separate
`PendingDoseGuard` and `FlowTrajectoryPlanner` consume reviewed dynamic/safety
calibration in Shadow; this historical engine itself never publishes a DCS
target.
