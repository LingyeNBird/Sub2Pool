from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.models import AppSettings, Observation, Participant, ParticipantSnapshot
from monitor.replay import rebuild_account
from monitor.tests.helpers import jwt_login


def _raw_observation(
    *,
    participant_costs: dict[Participant, Decimal],
    observed_at,
    reset_at,
    used_percent: Decimal,
    total_cost: Decimal,
) -> Observation:
    observation = Observation.objects.create(
        account_id=7,
        source="manual",
        observed_at=observed_at,
        window_seconds=604800,
        upstream_resets_at=reset_at,
        upstream_used_percent=used_percent,
        raw_selected_total_cost=total_cost,
        selected_total_cost=total_cost,
        total_standard_cost=total_cost,
        total_actual_cost=total_cost,
        effective_usd_per_percent=Decimal("20"),
    )
    ParticipantSnapshot.objects.bulk_create(
        [
            ParticipantSnapshot(
                observation=observation,
                participant=participant,
                raw_selected_cost=cost,
                selected_cost=cost,
                current_balance_usd=Decimal("1000"),
            )
            for participant, cost in participant_costs.items()
        ]
    )
    return observation


@pytest.mark.django_db
def test_replay_is_idempotent_preserves_raw_facts_and_model_intervals():
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save(update_fields=["openai_account_id"])
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=50,
        is_owner=True,
    )
    rider = Participant.objects.create(
        name="车友",
        sub2api_user_id=2,
        share_percent=50,
    )
    now = timezone.now().replace(microsecond=0)
    reset_at = now + timedelta(days=4)
    first = _raw_observation(
        participant_costs={owner: Decimal("150"), rider: Decimal("50")},
        observed_at=now,
        reset_at=reset_at,
        used_percent=Decimal("10"),
        total_cost=Decimal("200"),
    )
    second = _raw_observation(
        participant_costs={owner: Decimal("250"), rider: Decimal("150")},
        observed_at=now + timedelta(hours=1),
        reset_at=reset_at,
        used_percent=Decimal("20"),
        total_cost=Decimal("400"),
    )
    raw_before = list(
        Observation.objects.order_by("observed_at", "id").values_list(
            "raw_selected_total_cost",
            flat=True,
        )
    )

    rebuild_account(7, config)

    def derived_state():
        return list(
            ParticipantSnapshot.objects.order_by(
                "observation__observed_at",
                "participant_id",
            ).values_list(
                "observation_id",
                "participant_id",
                "charged_delta_percent",
                "charged_cycle_percent",
                "remaining_share_percent",
            )
        )

    first_state = derived_state()
    for observation in (first, second):
        observation.refresh_from_db()
        snapshots = list(observation.participant_snapshots.all())
        assert observation.raw_window["rate_method"] == "particle_filter_v4"
        assert observation.model_diagnostics["algorithm"] == "particle_filter_v4"
        assert observation.capacity_lower_usd <= (
            observation.effective_usd_per_percent * Decimal("100")
        )
        assert (
            observation.effective_usd_per_percent * Decimal("100")
            <= observation.capacity_upper_usd
        )
        assert all(
            snapshot.charged_percent_lower
            <= snapshot.charged_cycle_percent
            <= snapshot.charged_percent_upper
            for snapshot in snapshots
        )
    rebuild_account(7, config)

    assert derived_state() == first_state
    assert list(
        Observation.objects.order_by("observed_at", "id").values_list(
            "raw_selected_total_cost",
            flat=True,
        )
    ) == raw_before

