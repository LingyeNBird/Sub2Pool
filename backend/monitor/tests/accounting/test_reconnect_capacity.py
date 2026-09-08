"""Reconnect baselines must not act as new capacity evidence."""

from dataclasses import replace

import numpy as np
import pytest

from monitor.accounting.adaptive_range import run_adaptive_range_filter
from monitor.accounting.dynamic_contracts import DynamicModelInput
from monitor.accounting.particle_filter import ParticleFilterConfig


def sample(baseline, costs=(0., 0.), progress=(0., 0.)):
    return DynamicModelInput(
        times_hours=np.arange(len(costs), dtype=float) / 60,
        costs_usd=np.asarray(costs)[:, None],
        displayed_percent=np.asarray(progress) + baseline,
        rights_percent=np.asarray([100.]),
        baseline_display_percent=baseline,
        baseline_exact_zero=baseline == 0,
    )


@pytest.mark.parametrize("baseline", [0., 18., 75.])
@pytest.mark.parametrize("prior", [828.41, 1800., 8000.])
def test_no_spend_does_not_create_expansion_evidence_or_clip_prior(baseline, prior):
    output = run_adaptive_range_filter(
        sample(baseline),
        seed=42,
        config=ParticleFilterConfig(initial_capacity_usd=prior),
    )
    assert not output.promotions
    assert output.particle.capacity_hat_usd[-1] == pytest.approx(prior, abs=45)
    assert output.capacity_min_usd[-1] < prior < output.capacity_max_usd[-1]


@pytest.mark.parametrize("baseline", [18., 60.])
def test_nonzero_baseline_does_not_invent_lower_expansion_for_in_range_capacity(baseline):
    output = run_adaptive_range_filter(
        sample(baseline, (0., 180., 360.), (0., 10., 20.)),
        seed=17,
        config=ParticleFilterConfig(initial_capacity_usd=1800.),
    )
    assert not output.promotions
    assert output.particle.capacity_hat_usd[-1] == pytest.approx(1800., abs=200)


@pytest.mark.parametrize("baseline", [0., 18.])
@pytest.mark.parametrize(
    "costs,direction",
    [((0., 20., 40., 60.), "lower"), ((0., 800., 1600., 2400.), "upper")],
)
def test_true_range_mismatch_still_expands_after_reconnect(baseline, costs, direction):
    model = replace(
        sample(baseline, costs, (0., 10., 20., 30.)),
        times_hours=np.asarray([0., 12., 24., 36.]),
    )
    output = run_adaptive_range_filter(model, seed=91 if direction == "lower" else 81)
    assert output.direction == direction
    assert output.promotions
    assert all(p.row > 0 for p in output.promotions)
    if direction == "lower":
        assert output.capacity_min_usd[-1] < 1400
    else:
        assert output.capacity_max_usd[-1] > 4000
