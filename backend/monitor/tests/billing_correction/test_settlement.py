"""Wallet settlement uses observed actual-to-corrected cost, never raw totals."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.utils import timezone

from monitor.billing_correction.settlement import balance_conversion_factor
from monitor.fast_correction.domain import aggregate_fast_logs
from monitor.fast_correction.persistence import apply_fast_interval
from monitor.fast_correction.prefix import FastCorrectionPrefix
from monitor.fast_correction.rules import FastCorrectionRuleSet
from monitor.models import AppSettings, Observation, ObservationFastCorrection
from monitor.tests.billing_correction.test_corrections import log
from monitor.tests.helpers import (
    create_monitored_account,
    create_participant,
    create_participant_snapshot,
)

D = Decimal
ZERO = D("0")
pytestmark = pytest.mark.django_db


def _config(basis: str) -> AppSettings:
    config = AppSettings.load()
    config.cost_basis = basis
    config.fast_correction_enabled = False
    config.long_context_correction_enabled = False
    config.model_correction_enabled = False
    return config


def _observation(
    *,
    at,
    started_at,
    actual,
    standard,
    selected=ZERO,
    resets_at=None,
) -> Observation:
    return Observation.objects.create(
        account_id=7,
        observed_at=at,
        window_seconds=604800,
        upstream_resets_at=resets_at or started_at + timedelta(days=7),
        attribution_started_at=started_at,
        upstream_used_percent=D("10"),
        interval_used_percent=D("10"),
        total_actual_cost=D(actual),
        total_standard_cost=D(standard),
        normalized_actual_cost=D(actual),
        normalized_standard_cost=D(standard),
        raw_selected_total_cost=D(selected),
        selected_total_cost=D(selected),
        effective_usd_per_percent=D("10"),
    )


def _capture(
    observation: Observation,
    config: AppSettings,
    *,
    started_at,
    logs,
) -> None:
    interval = aggregate_fast_logs(
        logs,
        started_at=started_at,
        ended_at=observation.observed_at,
        rules=FastCorrectionRuleSet(config.fast_correction_rules),
    )
    apply_fast_interval(observation, interval)
    observation.save()


def _snapshot(observation, participant, selected) -> object:
    return create_participant_snapshot(
        observation=observation,
        participant=participant,
        raw_selected_cost=D(selected),
        selected_cost=D(selected),
    )


def test_personal_mix_and_new_participant_account_fallback():
    config = _config("standard")
    account = create_monitored_account()
    first = create_participant(
        name="first", sub2api_user_id=11, account=account, share_percent=40
    )
    second = create_participant(
        name="second", sub2api_user_id=12, account=account, share_percent=40
    )
    newcomer = create_participant(
        name="new", sub2api_user_id=13, account=account, share_percent=20
    )
    at = timezone.now().replace(microsecond=0)
    started_at = at - timedelta(days=1)
    observation = _observation(
        at=at, started_at=started_at, actual="140", standard="200"
    )
    _capture(
        observation,
        config,
        started_at=started_at,
        logs=[
            log(
                id=1,
                user_id=11,
                created_at=at - timedelta(minutes=2),
                service_tier="default",
                model="plain",
                actual_cost=D("50"),
                total_cost=D("100"),
            ),
            log(
                id=2,
                user_id=12,
                created_at=at - timedelta(minutes=1),
                service_tier="default",
                model="plain",
                actual_cost=D("90"),
                total_cost=D("100"),
            ),
        ],
    )
    prefix = FastCorrectionPrefix(7, "standard", config)
    first_snapshot = _snapshot(observation, first, "100")
    second_snapshot = _snapshot(observation, second, "100")
    newcomer_snapshot = _snapshot(observation, newcomer, "0")
    first.sub2api_user_id = 111
    first.save(update_fields=["sub2api_user_id"])

    assert balance_conversion_factor(
        first_snapshot,
        config,
        correction_prefix=prefix,
    ) == D("0.5")
    assert balance_conversion_factor(
        second_snapshot,
        config,
        correction_prefix=prefix,
    ) == D("0.9")
    assert balance_conversion_factor(
        newcomer_snapshot,
        config,
        correction_prefix=prefix,
    ) == D("0.7")


def test_current_rules_and_observation_cutoff_reprice_request_samples():
    config = _config("actual")
    config.model_correction_enabled = True
    config.model_correction_rules = [
        {"model_pattern": "discounted", "multiplier": "0.5"}
    ]
    account = create_monitored_account()
    participant = create_participant(
        name="rider", sub2api_user_id=21, account=account, share_percent=100
    )
    started_at = timezone.now().replace(microsecond=0) - timedelta(days=1)
    first_at = started_at + timedelta(hours=1)
    first = _observation(
        at=first_at, started_at=started_at, actual="100", standard="100"
    )
    _capture(
        first,
        config,
        started_at=started_at,
        logs=[
            log(
                id=1,
                user_id=21,
                created_at=first_at - timedelta(minutes=1),
                service_tier="default",
                model="discounted",
                actual_cost=D("100"),
                total_cost=D("100"),
            )
        ],
    )
    second_at = first_at + timedelta(hours=1)
    second = _observation(
        at=second_at, started_at=started_at, actual="200", standard="200"
    )
    _capture(
        second,
        config,
        started_at=first_at,
        logs=[
            log(
                id=2,
                user_id=21,
                created_at=second_at - timedelta(minutes=1),
                service_tier="default",
                model="plain",
                actual_cost=D("100"),
                total_cost=D("100"),
            )
        ],
    )
    prefix = FastCorrectionPrefix(7, "actual", config)
    first_snapshot = _snapshot(first, participant, "50")
    second_snapshot = _snapshot(second, participant, "150")

    assert balance_conversion_factor(
        first_snapshot, config, correction_prefix=prefix
    ) == D("2")
    assert balance_conversion_factor(
        second_snapshot, config, correction_prefix=prefix
    ) == D("200") / D("150")

    config.model_correction_rules = [
        {"model_pattern": "discounted", "multiplier": "0.25"}
    ]
    assert balance_conversion_factor(first_snapshot, config) == D("4")


def test_request_samples_do_not_cross_manual_attribution_start():
    config = _config("standard")
    account = create_monitored_account()
    participant = create_participant(
        name="manual rider",
        sub2api_user_id=31,
        account=account,
        share_percent=100,
    )
    official_start = timezone.now().replace(microsecond=0) - timedelta(days=2)
    manual_at = official_start + timedelta(days=1)
    baseline = _observation(
        at=manual_at,
        started_at=official_start,
        actual="10",
        standard="100",
    )
    _capture(
        baseline,
        config,
        started_at=official_start,
        logs=[
            log(
                id=1,
                user_id=31,
                created_at=manual_at - timedelta(minutes=1),
                service_tier="default",
                model="plain",
                actual_cost=D("10"),
                total_cost=D("100"),
            )
        ],
    )
    target_at = manual_at + timedelta(hours=1)
    target = _observation(
        at=target_at,
        started_at=manual_at,
        actual="90",
        standard="200",
        resets_at=baseline.upstream_resets_at,
    )
    _capture(
        target,
        config,
        started_at=manual_at,
        logs=[
            log(
                id=2,
                user_id=31,
                created_at=target_at - timedelta(minutes=1),
                service_tier="default",
                model="plain",
                actual_cost=D("80"),
                total_cost=D("100"),
            )
        ],
    )
    baseline.is_manual_start = True
    baseline.manual_start_end = target
    baseline.save(update_fields=["is_manual_start", "manual_start_end"])

    assert balance_conversion_factor(
        _snapshot(target, participant, "100"), config
    ) == D("0.8")


@pytest.mark.parametrize(
    "correction,selected,expected",
    [("25", "125", "0.8"), ("-50", "50", "2")],
)
def test_legacy_actual_recovers_raw_cost_from_aligned_correction(
    correction, selected, expected
):
    config = _config("actual")
    account = create_monitored_account()
    participant = create_participant(
        name="legacy rider",
        sub2api_user_id=41,
        account=account,
        share_percent=100,
    )
    at = timezone.now().replace(microsecond=0)
    started_at = at - timedelta(days=1)
    observation = _observation(
        at=at,
        started_at=started_at,
        actual="100",
        standard="200",
        selected=selected,
    )
    observation.fast_correction_started_at = started_at
    observation.fast_correction_request_count = 1
    observation.fast_correction_actual_cost = D(correction)
    observation.fast_correction_standard_cost = D(correction)
    observation.save()
    ObservationFastCorrection.objects.create(
        observation=observation,
        sub2api_user_id=41,
        request_count=1,
        fast_request_count=1,
        fast_actual_cost=D("100"),
        fast_standard_cost=D("200"),
        actual_correction_cost=D(correction),
        standard_correction_cost=D(correction),
    )
    snapshot = _snapshot(observation, participant, selected)

    assert balance_conversion_factor(snapshot, config) == D(expected)
    snapshot.selected_cost = D("999")
    assert balance_conversion_factor(
        snapshot, config, selected_cost=D(selected)
    ) == D(expected)


def test_legacy_standard_uses_aligned_account_actual_to_corrected_ratio():
    config = _config("standard")
    account = create_monitored_account()
    participant = create_participant(
        name="legacy standard rider",
        sub2api_user_id=51,
        account=account,
        share_percent=100,
    )
    manual_at = timezone.now().replace(microsecond=0) - timedelta(hours=1)
    baseline = _observation(
        at=manual_at,
        started_at=manual_at - timedelta(days=1),
        actual="700",
        standard="1000",
    )
    target_at = manual_at + timedelta(hours=1)
    target = _observation(
        at=target_at,
        started_at=manual_at,
        actual="780",
        standard="1100",
        selected="125",
        resets_at=baseline.upstream_resets_at,
    )
    target.fast_correction_started_at = manual_at
    target.fast_correction_request_count = 1
    target.fast_correction_actual_cost = D("20")
    target.fast_correction_standard_cost = D("25")
    target.save()
    ObservationFastCorrection.objects.create(
        observation=target,
        sub2api_user_id=51,
        request_count=1,
        fast_request_count=1,
        fast_actual_cost=D("80"),
        fast_standard_cost=D("100"),
        actual_correction_cost=D("20"),
        standard_correction_cost=D("25"),
    )
    baseline.is_manual_start = True
    baseline.manual_start_end = target
    baseline.save(update_fields=["is_manual_start", "manual_start_end"])

    assert balance_conversion_factor(
        _snapshot(target, participant, "125"), config
    ) == D("0.64")


def test_zero_cost_start_and_cpa_use_identity_factor():
    config = _config("standard")
    account = create_monitored_account()
    participant = create_participant(
        name="zero rider",
        sub2api_user_id=61,
        account=account,
        share_percent=100,
    )
    at = timezone.now().replace(microsecond=0)
    observation = _observation(
        at=at,
        started_at=at - timedelta(days=1),
        actual=ZERO,
        standard=ZERO,
    )

    assert balance_conversion_factor(
        _snapshot(observation, participant, ZERO), config
    ) == D("1")
    cpa_snapshot = SimpleNamespace(
        observation=SimpleNamespace(account_id=-1)
    )
    assert balance_conversion_factor(cpa_snapshot, config) == D("1")
