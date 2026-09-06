from dataclasses import replace
import numpy as np
import pytest
from monitor.research.estimator import analyze, bootstrap_support, cycle_likelihood
from monitor.research.protocol import candidates, FAMILIES, method_digest
from .synthetic import simulate


@pytest.mark.parametrize('factors,family', [
    ((1.8,)*4,'global'), ((1,1,2,1),'cache_read'), ((1,2,1,1),'cache_creation'),
    ((2,1,1,1),'input'), ((1,1,1,2),'output'), ((1.5,1,2,1),'mixed'),
])
def test_predeclared_families_recover_distinguishable_synthetic_signals(factors, family):
    result = analyze(simulate(factors), gateway_only=True)
    assert result['eligible'] and result['status'] == 'exploratory'
    assert FAMILIES[np.argmax(result['support'])] == family
    assert result['support'][FAMILIES.index(family)] > .7
    assert np.isclose(sum(result['support']), 1)


def test_unchanged_is_a_real_candidate_not_forced_to_explain_anomaly():
    result = analyze(simulate((1,)*4), gateway_only=True)
    assert FAMILIES[np.argmax(result['support'])] == 'unchanged'
    assert result['score_mean'][0] == 0


def test_unknown_account_scales_do_not_change_likelihoods_or_support():
    cycles = simulate()
    scaled = [[replace(b, baseline=b.baseline*scale, target=tuple(x*scale for x in b.target)) for b in cycle] for cycle, scale in zip(cycles, (.2, 4, 10, .9, 1.3))]
    for a, b in zip(cycles, scaled):
        assert np.allclose(cycle_likelihood(a)[0], cycle_likelihood(b)[0])
    assert analyze(cycles, gateway_only=True)['support'] == analyze(scaled, gateway_only=True)['support']


def test_ties_share_votes_and_method_is_versioned():
    assert len(candidates()) == 1311 and len(set(candidates())) == 1311
    assert set(f for f,_ in candidates()) == set(FAMILIES)
    assert np.allclose(bootstrap_support(np.zeros((4, 7))), np.ones(7)/7)
    assert len(method_digest()) == 64


@pytest.mark.parametrize('kind', ['few_cycles','few_requests','pure_target','collinear','single_switch','no_attestation'])
def test_no_manufactured_confidence_when_identification_fails(kind):
    cycles = simulate()
    if kind == 'few_cycles': cycles = cycles[:1]
    elif kind == 'few_requests': cycles = [[replace(b, baseline_requests=0, target_requests=1) for b in c] for c in cycles]
    elif kind == 'pure_target': cycles = [[replace(b, baseline=0, baseline_requests=0) for b in c] for c in cycles]
    elif kind == 'collinear': cycles = [[replace(b, target=(sum(b.target)/4,)*4) for b in c] for c in cycles]
    elif kind == 'single_switch':
        # One type of model composition per cycle has no within-cycle anchor.
        cycles = [[replace(b, baseline=10, target=(10,10,10,10)) for b in c] for c in cycles]
    result = analyze(cycles, gateway_only=kind != 'no_attestation')
    assert not result['eligible']
    assert sum(result['support']) == 0


def test_all_models_can_fail_absolute_fit():
    cycles = simulate()
    cycles = [[replace(b, baseline=b.baseline*(100 if i%2 else .01), target=tuple(x*(100 if i%2 else .01) for x in b.target)) for i,b in enumerate(c)] for c in cycles]
    result = analyze(cycles, gateway_only=True)
    assert result['status'] == 'model_mismatch'
    assert not result['eligible'] and not any(result['support'])
