# Scheme 2 controlled LOCAL_GAIN identification protocol

## Purpose

Historical operator slurry actions are dominated by large pulse-like actions and
do not support the low-flow staircase shape intended by Scheme 2. They are
therefore DYNAMIC/SAFETY evidence, not action demonstrations.

The missing evidence is local plant sensitivity:

```text
phi_so2 = delta SO2 / delta Q_actual < 0
phi_ph  = delta pH  / delta Q_actual > 0
```

This document defines how that evidence may be collected without turning the
identification subsystem into an automatic controller.

## Safety authority

The entire identification subsystem is manual-only:

```text
automatic_execution_allowed = false
dcs_write_enabled            = false
normal_algorithm_target_replaced = false
```

A proposal is not an instruction. An abort is a recommendation to the human
operator/test supervisor, not an automatic DCS command.

Production control permissions remain:

```text
LEARN = 0
Residual = 0
DCS write = off
```

## Three reviewed gates are required before a session

A supervised Phase-1 session is not ready merely because a `+2.0 m3/h` test
magnitude has been discussed. The unified readiness contract requires all three:

```text
Identification Design
+ Observation Profile
+ Trial Matrix Level
```

### Identification Design

Owns proposal/safety/evidence limits such as:

```text
step magnitude
pH identification margins
quiet interval
candidate interval
actual-flow/Qbase baseline stability
Qbase/inlet/outlet-SO2 stability
effect minimums
minimum evidence observation durations
```

Artifact:

```text
calibration_audits/MFAC-LOCAL-STEP-DESIGN-5553E529-20260827.json
```

### Observation Profile

Owns the independent manual-trial monitor configuration:

```text
SupplyFlowTrackingConfig
ProcessResponseConfig   # SO2
PHResponseConfig        # pH
```

Artifact:

```text
calibration_audits/MFAC-LOCAL-STEP-OBSERVATION-DESIGN-5553E529-20260827.json
```

This is intentionally separate from the production runtime. A manual
identification test may **not** silently borrow whichever tracking/response
windows happen to be configured in the formal MFAC runtime.

The observation profile remains incomplete. Current review candidates include:

```text
tracking reach_tolerance       0.5 m3/h
tracking execution timeout     300 s
monitor max_sample_gap         30 s
response baseline window       300 s
SO2 delay-onset audit candidate 310 s
pH delay-onset audit candidate  190 s
```

The last two come from large-pulse timing evidence and therefore remain only
review candidates; low-step timing has not been observed directly.

Still unresolved include:

```text
tracking target_change_deadband
tracking required_sustain_seconds
SO2/pH observation windows
SO2/pH measurement windows
SO2/pH target-change tolerance
SO2/pH minimum sample counts
```

`LocalStepObservationProfile` cannot construct monitors while any reviewed field
is missing.

### Trial Matrix

Phase-1 contains one candidate level only:

```text
PHASE1_STEP_2
step = +2.0 m3/h
max step = +2.0 m3/h
review status = REVIEW_REQUIRED
```

Still unresolved:

```text
required_valid_trials
required_independent_days
```

There is no automatic progression:

```text
2 -> 4 -> 6 m3/h
```

A later level requires complete Phase-1 evidence and a new human design review.

## Unified readiness and cross-profile consistency

`evaluate_local_step_session_readiness()` is the one read-only readiness gate.
Even if all three artifacts are individually reviewed, the session is rejected
when their overlapping safety/check semantics conflict.

Current consistency rules include:

```text
trial max_sample_gap
== tracking max_sample_gap
== SO2 response max_sample_gap
== pH response max_sample_gap

tracking target_change_deadband
< reviewed identification step

tracking reach_tolerance
<= trial max_abs_step_error

SO2 delay_onset + observation
>= trial minimum SO2 observation

pH delay_onset + observation
>= trial minimum pH observation
```

This removes last-writer-wins behavior between identification and observation
configuration.

Even `READY_FOR_SUPERVISED_MANUAL_SESSION` still means only:

> all reviewed prerequisites are internally consistent.

It does **not** execute a command. A separate first human approval is still
required for every proposal.

## Candidate values versus reviewed values

Audit artifacts separate:

```text
review_candidate_parameters
reviewed_parameters
```

Candidate values never populate reviewed values automatically.

The inherited first-phase engineering candidate remains:

```text
step_up = +2.0 m3/h
max_step_up = +2.0 m3/h
```

This is a manual identification stimulus only. It is **not**:

- the runtime FlowTrajectoryPlanner `max_step_up`;
- a 10-second recurrent increment;
- a Qbase cycle limiter;
- permission to execute a test.

## Proposal gate

`LocalStepIdentificationGate` may create a `REVIEW_CANDIDATE` only when all
required evidence is present and stable:

```text
explicit supervised-identification request
data quality valid
FAST inactive
equipment unchanged
condition snapshot/context available
actual flow close to Qbase
actual-flow baseline stable
Qbase absolute and relative drift stable
pH inside reviewed identification sub-band
pH baseline stable
outlet SO2 retains reviewed safety headroom
inlet/outlet SO2 baseline stable
reviewed quiet interval elapsed
reviewed candidate interval elapsed
proposed test target remains inside plant flow bounds
```

The pH operating/safe envelope and plant flow bounds remain owned by
`PLANT_CONFIG`; the identification design only supplies margins/tolerances.

## First human review

