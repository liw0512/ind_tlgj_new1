# Scheme 2 dual-response activation readiness review

## Boundary

A reviewed calibration profile is evidence, not authority.

The activation-readiness layer therefore has one deliberately narrow job:

```text
DualResponseCalibrationProfile
+ external engineering review facts
-> NOT_READY
   or
   READY_FOR_HUMAN_ACTIVATION_REVIEW
```

It cannot return `ENABLED`, cannot build runtime config and has no approval API.

## Required technical evidence

The V1 readiness evaluator checks:

```text
condition snapshot binding
MFAC context binding
SO2 channel CALIBRATED
pH channel CALIBRATED
plant-contract match reviewed
runtime parameters reviewed
shadow validation reviewed
causal target application reviewed
runtime persistence/restore reviewed
rollback plan reviewed
```

These checks are intentionally outside the Calibration Profile because they
belong to system activation, not channel identification.

## Layered readiness

The evaluator exposes three non-authorizing evidence states:

```text
profile_load_evidence_ready
online_learning_evidence_ready
residual_control_evidence_ready
```

`profile_load_evidence_ready` requires both channels calibrated, correct
snapshot/context, reviewed plant-contract compatibility, reviewed runtime
parameters and reviewed persistence/restore behavior.

`online_learning_evidence_ready` additionally requires reviewed Shadow behavior,
a reviewed causal target-application path and a rollback plan.

`residual_control_evidence_ready` currently requires the same system checks and
a calibrated pH channel because nonzero SO2 residual must remain subject to pH
arbitration.

These are evidence/readiness flags only. They are not runtime permissions.

## Current causal blocker

The formal primary runtime currently calls the coordinator with:

```text
target_was_applied = false
dcs_applied_target_supply_flow = None
```

and explicitly emits:

```text
learn_enabled = false
residual_enabled = false
dcs_write_enabled = false
```

Its execution recorder returns:

```text
MFAC_FORMAL_DCS_ADAPTER_NOT_ENABLED
```

Therefore the current activation checklist correctly keeps:

```text
causal_target_application_reviewed = false
```

This is a real implementation blocker for production causal online learning,
not a historical documentation placeholder.

## Audit artifact

Current fail-closed checklist:

```text
calibration_audits/MFAC-DUAL-ACTIVATION-REVIEW-DESIGN-5553E529-20260827.json
```

All engineering review facts are false/unresolved and:

```text
status = NOT_READY
activation_status = NOT_APPROVED
learning_enabled = false
residual_control_enabled = false
dcs_write_enabled = false
```

## Important distinction

Even if all prerequisite facts eventually become true, this V1 layer can only
return:

```text
READY_FOR_HUMAN_ACTIVATION_REVIEW
```

The result still enforces:

```text
can_enable_learning = false
can_enable_residual = false
can_enable_dcs = false
```

and `to_runtime_config()` raises.

A future activation-approval mechanism would require a separate engineering and
safety review. It must not be inferred from this readiness result.

## Current production state

Unchanged:

```text
LEARN = 0
Residual = 0
DCS write = off
```
