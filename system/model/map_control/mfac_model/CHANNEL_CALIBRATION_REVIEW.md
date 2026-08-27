# Scheme 2 Channel Calibration Review

## Purpose

`LOCAL_GAIN_READY` means the channel has a reviewed local sensitivity seed from the controlled LOCAL_GAIN cohort. It does **not** mean the response timing, observation windows, confidence or runtime permissions are calibrated.

The only supported promotion path is:

```text
LOCAL_GAIN_READY
  + observed response timing evidence
  + reviewed response config
  + reviewed confidence
  + explicit human reviewer/time
        ↓
CALIBRATED
```

SO2 and pH are reviewed independently. Reviewing one channel must not change the other channel state.

## Observed timing is not a configured window

The timing evidence semantics are:

```text
SCHEME2_OBSERVED_RESPONSE_TIMING_V1_PROCESS_TRACE
```

Both onset and response timing must come from an observed process trace. The following are forbidden as timing evidence:

- `response_start_time` merely because it equals a configured `delay_onset_seconds` boundary;
- `response_end_time` merely because it equals a configured observation-window boundary;
- large-pulse timing candidates copied directly into the low-step LOCAL_GAIN calibration;
- any timing record explicitly marked as using configured window boundaries.

The current historical large-pulse candidates (`SO2 onset P90 ~= 310 s`, `pH onset P90 ~= 190 s`) remain review candidates only. No controlled +2 m3/h local-step timing has yet been observed, so they cannot currently promote either channel to CALIBRATED.

## Same evidence context

Observed timing evidence must match the calibration profile's:

- `condition_snapshot_version`;
- `mfac_context_id`;
- channel (`SO2` or `PH`).

Its event IDs must be a subset of that channel's reviewed LOCAL_GAIN cohort. This keeps gain and timing evidence causally tied to the controlled identification trials instead of mixing unrelated historical actions.

## CALIBRATED construction seal

Calibration Profile V2 uses a package-private review seal. Ordinary code cannot create:

```python
DualResponseChannelCalibration(status="CALIBRATED", ...)
```

as a valid reviewed calibration. The object must be created through:

```python
approve_channel_calibration(...)
```

or restored from a V2 serialized profile carrying the complete review metadata.

Old V1 `LOCAL_GAIN_READY` profiles can be migrated to V2. Old V1 objects already marked `CALIBRATED` are rejected and require re-review because they predate the explicit channel-review seal.

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
7. explicitly reviewed confidence in `(0, 1]`.

The generated calibration metadata records reviewer, review time, timing-evidence ID/event IDs, timing semantics, response-config approval and confidence approval.

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
calibration_audits/
MFAC-CHANNEL-CALIBRATION-REVIEW-DESIGN-5553E529-20260827.json
```

At this checkpoint both SO2 and pH channel reviews remain `NOT_REVIEWED` because controlled local-step timing evidence does not yet exist.
