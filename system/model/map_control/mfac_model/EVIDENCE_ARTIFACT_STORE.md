# Scheme 2 Evidence Artifact Store / Loader

## Purpose

`EvidenceArtifactStore` and `EvidenceArtifactLoader` persist and recover the review evidence that is already bound by `DualResponseEvidenceProvenanceBundle`.

They are **not** a new configuration source and they do not activate MFAC. Their job is only to preserve evidence without allowing files from different trials, cohorts or calibration reviews to be silently mixed.

## Canonical paths

Production path ownership remains in:

```text
system/model/config/mfac_paths.py
```

The only production authorities are:

```text
MFAC_EVIDENCE_ROOT
MFAC_EVIDENCE_OBJECTS_DIR
MFAC_EVIDENCE_BUNDLES_DIR
```

The store does not independently rebuild a production evidence path. Unit tests may explicitly inject a temporary root.

## On-disk layout

```text
mfac_model_output/evidence/
├── objects/
│   └── <sha256-prefix>/
│       └── <sha256>.json
└── bundles/
    └── <bundle_id>.json
```

Each object filename is the SHA256 of its canonical JSON semantic payload. The persisted object types are existing evidence objects:

```text
LOCAL_GAIN_COHORT_APPROVED_EVENTS
LOCAL_STEP_RAW_TRACE
OBSERVED_RESPONSE_TIMING
CHANNEL_CONFIDENCE_EVIDENCE
DUAL_RESPONSE_CALIBRATION_PROFILE
```

No copied control parameters are introduced by the store.

## Atomic write contract

Before persistence, the supplied evidence chain must already pass:

```text
verify_evidence_provenance_bundle(...)
```

Each new JSON object is written as:

```text
*.tmp
→ flush
→ fsync
→ os.replace
```

The bundle manifest uses the same atomic replacement sequence.

A process failure before the manifest commit may leave an unreferenced content-addressed object. That object is harmless because no loader accepts it without a valid manifest reference and matching digest.

## Immutable content and append-only bundle identity

Evidence objects are immutable content addresses. If an object file already exists, its content is re-hashed and must still equal its filename digest.

Bundle IDs are also stable identities:

```text
same bundle_id + identical provenance chain
→ idempotent save

same bundle_id + different provenance chain
→ reject

same bundle_id + corrupt existing manifest
→ reject; do not silently overwrite
```

This prevents a later process from reusing a previously reviewed bundle name for a different evidence chain.

## Manifest contents

A bundle manifest records:

```text
store schema version
bundle_id
SHA256(provenance bundle)
serialized provenance bundle
referenced object digests
review_chain_complete
permission flags fixed false
written_at_utc
```

`written_at_utc` is audit metadata only and is not part of the immutable bundle identity. Re-saving an identical bundle does not rewrite the manifest.

## Loader contract

`EvidenceArtifactLoader.load(bundle_id)` is fail-closed.

It performs all of the following:

1. validates `bundle_id` as a non-path identifier;
2. loads and validates manifest schema and permission flags;
3. recomputes the provenance-bundle SHA256;
4. reconstructs `DualResponseEvidenceProvenanceBundle`;
5. requires the manifest object list to exactly match provenance refs;
6. loads every referenced object by its digest-derived path;
7. recomputes every object SHA256;
8. reconstructs typed `ActionResponseEvent`, raw trace, timing evidence, confidence evidence and calibration profile objects;
9. re-runs the calibration-profile review-seal validation during typed reconstruction;
10. re-runs `verify_evidence_provenance_bundle(...)` against the reconstructed objects.

Possible top-level results are:

```text
VERIFIED_REVIEW_EVIDENCE
EVIDENCE_BUNDLE_NOT_FOUND
EVIDENCE_INTEGRITY_FAILURE
```

## Verified does not mean activated

A successful load means only:

> the persisted evidence can be reconstructed and its review provenance is internally consistent.

It does not mean:

```text
MFAC learning enabled
residual control enabled
DCS write enabled
```

`EvidenceArtifactLoadResult.to_runtime_config()` always rejects.

Even when:

```text
review_chain_complete = true
verification = VERIFIED
```

a separate `DualResponseActivationReview` is still required, and the current formal runtime remains fail-closed.

## Current project state

The Store / Loader implementation and tests exist, but the repository still has no real controlled `+2 m3/h` LOCAL_GAIN trial evidence to populate a production evidence bundle.

Therefore the current real state remains:

```text
real raw trace objects       unavailable
real observed timing objects unavailable
real confidence objects      unavailable
real complete bundle         unavailable
```

Production safety remains:

```text
LEARN = 0
Residual = 0
DCS write = off
```
