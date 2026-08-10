import numpy as np
import pytest

from monitor.accounting.adaptive_range import run_adaptive_range_filter
from monitor.accounting.deterministic_bounds import (
    project_attribution_to_bounds,
    run_deterministic_bounds,
)
from monitor.accounting.dynamic_contracts import (
    DeterministicBoundsOutput,
    DynamicModelInput,
)
from monitor.accounting.particle_filter import (
    ParticleFilterConfig,
    run_particle_filter,
)


def _constant_capacity_input() -> DynamicModelInput:
    return DynamicModelInput(
        times_hours=np.asarray([0.0, 12.0, 24.0, 36.0, 48.0]),
        costs_usd=np.asarray(
            [
                [0.0, 0.0],
                [54.0, 36.0],
                [108.0, 72.0],
                [162.0, 108.0],
                [216.0, 144.0],
            ]
        ),
        displayed_percent=np.asarray([0.0, 5.0, 10.0, 15.0, 20.0]),
        rights_percent=np.asarray([60.0, 40.0]),
    )


def test_particle_filter_is_repeatable_and_tracks_constant_case():
    model_input = _constant_capacity_input()

    first = run_particle_filter(model_input, seed=20260809)
    second = run_particle_filter(model_input, seed=20260809)

    np.testing.assert_array_equal(first.capacity_hat_usd, second.capacity_hat_usd)
    np.testing.assert_array_equal(
        first.attributed_percent_hat,
        second.attributed_percent_hat,
    )
    assert first.capacity_hat_usd[-1] == pytest.approx(1800.0, abs=180.0)
    assert first.total_percent_hat[-1] == pytest.approx(20.0, abs=1.5)
    assert first.attributed_percent_hat[-1, 0] == pytest.approx(12.0, abs=1.5)
    assert first.attributed_percent_hat[-1, 1] == pytest.approx(8.0, abs=1.5)
    np.testing.assert_allclose(first.quantizer_probabilities.sum(axis=1), 1.0)
    np.testing.assert_allclose(first.speed_probabilities.sum(axis=1), 1.0)
    assert np.all(first.capacity_lower_usd <= first.capacity_hat_usd)
    assert np.all(first.capacity_hat_usd <= first.capacity_upper_usd)


def test_adaptive_range_keeps_standard_support_without_dual_evidence():
    output = run_adaptive_range_filter(
        _constant_capacity_input(),
        seed=20260809,
    )

    assert output.direction is None
    assert output.promotions == ()
    np.testing.assert_array_equal(output.capacity_min_usd, 1400.0)
    np.testing.assert_array_equal(output.capacity_max_usd, 4000.0)


def test_adaptive_range_replays_upper_stages_when_evidence_persists():
    model_input = DynamicModelInput(
        times_hours=np.asarray([0.0, 12.0, 24.0, 36.0]),
        costs_usd=np.asarray([[0.0], [800.0], [1600.0], [2400.0]]),
        displayed_percent=np.asarray([0.0, 10.0, 20.0, 30.0]),
        rights_percent=np.asarray([100.0]),
    )

    output = run_adaptive_range_filter(model_input, seed=81)

    assert output.direction == "upper"
    assert output.capacity_max_usd[-1] == 10000.0
    assert [item.to_max_usd for item in output.promotions] == [
        6000.0,
        10000.0,
    ]
    assert output.capacity_min_usd[-1] == 1400.0
    assert np.all(np.diff(output.stage) >= 0)
    assert output.capacity_max_usd[-1] >= 6000.0
    assert output.capacity_min_usd[-1] == 1400.0


def test_adaptive_range_replays_lower_stages_when_evidence_persists():
    model_input = DynamicModelInput(
        times_hours=np.asarray([0.0, 12.0, 24.0, 36.0]),
        costs_usd=np.asarray([[0.0], [20.0], [40.0], [60.0]]),
        displayed_percent=np.asarray([0.0, 10.0, 20.0, 30.0]),
        rights_percent=np.asarray([100.0]),
    )

    output = run_adaptive_range_filter(model_input, seed=91)

    assert output.direction == "lower"
    assert output.promotions
    assert output.capacity_min_usd[-1] == 50.0
    assert [item.to_min_usd for item in output.promotions] == [
        700.0,
        250.0,
        50.0,
    ]
    assert output.capacity_max_usd[-1] == 4000.0
    assert output.capacity_min_usd[-1] <= 700.0
    assert output.capacity_max_usd[-1] == 4000.0


def test_expanded_capacity_support_tracks_values_above_legacy_ceiling():
    model_input = DynamicModelInput(
        times_hours=np.asarray([0.0, 12.0, 24.0, 36.0]),
        costs_usd=np.asarray(
            [[0.0], [140.0], [280.0], [560.0]]
        ),
        displayed_percent=np.asarray([0.0, 5.0, 10.0, 20.0]),
        rights_percent=np.asarray([100.0]),
    )

    expanded = run_particle_filter(model_input, seed=81)
    legacy = run_particle_filter(
        model_input,
        seed=81,
        config=ParticleFilterConfig(
            capacity_min_usd=1400.0,
            capacity_max_usd=2100.0,
        ),
    )

    assert expanded.capacity_hat_usd[-1] > 2100.0
    assert abs(expanded.total_percent_hat[-1] - 20.0) < abs(
        legacy.total_percent_hat[-1] - 20.0
    )

