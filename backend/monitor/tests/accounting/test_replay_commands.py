import json
from io import StringIO
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from monitor.history_state import (
    LeaseBusyError,
    LeaseGuard,
)
from monitor.models import (
    AppSettings,
    Observation,
)
from monitor.replay import RATE_METHOD
from monitor.tests.helpers import (
    create_monitored_account,
    jwt_login,
)


@pytest.mark.django_db
def test_startup_replay_command_skips_current_algorithm_records():
    """容器重启不应重放已经由当前算法生成的稳定历史。"""

    now = timezone.now()
    observation = Observation.objects.create(
        account_id=7,
        source="manual",
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=3),
        attribution_started_at=now - timedelta(days=4),
        upstream_used_percent=Decimal("20"),
        interval_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("20"),
        sample_note="稳定结果哨兵",
        raw_window={"rate_method": RATE_METHOD},
    )
    output = StringIO()
    call_command("replayobservations", stdout=output)

    observation.refresh_from_db()
    assert observation.sample_note == "稳定结果哨兵"
    assert "派生结果已是最新版" in output.getvalue()


@pytest.mark.django_db
def test_startup_replay_command_upgrades_v6_zero_plateau():
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    first_observed_at = timezone.now().replace(microsecond=0)
    reset_at = first_observed_at + timedelta(days=7)
    observations = []
    for index, cost in enumerate(("100", "200", "300")):
        observed_at = first_observed_at + timedelta(minutes=10 * index)
        observations.append(
            Observation.objects.create(
                account_id=7,
                source="manual",
                observed_at=observed_at,
                window_seconds=604800,
                upstream_resets_at=reset_at,
                attribution_started_at=(
                    observed_at if index == 2 else None
                ),
                upstream_used_percent=Decimal("0"),
                raw_selected_total_cost=Decimal(cost),
                selected_total_cost=Decimal("0"),
                total_standard_cost=Decimal(cost),
                total_actual_cost=Decimal(cost),
                effective_usd_per_percent=Decimal("16"),
                raw_window={
                    "rate_method": "particle_filter_v6",
                    "replay_decision": (
                        "included"
                        if index == 2
                        else "deferred_zero_plateau"
                    ),
                },
            )
        )

    output = StringIO()
    call_command("replayobservations", stdout=output)

    for observation in observations:
        observation.refresh_from_db()
    assert all(
        observation.attribution_started_at == first_observed_at
        for observation in observations
    )
    assert [
        observation.selected_total_cost for observation in observations
    ] == [Decimal("0"), Decimal("100"), Decimal("200")]
    assert all(
        observation.raw_window["rate_method"] == RATE_METHOD
        for observation in observations
    )
    assert "重放 3 条观测" in output.getvalue()


@pytest.mark.django_db(transaction=True)
def test_manual_replay_writers_and_management_replay_respect_active_fence():
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    now = timezone.now()
    observation = Observation.objects.create(
        account_id=7,
        source="manual",
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=now + timedelta(days=3),
        upstream_used_percent=Decimal("10"),
        raw_selected_total_cost=Decimal("100"),
        selected_total_cost=Decimal("100"),
        total_standard_cost=Decimal("100"),
        total_actual_cost=Decimal("100"),
        effective_usd_per_percent=Decimal("10"),
        valid_sample=True,
    )
    client = Client()
    headers, _ = jwt_login(client)
    guard = LeaseGuard.acquire(7)
    try:
        responses = [
            client.post("/api/observations/rebuild", **headers),
            client.post(
                f"/api/observations/{observation.id}/exclude",
                data=json.dumps({"reason": "race"}),
                content_type="application/json",
                **headers,
            ),
            client.post(
                f"/api/observations/{observation.id}/restore",
                **headers,
            ),
            client.post(
                f"/api/observations/{observation.id}/manual-start",
                data=json.dumps({"reason": "race"}),
                content_type="application/json",
                **headers,
            ),
            client.delete(
                f"/api/observations/{observation.id}/manual-start",
                **headers,
            ),
        ]
        with pytest.raises(LeaseBusyError):
            call_command("replayobservations", "--all")
    finally:
        guard.release()

    assert [response.status_code for response in responses] == [409] * 5
    observation.refresh_from_db()
    assert observation.excluded_at is None
    assert observation.is_manual_start is False
