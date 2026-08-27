# Scheme 2 Channel Calibration Review

## Purpose

`LOCAL_GAIN_READY` means the channel has a reviewed local sensitivity seed from the controlled LOCAL_GAIN cohort. It does **not** mean the response timing, observation windows, confidence or runtime permissions are calibrated.

The supported promotion path is now:

```text
LOCAL_GAIN_READY
  + manual local-step raw 10 s process traces
  + observed timing extraction
  + confidence evidence
  + reviewed response config
  + explicit human confidence
  + explicit human reviewer/time
        ↓
CALIBRATED
```

SO2 and pH are reviewed independently. Reviewing one channel must not change the other channel state.

## Raw trace is required

The configured SO2/pH response monitors summarize configured windows. Their `response_start_time` and `response_end_time` fields are therefore **not** proof of physical onset or response timing.

A controlled manual local-step trial must retain the raw SO2/pH trajectory through:

```text
LocalStepRawTraceRecorder
```

The recorder is manual-evidence-only. It has no execute API, no automatic control authority, no DCS write and no learning permission. It anchors the trace to the real `actual_flow_reached_time` and later binds the SO2 and pH traces to the same canonical LOCAL_GAIN event ID.

## Observed timing extraction

The observed timing extractor semantics are:

```text
SCHEME2_OBSERVED_TIMING_EXTRACTOR_V1_RAW_PROCESS_TRACE
```

and the resulting evidence semantics are:

```text
SCHEME2_OBSERVED_RESPONSE_TIMING_V1_PROCESS_TRACE
```

Extraction uses reviewed, explicit parameters only. No plant defaults exist for:

- smoothing window;
- onset absolute threshold;
- onset sustain samples;
- response fraction of observed directional extremum;
- response sustain samples;
- minimum response amplitude;
- observation horizon/sample requirements.

For each trace the extractor:

1. anchors to real `actual_flow_reached_time`;
2. estimates the pre-reach baseline from raw samples;
3. uses a reviewed trailing-median smoothing rule;
4. finds the first sustained directional crossing of the reviewed onset threshold;
5. finds the first sustained time reaching a reviewed fraction of the observed directional extremum;
6. aggregates multiple accepted events into P50/P90 onset/response timing.

For SO2 the physical direction is negative; for pH it is positive. The extractor converts both into a positive directional-response magnitude only for timing detection.

The following are forbidden as observed timing evidence:

- `response_start_time` merely because it equals a configured `delay_onset_seconds` boundary;
- `response_end_time` merely because it equals a configured observation-window boundary;
- large-pulse timing candidates copied directly into the low-step LOCAL_GAIN calibration;
- synthetic unit-test thresholds copied into site-reviewed extraction config;
- fewer than two accepted timing events being called calibrated timing.

The current historical large-pulse candidates (`SO2 onset P90 ~= 310 s`, `pH onset P90 ~= 190 s`) remain review candidates only. No controlled +2 m3/h local-step raw trace has yet been collected, so they cannot currently promote either channel to CALIBRATED.

## Confidence evidence is separate from confidence review

A channel confidence value can no longer be typed in without a bound evidence record.

The evidence semantics are:

```text
SCHEME2_CHANNEL_CONFIDENCE_EVIDENCE_V1_REVIEW_CANDIDATE
```

`ChannelConfidenceEvidence` combines already-reviewed facts:

- valid-trial count sufficiency;
- independent-day sufficiency;
- observed timing coverage of the LOCAL_GAIN cohort;
- channel-specific phi relative MAD compared with its reviewed limit;
- channel-specific phi maximum relative deviation compared with its reviewed limit.

It produces:

```text
conservative_confidence_candidate
```

This candidate is deliberately **not a probability** and is not the final runtime confidence. It is only an auditable review input. The human channel review must still explicitly choose the final confidence in `(0, 1]`.

The confidence evidence must bind the exact timing evidence ID/event IDs and the same LOCAL_GAIN cohort used by the channel profile.

## Same evidence context

Observed timing and confidence evidence must match the calibration profile's:

- `condition_snapshot_version`;
- `mfac_context_id`;
- channel (`SO2` or `PH`).

Timing event IDs must be a subset of the reviewed LOCAL_GAIN cohort. Confidence evidence must bind the same timing evidence and its cohort event set must match the channel's LOCAL_GAIN evidence set.

## CALIBRATED construction seal

Calibration Profile V3 uses a package-private review seal. Ordinary code cannot create:

```python
DualResponseChannelCalibration(status="CALIBRATED", ...)
```

as a valid reviewed calibration. The object must be created through:

```python
approve_channel_calibration(...)
```

or restored from a V3 serialized profile carrying complete timing-evidence and confidence-evidence review metadata.

Old V1/V2 `LOCAL_GAIN_READY` profiles can migrate to V3. Old V1/V2 objects already marked `CALIBRATED` are rejected and require re-review because they predate the V3 timing+confidence evidence seal.

## What the review validates

A successful channel review requires:

1. the channel is currently `LOCAL_GAIN_READY`;
2. explicit human approval and non-empty reviewer ID;
3. valid review timestamp;
4. observed timing evidence from at least two bound events;
5. complete ordered `DelayProfile`:
   - `onset_p50 <= onset_p90`;
   - `response_p50 <= response_p90`;
   - response quantiles do not precede onset quantiles;
6. a complete response config validated by the canonical monitor class:
   - SO2 -> `ProcessResponseConfig`;
   - pH -> `PHResponseConfig`;
7. a bound `ChannelConfidenceEvidence` for the same channel/context/timing/cohort;
8. explicitly human-reviewed final confidence in `(0, 1]`.

The generated calibration metadata records reviewer, review time, timing-evidence ID/event IDs, confidence-evidence ID/candidate, timing semantics, response-config approval and confidence approval.

## Permission boundary

Channel calibration grants no production permissions:

```text
learning_enabled         = false
residual_control_enabled = false
dcs_write_enabled        = false
activation_status        = NOT_ACTIVATABLE
```

Even when both channels are `CALIBRATED`, a separate DualResponse Activation Review remains mandatory. The current formal primary runtime still has no reviewed causal target-application/readback adapter, so production remains:

```text
LEARN = 0
Residual = 0
DCS write = off
```

## Current evidence status

See:

```text
calibration_audits/MFAC-CHANNEL-CALIBRATION-REVIEW-DESIGN-5553E529-20260827.json
calibration_audits/MFAC-OBSERVED-TIMING-EXTRACTION-DESIGN-5553E529-20260827.json
```

At this checkpoint both SO2 and pH channel reviews remain `NOT_REVIEWED`: controlled local-step raw traces do not yet exist, timing-extractor parameters are not reviewed, and no channel confidence evidence has been produced.
