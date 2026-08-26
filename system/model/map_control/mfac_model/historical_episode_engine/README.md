# Historical Episode Engine

This directory is the MFAC-owned copy of the former second-module physical episode extractor.

It is retained only for offline evidence generation:

```text
10s production-equivalent history
-> actual slurry-flow event detection
-> SO2 / pH effect profiling
-> MFAC ActionResponseEvent adapter
```

It does **not** contain or authorize the removed slurry-policy/Q-learning online controller.
Runtime control remains owned by `mfac_model`.
