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
def test_particle_trajectory_requires_admin():
    get_user_model().objects.create_user(
        username="viewer",
        password="very-strong-password",
    )
    client = Client()
    headers, _ = jwt_login(client, username="viewer")

    response = client.get("/api/particle-trajectory", **headers)

    assert response.status_code == 403


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