@pytest.mark.django_db
def test_staged_expansion_is_persisted_in_observation_diagnostics():
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save(update_fields=["openai_account_id"])
    participant = Participant.objects.create(
        name="高容量参与者",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
    now = datetime(2026, 8, 11, 12, tzinfo=dt_timezone.utc)
    reset_at = now + timedelta(days=7)
    _raw_observation(
        participant_costs={participant: Decimal("0")},
        observed_at=now,
        reset_at=reset_at,
        used_percent=Decimal("0"),
        total_cost=Decimal("0"),
    )
    latest = _raw_observation(
        participant_costs={participant: Decimal("800")},
        observed_at=now + timedelta(hours=12),
        reset_at=reset_at,
        used_percent=Decimal("10"),
        total_cost=Decimal("800"),
    )

    rebuild_account(7, config)

    latest.refresh_from_db()
    diagnostics = latest.model_diagnostics
    assert diagnostics["capacity_range_usd"] == [1400.0, 10000.0]
    assert diagnostics["capacity_range_stage"] == 2
    assert diagnostics["capacity_range_direction"] == "upper"
    assert [
        item["to_range_usd"] for item in diagnostics["capacity_range_promotions"]
    ] == [[1400.0, 6000.0], [1400.0, 10000.0]]


@pytest.mark.django_db
def test_new_cycle_uses_previous_cycle_capacity_as_soft_prior():
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save(update_fields=["openai_account_id"])
    owner = Participant.objects.create(
        name="车主",
        sub2api_user_id=1,
        share_percent=100,
        is_owner=True,
    )
    now = timezone.now().replace(microsecond=0)
    first_reset = now - timedelta(days=6)
    second_reset = now + timedelta(days=6)
    first_cycle = [
        _raw_observation(
            participant_costs={owner: Decimal("0")},
            observed_at=now - timedelta(days=13),
            reset_at=first_reset,
            used_percent=Decimal("0"),
            total_cost=Decimal("0"),
        ),
        _raw_observation(
            participant_costs={owner: Decimal("360")},
            observed_at=now - timedelta(days=7),
            reset_at=first_reset,
            used_percent=Decimal("20"),
            total_cost=Decimal("360"),
        ),
    ]
    second_cycle = [
        _raw_observation(
            participant_costs={owner: Decimal("0")},
            observed_at=now - timedelta(days=1),
            reset_at=second_reset,
            used_percent=Decimal("0"),
            total_cost=Decimal("0"),
        ),
        _raw_observation(
            participant_costs={owner: Decimal("180")},
            observed_at=now,
            reset_at=second_reset,
            used_percent=Decimal("10"),
            total_cost=Decimal("180"),
        ),
    ]

    rebuild_account(7, config)

    first_cycle[-1].refresh_from_db()
    for observation in second_cycle:
        observation.refresh_from_db()
        assert observation.model_diagnostics["prior_capacity_usd"] == pytest.approx(
            float(first_cycle[-1].effective_usd_per_percent * Decimal("100"))
        )


@pytest.mark.django_db
def test_quota_model_switch_changes_projection_without_rewriting_snapshot():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.weekly_quota_model = "constant_average"
    config.save(update_fields=["openai_account_id", "weekly_quota_model"])
    participant = Participant.objects.create(
        name="车友",
        sub2api_user_id=51,
        share_percent=50,
    )
    now = timezone.now()
    observation = Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=4),
        attribution_started_at=now - timedelta(days=3),
        upstream_used_percent=Decimal("20"),
        interval_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("15"),
    )
    snapshot = ParticipantSnapshot.objects.create(
        observation=observation,
        participant=participant,
        raw_selected_cost=Decimal("100"),
        selected_cost=Decimal("100"),
        charged_cycle_percent=Decimal("12"),
        remaining_share_percent=Decimal("38"),
        current_balance_usd=Decimal("80"),
        recommended_balance_usd=Decimal("722"),
        needs_manual_update=True,
    )
    client = Client()
    headers, _ = jwt_login(client)

    constant = client.get("/api/participants", **headers).json()["data"][0]
    config.weekly_quota_model = "time_varying"
    config.save(update_fields=["weekly_quota_model"])
    varying = client.get("/api/participants", **headers).json()["data"][0]

    assert constant["snapshot"]["allocation_model"] == "constant_average"
    assert constant["snapshot"]["charged_cycle_percent"] == 5.0
    assert varying["snapshot"]["allocation_model"] == "time_varying"
    assert varying["snapshot"]["charged_cycle_percent"] == 12.0
    snapshot.refresh_from_db()
    assert snapshot.charged_cycle_percent == Decimal("12")
    assert snapshot.remaining_share_percent == Decimal("38")
    assert snapshot.recommended_balance_usd == Decimal("722")
