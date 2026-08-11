from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.models import AppSettings, Observation
from monitor.replay import rebuild_account
from monitor.tests.helpers import jwt_login


@pytest.mark.django_db
def test_particle_trajectory_reruns_current_segment_without_writes():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save(update_fields=["openai_account_id"])

    started_at = timezone.now() - timedelta(hours=12)
    resets_at = started_at + timedelta(days=7)
    first = Observation.objects.create(
        account_id=7,
        source="scheduled",
        observed_at=started_at,
        window_seconds=604800,
        upstream_resets_at=resets_at,
        upstream_used_percent=Decimal("0"),
        raw_selected_total_cost=Decimal("10"),
        selected_total_cost=Decimal("10"),
        total_standard_cost=Decimal("10"),
        total_actual_cost=Decimal("10"),
        effective_usd_per_percent=Decimal("16"),
    )
    second = Observation.objects.create(
        account_id=7,
        source="scheduled",
        observed_at=started_at + timedelta(hours=12),
        window_seconds=604800,
        upstream_resets_at=resets_at,
        upstream_used_percent=Decimal("10"),
        raw_selected_total_cost=Decimal("190"),
        selected_total_cost=Decimal("190"),
        total_standard_cost=Decimal("190"),
        total_actual_cost=Decimal("190"),
        effective_usd_per_percent=Decimal("16"),
    )
    rebuild_account(7, config)
    stored_before = list(
        Observation.objects.order_by("id").values(
            "id",
            "attribution_started_at",
            "effective_usd_per_percent",
            "model_diagnostics",
        )
    )

    client = Client()
    headers, _ = jwt_login(client)
    response = client.get("/api/particle-trajectory", **headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is True
    assert data["algorithm"] == "particle_filter_v4"
    assert data["particle_count"] == 480
    assert data["representative_particle_count"] == 96
    assert data["segment"]["observation_count"] == 2
    assert data["selected_period_id"] == first.id
    assert data["periods"] == [
        {
            "id": first.id,
            "sequence": 1,
            "started_at": started_at.isoformat(),
            "first_observed_at": first.observed_at.isoformat(),
            "last_observed_at": second.observed_at.isoformat(),
            "resets_at": resets_at.isoformat(),
            "ended_at": resets_at.isoformat(),
            "observation_count": 2,
            "is_current": True,
        }
    ]
    assert [point["observation_id"] for point in data["points"]] == [
        first.id,
        second.id,
    ]
    for point in data["points"]:
        assert len(point["particles_usd"]) == 96
        assert min(point["particles_usd"]) >= point["range_min_usd"]
        assert max(point["particles_usd"]) <= point["range_max_usd"]
        assert point["capacity_lower_usd"] <= point["capacity_usd"]
        assert point["capacity_usd"] <= point["capacity_upper_usd"]
    assert list(
        Observation.objects.order_by("id").values(
            "id",
            "attribution_started_at",
            "effective_usd_per_percent",
            "model_diagnostics",
        )
    ) == stored_before


@pytest.mark.django_db
def test_particle_trajectory_selects_historical_period():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save(update_fields=["openai_account_id"])

    now = timezone.now()
    old_start = now - timedelta(days=14)
    current_start = now - timedelta(days=4)

    def create_observation(
        observed_at,
        resets_at,
        used_percent,
        cost,
    ):
        return Observation.objects.create(
            account_id=7,
            source="scheduled",
            observed_at=observed_at,
            window_seconds=604800,
            upstream_resets_at=resets_at,
            upstream_used_percent=Decimal(used_percent),
            raw_selected_total_cost=Decimal(cost),
            selected_total_cost=Decimal(cost),
            total_standard_cost=Decimal(cost),
            total_actual_cost=Decimal(cost),
            effective_usd_per_percent=Decimal("16"),
        )

    old_first = create_observation(
        old_start,
        old_start + timedelta(days=7),
        "0",
        "0",
    )
    old_second = create_observation(
        old_start + timedelta(days=1),
        old_start + timedelta(days=7),
        "10",
        "180",
    )
    current_first = create_observation(
        current_start,
        current_start + timedelta(days=7),
        "0",
        "200",
    )
    current_second = create_observation(
        current_start + timedelta(days=1),
        current_start + timedelta(days=7),
        "8",
        "360",
    )
    rebuild_account(7, config)

    client = Client()
    headers, _ = jwt_login(client)
    current_response = client.get("/api/particle-trajectory", **headers)

    assert current_response.status_code == 200
    current_data = current_response.json()["data"]
    assert [period["sequence"] for period in current_data["periods"]] == [1, 2]
    assert current_data["selected_period_id"] == current_first.id
    assert [point["observation_id"] for point in current_data["points"]] == [
        current_first.id,
        current_second.id,
    ]

    historical_response = client.get(
        f"/api/particle-trajectory?period={old_first.id}",
        **headers,
    )

    assert historical_response.status_code == 200
    historical_data = historical_response.json()["data"]
    assert historical_data["selected_period_id"] == old_first.id
    assert historical_data["periods"][0]["is_current"] is False
    assert historical_data["periods"][1]["is_current"] is True
    assert [point["observation_id"] for point in historical_data["points"]] == [
        old_first.id,
        old_second.id,
    ]

    invalid_response = client.get(
        "/api/particle-trajectory?period=999999",
        **headers,
    )
    assert invalid_response.status_code == 400
    assert invalid_response.json()["message"] == "所选历史周期不存在"


@pytest.mark.django_db
def test_particle_trajectory_periods_end_at_the_next_segment_boundary():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    config.openai_account_id = 7
    config.save(update_fields=["openai_account_id"])

    first_start = (timezone.now() - timedelta(days=21)).replace(microsecond=0)
    raw_windows = [
        (first_start, first_start + timedelta(days=7)),
        (first_start + timedelta(days=5), first_start + timedelta(days=12)),
        (first_start + timedelta(days=7), first_start + timedelta(days=14)),
    ]
    for index, (observed_at, resets_at) in enumerate(raw_windows):
        Observation.objects.create(
            account_id=7,
            source="scheduled",
            observed_at=observed_at,
            window_seconds=604800,
            upstream_resets_at=resets_at,
            upstream_used_percent=Decimal("0"),
            raw_selected_total_cost=Decimal(index * 100),
            selected_total_cost=Decimal(index * 100),
            total_standard_cost=Decimal(index * 100),
            total_actual_cost=Decimal(index * 100),
            effective_usd_per_percent=Decimal("16"),
        )
    rebuild_account(7, config)

    client = Client()
    headers, _ = jwt_login(client)
    response = client.get("/api/particle-trajectory", **headers)

    assert response.status_code == 200
    periods = response.json()["data"]["periods"]
    assert [period["started_at"] for period in periods] == [
        started_at.isoformat() for started_at, _ in raw_windows
    ]
    assert [period["resets_at"] for period in periods] == [
        resets_at.isoformat() for _, resets_at in raw_windows
    ]
    assert [period["ended_at"] for period in periods] == [
        raw_windows[1][0].isoformat(),
        raw_windows[2][0].isoformat(),
        raw_windows[2][1].isoformat(),
    ]


@pytest.mark.django_db
def test_particle_trajectory_allows_authenticated_system_user():
    get_user_model().objects.create_user(
        username="viewer",
        password="very-strong-password",
    )
    client = Client()
    headers, _ = jwt_login(client, username="viewer")

    response = client.get("/api/particle-trajectory", **headers)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "available": False,
        "message": "尚未配置 OpenAI 上游账号",
    }


@pytest.mark.django_db
def test_particle_trajectory_requires_authentication():
    response = Client().get("/api/particle-trajectory")

    assert response.status_code == 401


@pytest.mark.django_db
def test_particle_trajectory_reports_unavailable_without_account():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client)

    response = client.get("/api/particle-trajectory", **headers)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "available": False,
        "message": "尚未配置 OpenAI 上游账号",
    }
