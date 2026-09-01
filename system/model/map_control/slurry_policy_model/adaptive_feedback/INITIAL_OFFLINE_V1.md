# Module-2 Offline Initial V1

This stage builds long-term plant-response knowledge. It does **not** train a future-trajectory predictor and it does not implement online control.

## Responsibilities

1. Calibrate the engineering raw-Qbase with a long-horizon `Kbase`.
2. Extract actual slurry-flow actions from `xstshsjy_LL` with A/B/rejected evidence grades.
3. Estimate SO2 and pH response onset independently, then estimate later response effect and `phi`.
4. Publish Global -> Condition shrinkage knowledge. Missing local history never means HOLD.

## Qbase calibration

The engineering relation is preserved exactly:

```text
omega_percent = 0.0013 * rho + 1.3
omega_fraction = omega_percent / 100
Ca/S = 1.7
purity = 0.9
```

Historical calibration uses measured outlet SO2 because the historical operator target is unknown:

```text
Qraw_hist = f(yyq_SO2 - jyq_SO2, yyq_LL, rho, omega, Ca/S, purity)
Kbase_window = actual_slurry_volume / raw_theoretical_slurry_volume
Kbase = robust median of long-horizon Kbase_window
```

The final snapshot does not contain a fixed outlet-SO2 target. Future online control must supply the current operator/DCS target at runtime.

## Response semantics

`response onset` and `response effect` are separate.

For +Q, outlet SO2 does not have to be already falling. If its pre-action rising trend becomes persistently slower after the action, that can be onset evidence. A later effect window is then compared with the pre-action local trend reference; only that later effect is used to form:

```text
phi_so2 = trend_referenced_SO2_effect / delta_Q_actual
phi_ph  = trend_referenced_pH_effect  / delta_Q_actual
```

SO2 and pH have independent onset delays and response statistics.

## Event quality

- A: strict disturbance-clean evidence, weight 1.0.
- B: moderate disturbance evidence, reduced weight.
- REJECT: overlapping action, condition/topology change, data gap, or disturbance too large.

The purpose is not to demand perfect laboratory-like history. A/B weighting keeps usable medium-quality evidence without treating it as equal to clean evidence.

## Sparse Condition policy

Response knowledge is hierarchical:

```text
Local Condition -> shrink toward Global -> Global fallback -> conservative step
```

C4 uses stronger shrinkage. EDGE_LOW/EDGE_HIGH are Global-only. Lack of local history is explicitly **not** a reason to HOLD; HOLD is reserved for later online safety/data-invalid/pending-state logic.

## Outputs

Running `initial_training.py` writes:

```text
module2_initial_snapshot.json
module2_initial_report.json
qbase_backtest.csv
response_events.csv
```

The snapshot is the future Incremental/Online long-term knowledge contract. It remains shadow-only and does not write DCS.
