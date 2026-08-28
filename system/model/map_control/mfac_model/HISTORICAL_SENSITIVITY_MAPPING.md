# Scheme 2 historical sensitivity learning and online mapping

## 1. Corrected main route

The primary offline route is historical, condition-aware, model-based learning:

```text
HistoricalEpisodeEngine
        ↓
valid DYNAMIC, non-SAFETY episodes
        ↓
model-based marginal response training
        ↓
phi_so2(context, work point)
phi_ph(context, work point)
        ↓
reviewed HistoricalSensitivityMap
        ↓
online hierarchical mapping
        ↓
per-channel online recursive adaptation
```

Supervised `+2.0 m3/h` local-step trials remain useful for validation, low-confidence contexts and future drift checks, but they are **not** a prerequisite for historical bootstrap.

## 2. Why large historical pulses are still useful

Historical operator data is endogenous: bad outlet SO2 / falling pH often causes the operator to increase slurry. Therefore contemporaneous correlation, or direct `delta_y / delta_q` on large pulses, can have the wrong sign.

Large STEP/PULSE events are therefore not promoted to direct LOCAL_GAIN. Instead they enter a response model that:

- uses delayed response rather than contemporaneous correlation;
- adjusts for work-point and nuisance variables;
- fits SO2 and pH separately;
- estimates the marginal derivative with respect to **actual** delta-Q;
- rejects a context/grid when the bootstrap physical direction is unstable.

This route is named `MODEL_BASED_LOCAL_GAIN`.

## 3. Direct versus model-based gain evidence

```text
DIRECT_LOCAL_GAIN
  small, clean actual-flow step
  → event phi may be measured directly

MODEL_BASED_LOCAL_GAIN
  larger historical STEP/PULSE/BOOST_STEP
  → no direct large-pulse delta-y/delta-q authority
  → robust response model
  → marginal derivative candidate

DYNAMIC
  delay / response-shape evidence

SAFETY
  pH/dose/overshoot evidence
```

The evidence roles are complementary. A historical pulse may contribute to DYNAMIC and MODEL_BASED_LOCAL_GAIN modeling while still being forbidden as a direct local-gain sample.

## 4. Online mapping is not exact-state lookup

The online runtime must never require an exact historical row such as:

```text
inlet SO2 = 1763
pH = 6.17
Qbase = 23.4
outlet SO2 = 11.2
```

A historical sensitivity surface is continuous inside its reviewed support domain:

```text
phi_so2 = f_so2(work point)
phi_ph  = f_ph(work point)
```

The current V1 surface supports normalized continuous features such as Qbase, inlet SO2, pH, gas flow and outlet SO2. A profile may use a subset of these features.

## 5. Hierarchical resolution order

`HistoricalSensitivityMap.resolve()` uses:

```text
1. EXACT_CONTEXT
2. EXACT_GRID
3. NEIGHBOR_INTERPOLATED
4. POOLED_FALLBACK
5. MFAC prior unavailable
```

This means “not seen exactly before” does not automatically remove the recommendation.

### 5.1 Exact context

If the current `mfac_context_id` has a reviewed model, evaluate that continuous surface at the current work point.

### 5.2 Exact grid

If a condition was re-merged or the exact context profile is absent but the underlying fixed-grid cell has reviewed evidence, use the grid profile with an explicit mapping source.

### 5.3 Neighbor interpolation

If the current grid is unsupported, interpolate the nearest supported grid profiles and reduce confidence according to grid distance.

A grid that has many historical events but fails the physical-sign/stability review is treated as **unsupported**, not as an exact-grid authority.

### 5.4 Pooled fallback

A separately reviewed plant-pooled model may be used as a low-confidence prior when no local/neighbor model is acceptable.

### 5.5 No credible historical prior

Only the MFAC prior is unavailable. Dynamic Qbase remains independent:

```text
Qbase valid
+ no credible historical phi
→ algorithm target still comes from Qbase
→ historical MFAC correction confidence = 0 / unavailable
```

The system must not suppress the complete supply-flow recommendation just because an exact historical MFAC state is missing.

## 6. Work-point extrapolation

Inside reviewed support, the continuous surface is evaluated normally.

Outside support but within the reviewed extrapolation distance:

```text
phi evaluated
confidence reduced
mapping marked WORKPOINT_EXTRAPOLATED
```

Beyond the reviewed extrapolation distance, that profile is not used and the resolver moves down the hierarchy.

This is intentionally different from both unsafe unrestricted extrapolation and brittle exact-match rejection.

## 7. Historical prior versus online learning ownership

Historical mapping is a prior, not a value that overwrites online learning every 10 seconds.

Ownership is independent by channel:

```text
SO2 valid_event_count == 0
→ historical phi_so2 may refresh with current work point

SO2 valid_event_count > 0
→ online phi_so2 owns SO2 channel

pH ph_valid_event_count == 0
→ historical phi_ph may refresh with current work point

pH ph_valid_event_count > 0
→ online phi_ph owns pH channel
```

Therefore a valid state may legitimately be:

```text
SO2: online learned
pH : historical mapped
```

until pH obtains accepted online evidence.

## 8. Current historical CSV audit

The current CSV cannot honestly be assigned old `condition_snapshot_version` / final condition labels because those artifacts are not present in the raw file. It can, however, be grouped by the current plant fixed-grid coordinate derived from the configured `yyq_SO2` axis.

The first audit is therefore explicitly:

```text
UNBOUND_GRID_LEVEL
AUDIT_ONLY
REVIEW_REQUIRED
NOT_ACTIVATABLE
```

Exploratory model-based results show why hierarchical mapping is needed:

- P13-S1, P14-S1, P15-S1: both SO2 and pH marginal directions were stable enough to become review candidates under the exploratory rule;
- P12-S1: many events existed, but fitted SO2 and pH directions were strongly non-physical and the grid was rejected;
- several surrounding grids had insufficient sign stability;
- the pooled model had stable physical directions and is a candidate only for a deliberately lower-confidence fallback.

See `calibration_audits/MFAC-HISTORICAL-MODEL-BASED-GAIN-AUDIT-5553E529-20260828.json`.

## 9. Reproducible offline training

One-off pulse-selection scripts are not the production training route.

The canonical route is now:

```text
HistoricalEpisodeEngine
→ historical_evidence enrichment
→ HistoricalModelBasedGainAdapter
→ HistoricalSensitivityTrainingPipeline
→ ModelBasedLocalGainCandidate per snapshot/context/grid
→ human/model review
→ future reviewed HistoricalSensitivityMap publication
```

The training pipeline never publishes runtime authority itself.

## 10. Safety state

None of the historical-map code changes the current production permissions:

```text
LEARN = 0
Residual = 0
DCS write = off
```

Historical priors currently exist for Shadow/audit architecture only. Separate calibration/activation review is still required before any future residual-control authority may be considered.
