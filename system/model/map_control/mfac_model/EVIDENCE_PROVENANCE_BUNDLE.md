# Scheme 2 Evidence Provenance Bundle

## Why this layer exists

Scheme 2 now has several independently reviewed evidence artifacts:

```text
human-approved LOCAL_GAIN cohort copies
raw manual local-step traces
SO2 / pH observed timing evidence
SO2 / pH confidence evidence
dual-response calibration profile
```

Each artifact may be valid by itself while still being unsafe to combine with an artifact from another cohort, condition snapshot or MFAC context. The provenance bundle prevents that class of mix-up.

It is **not** a new plant/configuration authority and it cannot activate runtime behavior.

## Content-addressed contract

`evidence_provenance_bundle.py` hashes each artifact using:

```text
SHA256(canonical JSON)
```

Canonical JSON means UTF-8 JSON with sorted keys, compact separators and non-finite numeric values forbidden.

The manifest stores content digests for:

```text
LOCAL_GAIN_COHORT_APPROVED_EVENTS
LOCAL_STEP_RAW_TRACE
OBSERVED_RESPONSE_TIMING
CHANNEL_CONFIDENCE_EVIDENCE
DUAL_RESPONSE_CALIBRATION_PROFILE
```

The cohort-approved event list is sorted by canonical event ID before hashing, so input list order is not treated as physical evidence.

## Binding rules

A bundle requires one:

```text
condition_snapshot_version
mfac_context_id
cohort_review_id
LOCAL_GAIN cohort event set
```

All artifacts must agree with those values.

For each channel:

```text
timing event IDs ⊆ approved LOCAL_GAIN cohort IDs
```

and every timing event must have a valid bound raw-trace artifact.

Confidence evidence must use the complete approved LOCAL_GAIN cohort and bind the same channel timing evidence ID plus the same human cohort review ID.

If a channel is already `CALIBRATED`, the profile metadata must bind the exact timing evidence ID, confidence evidence ID and cohort review ID included in the manifest.

## COMPLETE_REVIEW_CHAIN is not activation

The highest provenance status is:

```text
COMPLETE_REVIEW_CHAIN
```

It only means the evidence chain is internally complete and content-addressed according to the existing review contracts.

It explicitly does **not** mean:

```text
learning enabled
residual enabled
DCS write enabled
runtime activatable
```

`to_runtime_config()` always fails. A separate activation review remains mandatory.

## Verification

A stored manifest alone is insufficient. `verify_evidence_provenance_bundle()` receives the current source artifacts, rebuilds all canonical hashes and bindings, and compares the rebuilt manifest with the stored one.

Examples that must produce `MISMATCH` or fail reconstruction:

- one approved event's response value changed after the manifest was created;
- one raw-trace artifact is replaced by a different-context trace;
- a timing event has no raw trace;
- SO2 timing and confidence bind different evidence IDs;
- confidence comes from another cohort review;
- a calibrated profile points to different timing/confidence/cohort IDs.

## Current state

The implementation and regression contract exist, but there is no real complete bundle yet because the controlled +2 m3/h LOCAL_GAIN trial set has not been executed and reviewed.

Current production permission remains:

```text
LEARN = 0
Residual = 0
DCS write = off
```
