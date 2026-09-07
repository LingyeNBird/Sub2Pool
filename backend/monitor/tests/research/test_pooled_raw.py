"""Raw-only contract: no sample gate, estimator feedback or automatic data loss."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from importlib import import_module
import json
import uuid

import numpy as np
import pytest
from django.apps import apps
from django.db import connection
from django.utils import timezone
from monitor.billing_correction.persistence import persist_capture
from monitor.fast_correction.domain import aggregate_fast_logs
from monitor.fast_correction.rules import FastCorrectionRuleSet
from monitor.models import AppSettings, Observation, ResearchSettings, ResearchRequestComponents
from monitor.models.research import ResearchEvidenceBatch
from monitor.research import service, transport
from monitor.research.pooled import Interval, grid, group_evidence, groups, summarize
from monitor.research.pooled_data import collect_batches, normalized_cost
from monitor.research.pooled_protocol import canonical, consent_digest, descriptor, method_digest, grid_digest, QUALITY_KEYS, STUDY
from monitor.tests.research.test_data import enable, request

pytestmark = pytest.mark.django_db


def raw_cycle(n=2, *, start=None, account=7, modes=None):
    """One request per interval, including an initial raw quota observation."""
    start = start or timezone.now().replace(microsecond=0) - timedelta(hours=n+1)
    reset = start + timedelta(days=7)
    obs = []
    for i in range(n+1):
        at = start + timedelta(hours=i)
        row = Observation.objects.create(account_id=account, observed_at=at, window_seconds=604800,
            upstream_resets_at=reset, upstream_used_percent=i, total_actual_cost=10*i,
            total_standard_cost=10*i, raw_selected_total_cost=10*i, selected_total_cost=10*i,
            effective_usd_per_percent=20, raw_window={'query_mode':'direct'})
        obs.append(row)
        if i:
            changes = (modes or [{}]*n)[i-1]
            log = replace(request(at-timedelta(minutes=1), id=1000+i), **changes)
            interval = aggregate_fast_logs([log], started_at=at-timedelta(hours=1), ended_at=at,
                rules=FastCorrectionRuleSet(AppSettings.load().fast_correction_rules))
            persist_capture(row, interval)
    return obs


def test_one_request_one_interval_is_a_contribution_not_a_local_verdict(monkeypatch):
    settings = enable()
    settings.endpoint = 'https://receiver.example'
    settings.consent_hash = consent_digest(settings.endpoint, settings.projects, settings.gateway_only)
    settings.save()
    raw_cycle(1)
    packets = []
    def send(_endpoint, path, body, signature):
        data = json.loads(body); packets.append(data)
        assert path == '/api/reports' and signature
        return {'revision':data['revision']}
    monkeypatch.setattr(transport, 'send', send)
    assert service.run_due() == 'sent'
    assert len(packets) == 1
    s = packets[0]['summary']
    assert (s['requests'], s['intervals'], s['groups'], s['contrasts']) == (1,1,1,0)
    assert not np.any(s['log_evidence'])
    assert 'eligible' not in s and 'support' not in s
    assert set(s) == {'requests','gpt6_requests','other_requests','raw_usd','gpt6_raw_usd','quota_points',
        'intervals','groups','contrasts','gateway_only','quality','log_evidence','gpt6_quota','information'}
    serialized = canonical(s).lower()
    for word in (b'capacity', b'particle', b'constant', b'auxiliary', b'account_id', b'created_at', b'user_id', b'api_key', b'prompt'):
        assert word not in serialized


def test_costs_without_quota_observations_still_send_counts(monkeypatch):
    enable(); rows = raw_cycle(1)
    rows[0].delete()
    batches = collect_batches(timezone.now(), gateway_only=False)
    assert len(batches) == 1
    assert batches[0].summary['requests'] == 1
    assert batches[0].summary['intervals'] == 0
    assert not np.any(batches[0].summary['log_evidence'])


def test_normalize_fast_long_and_other_models_without_whole_interval_exclusion():
    enable()
    changes = [dict(service_tier='priority', long_context_billing_applied=True),
        dict(model='claude-test', component_costs=None), dict(model='gpt-5.6', service_tier='fast')]
    raw_cycle(3, modes=changes)
    batch = collect_batches(timezone.now(), gateway_only=True)[0]
    assert batch.summary['requests'] == 3 and batch.summary['intervals'] == 3
    assert batch.summary['gpt6_requests'] == 1 and batch.summary['other_requests'] == 2
    assert not any(batch.summary['quality'].values())
    fact = ResearchRequestComponents.objects.select_related('fact').first().fact
    known, components = normalized_cost(fact, AppSettings.load())
    assert known == 0 and components == (1,.5,1.5,2)  # FAST 2/2, long 1/2, NOT model 1.8


def test_no_feedback_from_either_derived_model_or_running_gpt6_multiplier():
    enable(); raw_cycle(3, modes=[{},dict(model='gpt-5.6'),{}])
    before = canonical(collect_batches(timezone.now(), gateway_only=True)[0].summary)
    Observation.objects.update(selected_total_cost=9999, effective_usd_per_percent=999,
        model_diagnostics={'posterior_capacity':99999,'particles':[3000,4500]}, estimated_used_percent=99)
    config = AppSettings.load(); config.cost_basis = 'actual'
    config.model_correction_rules = [{'model_pattern':'gpt-6*','multiplier':'1.75'}]
    config.fast_correction_rules[0]['target_multiplier'] = '99'
    config.long_context_correction_rules[1]['target_multiplier'] = '88'
    config.weekly_quota_model = 'time_varying'; config.save()
    assert canonical(collect_batches(timezone.now(), gateway_only=True)[0].summary) == before
    config.weekly_quota_model = 'constant_average'
    config.model_correction_rules[0]['multiplier'] = '1.8'; config.save()
    assert canonical(collect_batches(timezone.now(), gateway_only=True)[0].summary) == before


@pytest.mark.parametrize('source, expected', [('1',(2,1,3,4)),('2',(1,.5,1.5,2)),('4',(.5,.25,.75,1))])
def test_explicit_upstream_source_long_multiplier_only(source, expected):
    enable(); raw_cycle(1, modes=[dict(long_context_billing_applied=True)])
    fact = ResearchRequestComponents.objects.select_related('fact').first().fact
    config = AppSettings.load()
    config.long_context_correction_rules[1]['source_multiplier'] = source
    config.long_context_correction_enabled = False
    assert normalized_cost(fact, config)[1] == expected


def test_unknown_control_preserves_counts_not_fabricated_quota_evidence():
    enable(); raw_cycle(2, modes=[dict(input_tokens=None,cache_creation_tokens=None,cache_read_tokens=None,long_context_billing_applied=None),{}])
    batch = collect_batches(timezone.now(), gateway_only=True)[0]
    assert batch.summary['requests'] == 2 and batch.summary['intervals'] == 1
    assert batch.summary['quality']['unknown_control'] == 1


def test_repeated_collection_appends_new_cycle_and_never_expires_or_reduces_archive():
    enable()
    rows = raw_cycle(2, start=timezone.now()-timedelta(days=400))
    before = collect_batches(timezone.now(), gateway_only=True)[0]
    key, old = before.pk, before.summary
    assert len(collect_batches(timezone.now(), gateway_only=True)) == 1
    raw_cycle(1)
    assert len(collect_batches(timezone.now(), gateway_only=True)) == 2
    rows[-1].delete()
    batches = collect_batches(timezone.now()+timedelta(days=1000), gateway_only=True)
    archived = next(b for b in batches if b.pk == key)
    assert archived.summary == old and archived.archived_source
    assert len(batches) == 2


def test_shutdown_and_import_do_not_retract_remote_history(monkeypatch):
    config = enable(); raw_cycle(1)
    batches = collect_batches(timezone.now(), gateway_only=True)
    raw_count = ResearchRequestComponents.objects.count()
    def forbidden(*args, **kwargs): raise AssertionError('unexpected outbound call')
    monkeypatch.setattr(transport, 'send', forbidden)
    config.enabled = False; config.save()
    assert service.run_due() == 'disabled'
    assert ResearchRequestComponents.objects.count() == raw_count
    assert ResearchEvidenceBatch.objects.get(pk=batches[0].pk).summary['requests'] == 1


@pytest.mark.parametrize('endpoint,expected', [
    ('https://study.example.invalid','https://codex.nightunderfly.online'),
    ('https://study.example.invalid/','https://codex.nightunderfly.online'),
    ('https://custom.example','https://custom.example'), ('','')])
def test_upgrade_defaults_preserves_identity_and_user_address(endpoint, expected):
    settings = enable(); settings.endpoint=endpoint
    settings.identity_encrypted='retained-ciphertext';settings.report_revision=17
    settings.last_sent_endpoint='https://old.example';settings.last_sent_hash='sent-hash';settings.save()
    migration = import_module('monitor.migrations.0051_pooled_research_raw_only')
    class Schema: pass
    schema = Schema(); schema.connection = connection
    migration.upgrade_defaults_and_consent(apps,schema)
    settings.refresh_from_db()
    assert settings.endpoint == expected and not settings.enabled and not settings.consent_hash
    assert settings.identity_encrypted == 'retained-ciphertext' and settings.report_revision == 17
    assert settings.last_sent_endpoint == 'https://old.example' and settings.last_sent_hash == 'sent-hash'


def test_one_prior_shared_grid_digest_and_declared_no_estimate_policy():
    assert grid_digest() == descriptor()['grid_sha256']
    assert len(grid()[0]) == descriptor()['candidate_count'] == 1311
    assert descriptor()['minimum_requests'] == descriptor()['minimum_cycles'] == descriptor()['minimum_intervals'] == 0
    assert not descriptor()['local_rank_required']
    assert 'No particle-filter' in descriptor()['estimate_policy']


def test_scale_invariance_and_single_interval_flat_evidence():
    rows = [Interval(0,1,1,5,(1,2,3,4)),Interval(1,2,2,2,(4,3,2,1))]
    before = group_evidence(rows)
    after = group_evidence([replace(r,known=r.known*100,target=tuple(x*100 for x in r.target)) for r in rows])
    for a,b in zip(before,after): np.testing.assert_allclose(a,b,rtol=1e-8,atol=1e-8)
    ll, q, info = group_evidence(rows[:1])
    assert np.count_nonzero(ll) == np.count_nonzero(info) == 0
    assert np.all(q >= 0) and np.all(q <= 1)


def test_direct_scalar_gls_matches_vectorized_evidence():
    rows=[Interval(0,.5,0,2,(1,3,1,4)),Interval(.5,1,2,4,(2,1,3,1)),Interval(1,1.5,1,1,(4,2,1,3))]
    curves,_,_=group_evidence(rows)
    z=np.array([[r.known,*r.target] for r in rows]);y=np.array([r.quota for r in rows])
    v=np.eye(3)*(1/6+.01);v[0,1]=v[1,0]=v[1,2]=v[2,1]=-1/12
    w=np.linalg.inv(v);points,null=grid()
    scores=[]
    for beta in points:
        c=z @ np.r_[1.,beta]
        a=max(c @ w @ y,0)/(c @ w @ c)
        residual=(y-a*c) @ w @ (y-a*c)
        scores.append(-.5*(4+3-1)/2*np.log1p(residual/4))
    np.testing.assert_allclose(curves[0],np.array(scores)-scores[null],atol=1e-10)


def test_small_locally_rank_one_groups_add_global_information():
    rng=np.random.default_rng(260907)
    total=np.zeros((4,4))
    for _ in range(30):
        z=rng.uniform(1,10,size=(2,5));y=z @ np.array([1,1,1,2,1])/10
        rows=[Interval(i,i+1,float(y[i]),float(z[i,0]),tuple(z[i,1:])) for i in range(2)]
        _,_,info=group_evidence(rows)
        assert np.linalg.matrix_rank(info,tol=1e-8)==1
        total+=info
    assert np.linalg.matrix_rank(total,tol=1e-8)==4


def test_zero_quota_counts_and_upper_group_sizes_not_a_lower_gate():
    rows=[Interval(i*.1,(i+1)*.1,0,2,(1,2,3,4)) for i in range(33)]
    assert list(map(len,groups(rows))) == [32,1]
    result=summarize(rows,requests=33,gpt6_requests=33)
    assert result['intervals']==33 and result['quota_points']==0
    assert not np.any(result['log_evidence'])


def test_new_consent_required_no_silent_method_upgrade():
    from monitor.research.protocol import consent_digest as legacy_digest
    config=enable()
    config.consent_hash=legacy_digest(config.endpoint,config.projects,config.gateway_only);config.save()
    assert service.run_due()=='disabled'


def test_identity_and_batch_pseudonyms_are_different_at_other_destinations():
    settings=enable();batch=uuid.uuid4()
    one=json.loads(transport.packet(settings,summarize([]),batch_id=batch)[1])
    two=json.loads(transport.packet(settings,summarize([]),batch_id=batch)[1])
    other=json.loads(transport.packet(settings,summarize([]),batch_id=batch,endpoint='https://other.example')[1])
    assert one['batch_id']==two['batch_id'] and one['public_key']==two['public_key']
    assert one['batch_id']!=other['batch_id'] and one['public_key']!=other['public_key']
    assert str(batch)!=one['batch_id']