A proposal becomes a `LocalStepTrialPlan` only after explicit human approval:

```text
approve_local_step_proposal(... human_approved=True ...)
```

The plan records pre-trial process state, the approved manual target, the manual
return target, condition/context binding and reviewer/time. Creating the plan has
no actuator side effect.

## Execution and causal anchor

If the test is manually executed, the reviewed observation profile instantiates
the existing monitor classes:

```text
manual reviewed test target
-> DCS applied target/readback
-> SupplyFlowTrackingMonitor
-> actual_flow_reached_time
-> ProcessResponseMonitor      # SO2
-> PHResponseMonitor           # pH
```

The identification subsystem does not implement a second tracking/response
algorithm and does not use historical actual flow as an algorithm target.

## In-trial abort recommendation

`LocalStepTrialSafetyMonitor` recommends manual abort/return-to-baseline for:

```text
data quality invalid
sample gap
FAST becomes active
equipment change
condition/context change
unexpected target change
pH leaves the plant operating envelope
outlet SO2 reaches reviewed abort headroom threshold
Qbase drift exceeds reviewed limit
inlet SO2 change exceeds reviewed limit
```

The monitor has no `execute()` API and cannot write DCS.

## Dual-response success gate

`evaluate_local_step_trial()` requires both responses from the same real
tracking event and enforces:

```text
SO2 response == COMPLETED
pH response  == COMPLETED
same actual-flow tracking event
positive actual delta-Q
actual delta-Q matches approved step within reviewed tolerance
same condition snapshot/context
no FAST overlap
no target/context change
data quality valid
Qbase stable
inlet SO2 stable
reviewed SO2 observation duration reached
reviewed pH observation duration reached
pH stayed inside operating envelope
abs(delta SO2) above reviewed effect threshold
abs(delta pH) above reviewed effect threshold
phi_so2_event < 0
phi_ph_event  > 0
```

Current review candidates are:

```text
minimum SO2 observation = 600 s
minimum pH observation  = 900 s
```

They remain unreviewed.

## Second human review: evidence promotion

A successful trial becomes only:

```text
LOCAL_GAIN_EVIDENCE_CANDIDATE
learning_permission = false
```

Only a second explicit evidence review may call
`promote_local_step_evidence()`. The resulting canonical event contains both
channels from the same physical action:

```text
delta_q_actual
delta_so2 / phi_so2_event
delta_ph  / phi_ph_event
```

and is marked:

```text
action_source = MANUAL_LOCAL_STEP_IDENTIFICATION_REVIEWED
evidence_role = LOCAL_GAIN
operator_action_imitation = false
automatic_online_adaptation_allowed = false
offline_bootstrap_evidence_allowed = true
```

## Return to baseline and pH recovery

The manual return from the test plateau is a recovery action, not a second
LOCAL_GAIN event. It must not be automatically learned.

These clocks are different:

```text
PendingDoseGuard onset/peak
!= Planner HOLD
!= pulse recovery / identification quiet
!= candidate interval
```

`phi_ph` is a step sensitivity. A sustained positive delta-Q does not decay
because a generic memory timer expires. For a pulse, pH recovery is generated by
the later negative return-flow step progressively cancelling the earlier
positive step.

PendingDoseGuard therefore models only the future response not yet realized
between onset and peak.

Separate 918-event pulse-recovery audit:

```text
pulse end -> pH peak
P50 ~ 220 s   P90 ~ 393 s   P95 ~ 450 s

peak -> half-decay
P50 ~ 590 s   P90 ~ 970 s   P95 ~ 1140 s

pulse end -> half-decay
P50 ~ 830 s   P90 ~ 1280 s  P95 ~ 1440 s

pulse end -> recovery band
P50 ~ 1030 s  P90 ~ 2020 s  P95 ~ 2450 s
```

Recovery band:

```text
pH <= pre-pulse baseline + 0.05
sustained for approximately 120 s
```

Current quiet-time candidate:

```text
min_quiet_seconds = 2700 s  # REVIEW_CANDIDATE
```

A new test still requires all fresh baseline gates after this interval.

Separate session-spacing candidate:

```text
min_candidate_interval_seconds = 3600 s
```

Neither is a PendingDoseGuard parameter.

## Historical evidence gaps that remain explicit

The CSV can support some stability candidates, but it cannot safely fill all
parameters.

For example, a dedicated 300-second baseline audit found only **3** independent
windows satisfying identification-like stable-flow/pH/outlet-SO2 conditions.
Therefore:

```text
max_abs_inlet_so2_change = null
status = INSUFFICIENT_5MIN_STABLE_BASELINE_EVIDENCE
```

The CSV also lacks the historical runtime `outlet_so2_target`, so strict
historical Qbase proximity cannot be reconstructed:

```text
max_abs_actual_minus_qbase = null
```

Noise-floor evidence exists for minimum SO2/pH effects, but the multiplier above
that floor remains an engineering review decision.

## Current readiness

```text
historical DYNAMIC evidence       rich
historical SAFETY evidence        rich
historical LOCAL_GAIN             insufficient
Identification Design             incomplete
Observation Profile               incomplete
Phase-1 Trial Matrix              incomplete
cross-profile consistency         cannot pass until above are reviewed
manual session ready              false
runtime activation                false
```

No amount of editing an audit JSON changes production authority:

```text
LEARN = 0
Residual = 0
DCS write = off
```
