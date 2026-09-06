from datetime import timedelta
from dataclasses import replace
from decimal import Decimal as D
import pytest
from django.utils import timezone
from monitor.models import AppSettings, Observation, ResearchSettings, ResearchRequestComponents
from monitor.research.protocol import consent_digest, STUDY
from monitor.research.data import _block, Ineligible, quota_time, collect_cycles
from monitor.billing_correction.persistence import persist_capture
from monitor.fast_correction.domain import aggregate_fast_logs
from monitor.fast_correction.rules import FastCorrectionRuleSet
from monitor.integrations.sub2api.usage import _component_costs
from monitor.tests.billing_correction.test_corrections import log
from monitor.tests.helpers import create_monitored_account

pytestmark = pytest.mark.django_db


def enable():
    settings = ResearchSettings.load()
    settings.enabled = True; settings.projects = [STUDY]
    settings.consent_hash = consent_digest(settings.endpoint, settings.projects, settings.gateway_only)
    settings.save()
    return settings


def capture(start, end, logs, percent=5):
    row = Observation.objects.create(account_id=7, observed_at=end, window_seconds=604800,
        upstream_resets_at=start+timedelta(days=4), upstream_used_percent=percent,
        total_actual_cost=100, total_standard_cost=100, raw_selected_total_cost=100, selected_total_cost=100,
        effective_usd_per_percent=20, raw_window={'query_mode':'direct'})
    interval = aggregate_fast_logs(logs, started_at=start, ended_at=end, rules=FastCorrectionRuleSet(AppSettings().fast_correction_rules))
    persist_capture(row, interval)
    return row


def request(at, **changes):
    return log(created_at=at, total_cost=D(10), actual_cost=D(10), service_tier='default',
        long_context_billing_applied=False, component_costs=(D(2),D(1),D(3),D(4)), **changes)


def test_default_off_never_stores_extra_component_facts():
    at = timezone.now()
    row = capture(at-timedelta(hours=1), at, [request(at-timedelta(seconds=1))])
    assert row.billing_capture.facts.count() == 1
    assert not ResearchRequestComponents.objects.exists()
    with pytest.raises(Ineligible, match='missing_components'): _block(7,at-timedelta(hours=1),at,5)


def test_opted_in_preserves_exact_raw_components_even_when_multipliers_change():
    enable(); at=timezone.now()
    capture(at-timedelta(hours=1),at,[request(at-timedelta(seconds=1))])
    before = list(ResearchRequestComponents.objects.values())
    block = _block(7,at-timedelta(hours=1),at,5)
    assert block.target == (2,1,3,4) and block.target_requests == 1
    settings=AppSettings.load();settings.model_correction_rules=[{'model_pattern':'*','multiplier':'99'}];settings.save()
    assert _block(7,at-timedelta(hours=1),at,5) == block
    assert list(ResearchRequestComponents.objects.values()) == before


def test_true_quota_snapshot_time_and_exact_clipping():
    enable(); end=timezone.now(); start=end-timedelta(hours=1)
    capture(start,end,[request(start,id=1),request(start+timedelta(minutes=30),id=2),request(end-timedelta(seconds=1),id=3)])
    result = _block(7,start+timedelta(minutes=30),end-timedelta(seconds=1),4)
    assert result.target_requests == 1
    row=Observation.objects.first();row.raw_window={'query_mode':'passive','sampled_at':(start+timedelta(minutes=30)).isoformat()}
    assert quota_time(row) == start+timedelta(minutes=30)
    row.raw_window={'query_mode':'passive'}
    with pytest.raises(Ineligible, match='missing_snapshot_time'): quota_time(row)


@pytest.mark.parametrize('change,expected', [
    ({'model':'gpt-60'},'other_model'), ({'model':'gpt-5.60'},'other_model'),
    ({'model':'claude'},'other_model'), ({'service_tier':'priority'},'nonstandard_request'),
    ({'long_context_billing_applied':True},'nonstandard_request'),
    ({'component_costs':None},'missing_components'),
    ({'component_costs':(D(1),)*4},'cost_mismatch'),
])
def test_ineligible_request_excludes_whole_quota_interval(change, expected):
    enable();at=timezone.now();start=at-timedelta(hours=1)
    logs=[request(start+timedelta(minutes=1),id=1), replace(request(start+timedelta(minutes=2),id=2),**change)]
    capture(start,at,logs)
    with pytest.raises(Ineligible, match=expected): _block(7,start,at,5)


def test_coverage_gap_cannot_be_inferred_from_totals():
    enable();at=timezone.now()
    capture(at-timedelta(minutes=30),at,[request(at-timedelta(minutes=1))])
    with pytest.raises(Ineligible,match='capture_gap'): _block(7,at-timedelta(hours=1),at,5)


@pytest.mark.parametrize('bad', [None,{},True,{'input_cost':'NaN'}, {'input_cost':1,'output_cost':1,'cache_creation_cost':0,'cache_read_cost':False}])
def test_missing_or_malformed_optional_components_do_not_break_primary_usage(bad):
    assert _component_costs(bad or {}) is None


def test_baseline_family_and_collect_cycles():
    enable();create_monitored_account()
    start=timezone.now()-timedelta(hours=20)
    first=None
    for i in range(13):
        end=start+timedelta(hours=i)
        row=capture(end-timedelta(hours=1),end,[request(end-timedelta(minutes=1),id=i*2+j+1,model='gpt-5.6' if i%2 else 'gpt-6') for j in range(2)],percent=i*4)
        row.upstream_resets_at=start+timedelta(days=7);row.save()
        first=first or row
    cycles,excluded=collect_cycles()
    assert len(cycles)==1 and len(cycles[0])==12
    assert sum(b.baseline_requests for b in cycles[0]) == 12


def test_stop_prevents_new_sidecars_without_erasing_existing_evidence():
    settings=enable();at=timezone.now();start=at-timedelta(hours=2)
    capture(start,start+timedelta(hours=1),[request(start+timedelta(minutes=1),id=1)])
    settings.enabled=False;settings.save()
    capture(start+timedelta(hours=1),at,[request(at-timedelta(minutes=1),id=2)])
    assert ResearchRequestComponents.objects.count()==1