def test_particle_filter_supports_uncertain_manual_baseline():
    model_input = DynamicModelInput(
        times_hours=np.asarray([0.0, 12.0, 24.0]),
        costs_usd=np.asarray([[0.0], [90.0], [180.0]]),
        displayed_percent=np.asarray([10.0, 15.0, 20.0]),
        rights_percent=np.asarray([50.0]),
        baseline_display_percent=10.0,
        baseline_exact_zero=False,
    )

    output = run_particle_filter(model_input, seed=17)

    assert output.attributed_percent_hat[-1, 0] == pytest.approx(10.0, abs=1.5)
    assert np.all(np.isfinite(output.balance_hat_usd))


def test_deterministic_bounds_contain_constant_case_truth():
    output = run_deterministic_bounds(_constant_capacity_input())
    true_attributed = np.asarray(
        [[0.0, 0.0], [3.0, 2.0], [6.0, 4.0], [9.0, 6.0], [12.0, 8.0]]
    )

    assert np.all(output.attributed_percent_lower <= true_attributed)
    assert np.all(true_attributed <= output.attributed_percent_upper)
    assert output.total_percent_lower[-1] <= 20.0
    assert output.total_percent_upper[-1] >= 20.0
    assert output.infeasible_repairs == 0

def test_guarded_projection_restores_deterministic_progress_feasibility():
    bounds = run_deterministic_bounds(_constant_capacity_input())
    impossible = np.zeros_like(bounds.attributed_percent_lower)

    projected, repaired_rows, max_adjustment = (
        project_attribution_to_bounds(impossible, bounds)
    )

    assert repaired_rows > 0
    assert max_adjustment > 0
    assert np.all(projected >= bounds.attributed_percent_lower - 1e-9)
    assert np.all(projected <= bounds.attributed_percent_upper + 1e-9)
    totals = projected.sum(axis=1)
    assert np.all(totals >= bounds.total_percent_lower - 1e-9)
    assert np.all(totals <= bounds.total_percent_upper + 1e-9)


def test_guarded_projection_preserves_cumulative_monotonicity():
    bounds = DeterministicBoundsOutput(
        total_percent_lower=np.asarray([0.0, 5.0, 10.0]),
        total_percent_upper=np.asarray([0.0, 5.0, 10.0]),
        attributed_percent_lower=np.zeros((3, 2)),
        attributed_percent_upper=np.asarray(
            [[0.0, 0.0], [5.0, 5.0], [10.0, 10.0]]
        ),
        balance_lower_usd=np.zeros((3, 2)),
        balance_upper_usd=np.zeros((3, 2)),
        infeasible_repairs=0,
    )
    oscillating = np.asarray([[0.0, 0.0], [5.0, 0.0], [0.0, 10.0]])

    projected, _, _ = project_attribution_to_bounds(oscillating, bounds)

    np.testing.assert_allclose(projected.sum(axis=1), [0.0, 5.0, 10.0])
    assert np.all(np.diff(projected, axis=0) >= -1e-9)


def test_dynamic_models_cap_progress_at_cycle_exhaustion():
    model_input = DynamicModelInput(
        times_hours=np.asarray([0.0, 12.0, 24.0]),
        costs_usd=np.asarray([[0.0], [2000.0], [2200.0]]),
        displayed_percent=np.asarray([0.0, 100.0, 100.0]),
        rights_percent=np.asarray([100.0]),
    )

    particle = run_particle_filter(model_input, seed=9)
    bounds = run_deterministic_bounds(model_input)

    assert np.all(particle.total_percent_upper <= 100.0)
    assert np.all(particle.attributed_percent_upper <= 100.0)
    assert np.all(bounds.total_percent_upper <= 100.0)


def test_dynamic_input_rejects_non_monotone_costs():
    model_input = DynamicModelInput(
        times_hours=np.asarray([0.0, 1.0]),
        costs_usd=np.asarray([[10.0], [9.0]]),
        displayed_percent=np.asarray([0.0, 1.0]),
        rights_percent=np.asarray([100.0]),
    )

    with pytest.raises(ValueError, match="累计成本"):
        run_particle_filter(model_input, seed=1)


def test_particle_filter_reports_pre_resample_effective_sample_size():
    model_input = DynamicModelInput(
        times_hours=np.asarray([0.0, 12.0]),
        costs_usd=np.asarray([[0.0], [200.0]]),
        displayed_percent=np.asarray([0.0, 20.0]),
        rights_percent=np.asarray([100.0]),
    )

    output = run_particle_filter(
        model_input,
        seed=31,
        config=ParticleFilterConfig(resample_ess_fraction=1.0),
    )

    assert output.resampled[1]
    assert output.ess_fraction[1] < 1.0
