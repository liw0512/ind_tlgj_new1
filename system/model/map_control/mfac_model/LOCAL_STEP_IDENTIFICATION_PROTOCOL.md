# Scheme 2 controlled LOCAL_GAIN identification protocol

## Purpose

Historical operator slurry actions are dominated by large pulse-like actions and
do not support the low-flow staircase shape intended by Scheme 2.  They are
therefore DYNAMIC/SAFETY evidence, not action demonstrations.

The missing evidence is local plant sensitivity:

```text
phi_so2 = delta SO2 / delta Q_actual < 0
phi_ph  = delta pH  / delta Q_actual > 0
```

This document defines how that evidence may be collected without turning the
identification subsystem into an automatic controller.

## Safety authority

The identification subsystem is manual-only:

```text
automatic_execution_allowed = false
dcs_write_enabled            = false
normal_algorithm_target_replaced = false
```

A proposal is not an instruction.  An abort is a recommendation to the human
operator/test supervisor, not an automatic DCS command.

Production control permissions remain:

```text
LEARN = 0
Residual = 0
DCS write = off
```

## Candidate values versus reviewed values

The audit artifact

```text
calibration_audits/MFAC-LOCAL-STEP-DESIGN-5553E529-20260827.json
```

separates:

```text
review_candidate_parameters
reviewed_parameters
```

Candidate values never populate reviewed values automatically.
`LocalStepIdentificationDesignProfile` refuses to build manual trial configs
until all required reviewed fields are present and the design status is
explicitly `REVIEWED_MANUAL_ONLY`.

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

## Phase-1 trial matrix

Phase-1 contains one candidate level only:

```text
PHASE1_STEP_2
step = +2.0 m3/h
max step = +2.0 m3/h
review status = REVIEW_REQUIRED
```

The following remain unset until reviewed:

```text
required_valid_trials
required_independent_days
```

Therefore the current matrix is not ready for a manual session.

There is no automatic progression such as:

```text
2 -> 4 -> 6 m3/h
```

A later level can only be considered after Phase-1 has accumulated the reviewed
number of valid trials and independent days, followed by a new human design
review.

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

The plan records:

```text
pre-trial actual flow
pre-trial Qbase
pre-trial pH
pre-trial outlet SO2
approved manual test target
manual return target
condition snapshot
MFAC context
reviewer identity/time
```

Creating the plan has no actuator side effect.

## Execution and causal anchor

If the test is manually executed, the existing execution/response stack remains
the causal source of truth:

```text
manual reviewed test target
-> DCS applied target/readback
-> actual supply-flow feedback
-> actual_flow_reached_time
-> independent SO2 response monitor
-> independent pH response monitor
```

The identification protocol does not replace these monitors.

## In-trial abort recommendation

`LocalStepTrialSafetyMonitor` recommends manual abort/return-to-baseline when any
of the following occurs:

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

A manually executed test does not become learning evidence merely because a
response is visible. `evaluate_local_step_trial()` requires:

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

The real-data audit now indicates that a 600-second SO2-only early observation
is not sufficient for dual-channel closure because pH peak P90 is about 886 s.
The current review candidate is therefore:

```text
minimum SO2 observation = 600 s
minimum pH observation  = 900 s
```

These are still review candidates, not reviewed site parameters.

## Second human review: evidence promotion

A successful dual-response trial has status:

```text
LOCAL_GAIN_EVIDENCE_CANDIDATE
```

but still:

```text
learning_permission = false
```

Only a second explicit evidence review may call
`promote_local_step_evidence()`.  The promoted canonical event contains both
channels from the same physical action:

```text
delta_q_actual
delta_so2
phi_so2_event
delta_ph
phi_ph_event
```

and is marked:

```text
action_source = MANUAL_LOCAL_STEP_IDENTIFICATION_REVIEWED
evidence_role = LOCAL_GAIN
operator_action_imitation = false
automatic_online_adaptation_allowed = false
offline_bootstrap_evidence_allowed = true
```

SO2 and pH offline bootstrap therefore consume the same reviewed physical trial,
not two unrelated actions.

## Return to baseline

The manual return from the test plateau to the pre-trial target is a recovery
action, not a second LOCAL_GAIN demonstration. Its delayed response overlaps the
first test and must not be automatically learned.

After return-to-baseline, a new trial may be proposed only after the complete
proposal gate passes again, including quiet interval and fresh stable baselines.
There is no dose debt and no requirement to perform another action merely to
balance cumulative slurry volume.

## Historical timing context

The current real-data audit remains useful for conservative observation design:

```text
pH onset P90              ~190 s
pH peak P90               ~886 s
SO2 improvement onset P90 ~310 s
pH memory lower bound     >=1800 s
```

These values constrain observation/recovery review. They do not determine local
`phi` or the runtime staircase step cap.

## Current readiness

```text
historical DYNAMIC evidence   rich
historical SAFETY evidence    rich
historical LOCAL_GAIN         insufficient
Phase-1 step                  +2.0 m3/h REVIEW_CANDIDATE
required trial count          unresolved
required independent days     unresolved
several safety/effect limits  unresolved
manual session ready          false
runtime activation            false
```

The next engineering decision is to review the remaining null fields in the
local-step design artifact. Only after that review can a supervised Phase-1
manual session be considered.
