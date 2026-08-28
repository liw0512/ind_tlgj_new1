# Scheme 2 Channel Calibration Review

## Purpose

`LOCAL_GAIN_READY` means a channel has a reviewed local sensitivity seed from the controlled LOCAL_GAIN cohort. It does **not** mean timing, observation windows, confidence or runtime permissions are calibrated.

The supported path is:

```text
manual +2 m3/h trial
  -> raw 10 s SO2/pH trace
  -> reviewed timing-extraction profile
  -> observed timing evidence
  -> quantitative LOCAL_GAIN cohort gate
  -> human cohort-bootstrap approval
  -> confidence evidence
  -> reviewed response config + explicit final confidence
  -> human channel calibration review
  -> CALIBRATED
```

SO2 and pH are reviewed independently. `CALIBRATED` is still not runtime activation permission.

## Raw trace is required

Configured SO2/pH response monitors summarize configured windows. Their `response_start_time` and `response_end_time` are **not** proof of physical onset or response timing.

`LocalStepRawTraceRecorder` retains the manual trial's raw SO2/pH trajectory around the real `actual_flow_reached_time`. It is evidence-only and has no execute API, DCS write, learning or normal-control authority.

An invalid raw-trace bundle is fail-closed: `so2_trace` and `ph_trace` are not exposed as usable timing inputs when the trial has context/snapshot drift, missing reach anchor, missing pre/post-reach data or an out-of-range reach timestamp.

## Reviewed timing extraction is a separate approval

Low-level extraction semantics:

```text
SCHEME2_OBSERVED_TIMING_EXTRACTOR_V1_RAW_PROCESS_TRACE
```

Calibration-eligible extraction profile semantics:

```text
SCHEME2_OBSERVED_TIMING_EXTRACTION_DESIGN_V2_REVIEW_SEALED
```

Observed timing evidence semantics:

```text
SCHEME2_OBSERVED_RESPONSE_TIMING_V1_PROCESS_TRACE
```

The extractor has no site defaults for smoothing, onset threshold, sustain samples, response fraction, minimum amplitude or observation horizon. Candidate values never participate in calibration evidence.

Only `ObservedTimingExtractionProfile(status="REVIEWED_MANUAL_ONLY")` with complete reviewed parameters plus reviewer ID/time may create calibration-eligible timing evidence. That evidence carries:

```text
timing_extraction_profile_reviewed = true
timing_extraction_profile_id
timing_extraction_reviewer_id
timing_extraction_review_time
reviewed_extraction_parameters
candidate_parameters_used_for_extraction = false
calibration_review_eligible = true
```

A low-level extractor result without this provenance seal is useful for engineering analysis but cannot enter channel calibration.

The current historical large-pulse timing candidates (`SO2 onset P90 ~= 310 s`, `pH onset P90 ~= 190 s`) remain audit candidates only. They are not observed timing for the unexecuted controlled +2 m3/h LOCAL_GAIN cohort.

## Confidence evidence requires human cohort approval

Confidence evidence semantics:

```text
SCHEME2_CHANNEL_CONFIDENCE_EVIDENCE_V1_REVIEW_CANDIDATE
```

An `ADEQUATE_FOR_BOOTSTRAP_REVIEW` quantitative cohort is not sufficient by itself. `ChannelConfidenceEvidence` additionally requires the event copies produced by the explicit human cohort-bootstrap approval. Those copies must carry one coherent:

```text
cohort_review_id
cohort_review_reviewer_id
cohort_review_time
cohort_bootstrap_review_approved = true
offline_bootstrap_evidence_allowed = true
learning_eligible = true
automatic_online_adaptation_allowed = false
```

Confidence evidence also requires timing evidence created under the reviewed extraction-profile seal above.

The candidate summarizes:

- valid-trial count sufficiency;
- independent-day sufficiency;
- timing coverage of the approved LOCAL_GAIN cohort;
- channel-specific phi relative MAD against its reviewed limit;
- channel-specific maximum relative deviation against its reviewed limit.

The final candidate is the conservative minimum of those normalized audit scores. It is **not a probability** and is not automatically copied into runtime confidence. A human channel review still explicitly chooses the final confidence in `(0, 1]`.

## Same physical evidence chain

For one channel calibration, all evidence must agree on:

- `condition_snapshot_version`;
- `mfac_context_id`;
- channel (`SO2` or `PH`);
- LOCAL_GAIN cohort event set;
- timing evidence ID/event subset;
- human cohort-bootstrap review;
- reviewed timing-extraction provenance.

SO2 and pH may be reviewed at different times, but when both have local gain they still share the same underlying LOCAL_GAIN physical cohort.

## CALIBRATED construction seal

Calibration Profile V3 cannot be validly produced by directly writing:

```python
DualResponseChannelCalibration(status="CALIBRATED", ...)
```

The supported path remains:

```python
approve_channel_calibration(...)
```

The review now rejects timing evidence unless its reviewed extraction provenance is complete, and rejects confidence evidence unless it binds the same timing/cohort and records human cohort-bootstrap approval.

A successful channel review requires:

1. channel status is `LOCAL_GAIN_READY`;
2. reviewed timing-extraction provenance;
3. at least two accepted observed timing events;
4. ordered `DelayProfile`;
5. complete canonical `ProcessResponseConfig` or `PHResponseConfig`;
6. human cohort-approved LOCAL_GAIN evidence behind the confidence record;
7. bound self-consistent `ChannelConfidenceEvidence`;
8. explicit final confidence in `(0, 1]`;
9. explicit channel reviewer and review time.

Old local-gain-only profiles may migrate. Old objects already marked `CALIBRATED` without the current evidence seals must be re-reviewed.

## Permission boundary

None of these evidence or calibration layers may enable production permissions:

```text
learning_enabled         = false
residual_control_enabled = false
dcs_write_enabled        = false
activation_status        = NOT_ACTIVATABLE
```

Even two `CALIBRATED` channels only become inputs to the separate DualResponse Activation Review. The formal primary runtime still lacks a reviewed target-applied/readback causal adapter, so current production state remains:

```text
LEARN = 0
Residual = 0
DCS write = off
```

## Current evidence status

See:

```text
calibration_audits/MFAC-OBSERVED-TIMING-EXTRACTION-DESIGN-5553E529-20260827.json
calibration_audits/MFAC-CHANNEL-CALIBRATION-REVIEW-DESIGN-5553E529-20260827.json
```

At this checkpoint no controlled +2 m3/h raw trace exists, timing-extraction reviewer/time are still unset, no human-approved local-step cohort exists, and both channel calibration states remain `NOT_REVIEWED`.
