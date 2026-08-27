# Scheme 2 historical pulse evidence and staircase Shadow contract

## Scope

This note records the engineering conclusions obtained from the uploaded
`new_data_train_10s.csv` historical dataset and the resulting MFAC architecture
change. Historical operator behavior is treated as **process identification and
safety evidence**, not as an action policy to imitate.

Dataset audit used during this design review:

```text
rows:       298,875
cadence:    predominantly 10 s
time range: 2026-06-14 00:00:00 -> 2026-07-22 00:00:00
```

Relevant site signals are already owned by `PLANT_CONFIG`:

```text
yyq_SO2       inlet/raw flue-gas SO2
jyq_SO2       outlet/clean flue-gas SO2
xstjy_PH      absorber slurry pH
xstshsjy_LL   actual limestone-slurry supply flow
xstshsjy_MD   slurry density
xstgjb_APL    slurry pump 2A frequency (historical operation indicator)
```

## Historical observation

The historical operation is dominated by short, high-flow pump pulses rather
than clean small sustained steps. Typical pulse-like actions quickly drive the
actual slurry flow into the high-flow region, stay active for several minutes,
and then return close to baseline. The process response is delayed: much of the
slurry has already been delivered before the full pH and outlet-SO2 response is
observable.

The dataset also contains many post-action pH excursions beyond the configured
normal operating envelope `[6.0, 6.4]`, with a smaller but material subset above
the configured safe maximum `6.8`. These events are valuable safety/dynamic
evidence but are not acceptable local-gain demonstrations.

Matched historical comparisons also show cases where a larger delivered slurry
volume mainly produces a higher pH peak without robust additional outlet-SO2
benefit. Therefore the online controller must have **no dose debt**: it must not
continue adding slurry merely to reproduce a historical total volume after SO2
has already recovered.

The exact event counts, medians and response-delay estimates from this dataset
remain **audit candidates**, not frozen plant parameters. They must be reproduced
by the formal calibration pipeline before any value is copied into runtime
configuration.

## Evidence roles

Historical episodes are now non-exclusively routed into:

```text
LOCAL_GAIN
DYNAMIC
SAFETY
```

### LOCAL_GAIN

Only reviewed, small, clean sustained STEP events may seed:

```text
phi_so2 = delta SO2 / delta Q_actual  < 0
phi_ph  = delta pH  / delta Q_actual  > 0
```

The repository intentionally provides **no default** maximum local step or dose.
Without explicit reviewed limits, historical LOCAL_GAIN eligibility fails closed.

A pH operating-envelope excursion blocks LOCAL_GAIN. A pH safe-envelope
excursion is additionally rejected by the bootstrap adapters as defense in
depth.

### DYNAMIC

Clean STEP/PULSE/BOOST_STEP events can remain useful for dynamic identification,
including response onset/peak/settling characterization. They do not become
local MFAC gain samples merely because their SO2 direction is physically useful.

### SAFETY

Events that drive pH outside the configured operating or safe envelope are kept
as safety evidence. Fixed-window audit metrics include:

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

Dose metrics use the same cleaned actual-flow basis as historical event
segmentation when that signal is available.

## Offline bootstrap boundary

SO2 and pH offline bootstrap are now symmetric in evidence policy:

```text
historical LOCAL_GAIN only
operator action imitation = false
```

Large manual pulses must not initialize either local sensitivity channel.

The current historical dataset is rich in DYNAMIC/SAFETY evidence but sparse in
clean small-step LOCAL_GAIN evidence. Therefore it is not sufficient, by itself,
to justify a production `phi_so2` / `phi_ph` activation.

## Delayed pH memory

Current pH arbitration predicts the immediate local effect of the current SO2
residual candidate. That is insufficient when several recent slurry increments
have delayed pH effects that are not yet visible in the current measurement.

`PendingDoseGuard` therefore tracks recent **actual-flow increments**, not slurry
volume times `phi_ph`.

```text
recent actual delta-Q events
-> calibrated onset/peak response fraction
-> pending equivalent delta-Q
-> phi_ph * pending equivalent delta-Q
-> predicted pH after pending effect
```

Recent delivered volume is retained for audit only:

```text
AUDIT_ONLY_NOT_CONTROL_DEBT
```

No controller is required to "repay" or complete a historical dose.

The V1 response fraction is deliberately simple and Shadow-only. Required
parameters have no production defaults:

```text
flow_change_deadband
response_onset_seconds
response_peak_seconds
response_memory_seconds
max_sample_gap_seconds
min_confidence (optional)
```

## Flow trajectory planner

`FlowTrajectoryPlanner` shapes a raw MFAC demand into a non-authoritative
staircase candidate:

```text
raw MFAC demand
-> minimum HOLD time
-> bounded single step
-> pending-pH memory gate
-> next Shadow target candidate
```

It explicitly prevents the unsafe pattern:

```text
10 s: +step
10 s: +step
10 s: +step
...
```

before delayed process response can be observed.

Required planner calibration also has no production defaults:

```text
max_step_up
max_step_down
min_hold_seconds
demand_deadband (optional)
```

## Target ownership and safety

The trajectory layer is currently advisory only. Formal control remains:

```text
condition_model
-> Dynamic Qbase
-> SO2 MFAC residual
-> pH local arbitration
-> existing algorithm_target_supply_flow
```

The trajectory Shadow is appended after the existing coordinator cycle:

```text
algorithm_target_supply_flow          # authoritative current Shadow target
pending_dose_guard                     # advisory metadata
trajectory_plan                        # advisory metadata
```

It must satisfy:

```text
algorithm_target_replaced_by_trajectory_planner = false
trajectory_planner_dcs_write_enabled = false
dose_debt_semantics = false
```

Production permissions remain:

```text
LEARN = 0
Residual = 0
DCS write = off
```

## Runtime V4

`SCHEME2_MFAC_RUNTIME_CONFIG_V4_PENDING_TRAJECTORY` requires explicit calibration
for both the original dual-response MFAC and the new dynamic-memory/trajectory
sections before a coordinator can be built.

Default repository state remains:

```text
enabled = false
status = DISABLED_UNCALIBRATED
pending_dose = {}
trajectory_planner = {}
```

A configured formal P4PC runtime must be a
`Scheme2TrajectoryShadowCoordinator`; a legacy dual-response-only coordinator is
not accepted by the formal P4PC injection boundary.

## Next calibration work

Before any production activation:

1. reproduce historical event routing on the full CSV;
2. review candidate LOCAL_GAIN magnitude/dose limits;
3. estimate independent pH and SO2 dynamic onset/peak/memory distributions;
4. calibrate conservative PendingDoseGuard parameters;
5. evaluate several staircase step/hold profiles in counterfactual Shadow;
6. compare human pulse vs adaptive staircase using SO2 peak/exceedance, pH peak
   and exceedance duration, max flow, and delivered slurry volume;
7. collect real small-step identification evidence if historical LOCAL_GAIN
   remains insufficient;
8. only then review LEARN, non-zero Residual and finally DCS write as separate
   gates.
