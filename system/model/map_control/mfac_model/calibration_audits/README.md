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

## Current real-data audit

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
pH onset P90             ~ 190 s
pH peak P90              ~ 886 s
SO2 improvement onset P90~ 310 s
planner HOLD candidate   ~ 360 s
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

Production permissions remain unchanged:

```text
LEARN = 0
Residual = 0
DCS write = off
```
