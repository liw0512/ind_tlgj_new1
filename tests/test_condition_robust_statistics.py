from system.model.map_control.condition_model.robust_statistics import (
    OPERATING_CONTEXT_EVIDENCE_TYPE,
    RobustHistogramConfig,
    build_histogram,
    classify_distribution_shift,
    summarize_histogram,
)


def test_histogram_quantiles_and_trimmed_mean_ignore_extreme_overflow():
    config = RobustHistogramConfig()
    values = [31.0] * 400 + [32.0] * 400 + [33.0] * 400 + [10000.0] * 10
    histogram = build_histogram(values, config)
    summary = summarize_histogram(histogram, config)

    assert summary["in_range_count"] == 1200
    assert summary["overflow_count"] == 10
    assert 31.0 <= summary["p50"] <= 33.0
    assert 31.0 <= summary["trimmed_mean_05_95"] <= 33.0


def test_context_shift_thresholds_follow_steel_history_calibration():
    config = RobustHistogramConfig(
        min_independent_days=1,
        min_batch_samples=100,
        min_baseline_samples=100,
    )
    baseline = build_histogram([32.0] * 1000, config)

    stable = classify_distribution_shift(
        baseline,
        build_histogram([33.0] * 300, config),
        config,
        independent_days=2,
    )
    suspected = classify_distribution_shift(
        baseline,
        build_histogram([34.2] * 300, config),
        config,
        independent_days=2,
    )
    strong = classify_distribution_shift(
        baseline,
        build_histogram([40.0] * 300, config),
        config,
        independent_days=2,
    )

    assert stable["status"] == "STABLE"
    assert suspected["status"] == "SUSPECTED_CONTEXT_SHIFT"
    assert strong["status"] == "STRONG_CONTEXT_SHIFT"
    assert suspected["evidence_type"] == OPERATING_CONTEXT_EVIDENCE_TYPE
    assert suspected["structural_evidence"] is False


def test_insufficient_batch_is_not_called_stable_or_context_shift():
    config = RobustHistogramConfig(
        min_independent_days=2,
        min_batch_samples=300,
        min_baseline_samples=300,
    )
    baseline = build_histogram([32.0] * 1000, config)
    batch = build_histogram([40.0] * 100, config)
    result = classify_distribution_shift(
        baseline,
        batch,
        config,
        independent_days=1,
    )
    assert result["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["evidence_type"] == OPERATING_CONTEXT_EVIDENCE_TYPE
    assert result["structural_evidence"] is False
