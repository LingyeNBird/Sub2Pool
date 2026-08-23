import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from monitor.models import (
    AppSettings,
    Observation,
    ParticipantSnapshot,
)
from monitor.replay import (
    RATE_METHOD,
    exclude_observation,
    rebuild_account,
)
from monitor.tests.helpers import (
    create_monitored_account,
    create_participant,
    create_participant_snapshot,
    jwt_login,
)


@pytest.mark.django_db
def test_manual_start_interval_keeps_protected_observations_in_one_cycle():
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    now = timezone.now().replace(microsecond=0)
    resets = [
        now + timedelta(days=7, minutes=index)
        for index in range(4)
    ]
    percents = [
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("1"),
        Decimal("2"),
    ]
    observations = [
        Observation.objects.create(
            account_id=7,
            source="manual",
            observed_at=now + timedelta(minutes=index),
            window_seconds=604800,
            upstream_resets_at=resets[min(index, 3)],
            upstream_used_percent=percent,
            raw_selected_total_cost=Decimal(index * 20),
            selected_total_cost=Decimal(index * 20),
            total_standard_cost=Decimal(index * 20),
            total_actual_cost=Decimal(index * 20),
            effective_usd_per_percent=Decimal("20"),
        )
        for index, percent in enumerate(percents)
    ]
    start, _middle, nested_start, end, _after = observations
    start.is_manual_start = True
    start.manual_start_end = end
    start.save(update_fields=["is_manual_start", "manual_start_end"])
    nested_start.is_manual_start = True
    nested_start.manual_start_end = nested_start
    nested_start.save(update_fields=["is_manual_start", "manual_start_end"])

    result = rebuild_account(7, config)

    assert result.inferred_intervals == 1
    replayed = list(Observation.objects.order_by("observed_at", "id"))
    assert all(item.excluded_at is None for item in replayed)
    assert all(item.attribution_started_at == start.observed_at for item in replayed)
    assert [item.interval_used_percent for item in replayed] == percents


@pytest.mark.django_db
def test_manual_start_interval_api_validates_and_merges_existing_starts():
    get_user_model().objects.create_superuser(
        username="interval-owner",
        password="very-strong-password",
        email="interval-owner@example.com",
    )
    client = Client()
    headers, _ = jwt_login(client, username="interval-owner")
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    now = timezone.now().replace(microsecond=0)

    def raw_observation(index: int, *, account_id: int = 7) -> Observation:
        return Observation.objects.create(
            account_id=account_id,
            source="manual",
            observed_at=now + timedelta(minutes=index),
            window_seconds=604800,
            upstream_resets_at=now + timedelta(days=7),
            upstream_used_percent=Decimal(index),
            raw_selected_total_cost=Decimal(index * 20),
            selected_total_cost=Decimal(index * 20),
            total_standard_cost=Decimal(index * 20),
            total_actual_cost=Decimal(index * 20),
            effective_usd_per_percent=Decimal("20"),
        )

    start = raw_observation(0)
    nested = raw_observation(1)
    end = raw_observation(2)
    later = raw_observation(3)
    other_account = raw_observation(4, account_id=8)

    legacy_shape = client.post(
        f"/api/observations/{nested.id}/manual-start",
        data=json.dumps({"reason": "原单点起点"}),
        content_type="application/json",
        **headers,
    )
    assert legacy_shape.status_code == 200
    nested.refresh_from_db()
    assert nested.manual_start_end_id == nested.id

    merged = client.post(
        f"/api/observations/{start.id}/manual-start",
        data=json.dumps(
            {
                "end_observation_id": end.id,
                "reason": "覆盖 0% 到首次消费",
            }
        ),
        content_type="application/json",
        **headers,
    )
    assert merged.status_code == 200
    assert merged.json()["data"]["absorbed_manual_starts"] == 1
    start.refresh_from_db()
    nested.refresh_from_db()
    assert start.manual_start_end_id == end.id
    assert nested.is_manual_start is False
    assert nested.manual_start_end_id is None

    listed = client.get("/api/observations", **headers).json()["data"]["items"]
    listed_start = next(item for item in listed if item["id"] == start.id)
    assert listed_start["manual_start_end_id"] == end.id
    assert (
        listed_start["manual_start_end_observed_at"]
        == end.observed_at.isoformat()
    )

    same_record = client.post(
        f"/api/observations/{later.id}/manual-start",
        data=json.dumps({"end_observation_id": later.id}),
        content_type="application/json",
        **headers,
    )
    assert same_record.status_code == 200
    later.refresh_from_db()
    assert later.manual_start_end_id == later.id

    partial_overlap = client.post(
        f"/api/observations/{nested.id}/manual-start",
        data=json.dumps({"end_observation_id": later.id}),
        content_type="application/json",
        **headers,
    )
    assert partial_overlap.status_code == 400
    assert "部分重叠" in partial_overlap.json()["message"]
    start.refresh_from_db()
    later.refresh_from_db()
    assert start.manual_start_end_id == end.id
    assert later.manual_start_end_id == later.id

    reversed_range = client.post(
        f"/api/observations/{end.id}/manual-start",
        data=json.dumps({"end_observation_id": start.id}),
        content_type="application/json",
        **headers,
    )
    assert reversed_range.status_code == 400
    assert "不能早于起点" in reversed_range.json()["message"]

    cross_account = client.post(
        f"/api/observations/{start.id}/manual-start",
        data=json.dumps({"end_observation_id": other_account.id}),
        content_type="application/json",
        **headers,
    )
    assert cross_account.status_code == 400
    assert "不能跨账号" in cross_account.json()["message"]


@pytest.mark.django_db
def test_exclusion_restore_and_manual_start_cancellation_replay_affected_suffix():
    """回退点可恢复为管理员起点，也可取消后重新由异常检测排除。"""
    get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    participant = create_participant(name="车主",
    sub2api_user_id=1,
    share_percent=100,
    is_owner=True,)
    now = timezone.now()
    reset_at = now + timedelta(days=3)

    def raw_observation(minutes_ago, percent_value, cost_value, source):
        observed_at = now - timedelta(minutes=minutes_ago)
        observation = Observation.objects.create(
            account_id=7,
            source=source,
            observed_at=observed_at,
            window_seconds=604800,
            upstream_resets_at=reset_at,
            attribution_started_at=reset_at - timedelta(days=7),
            upstream_used_percent=percent_value,
            raw_selected_total_cost=cost_value,
            selected_total_cost=cost_value,
            total_standard_cost=cost_value,
            total_actual_cost=cost_value,
            sample_usd_per_percent=(
                cost_value / percent_value if percent_value else None
            ),
            effective_usd_per_percent=Decimal("20"),
            valid_sample=percent_value > 0,
            raw_window={
                "rate_method": RATE_METHOD,
                "sampled_at": observed_at.isoformat(),
            },
        )
        create_participant_snapshot(observation=observation,
        participant=participant,
        raw_selected_cost=cost_value,
        selected_cost=cost_value,
        charged_delta_percent=percent_value,
        charged_cycle_percent=percent_value,
        remaining_share_percent=Decimal("100") - percent_value,
        current_balance_usd=Decimal("1000"),
        recommended_balance_usd=Decimal("1000"),)
        return observation

    raw_observation(120, Decimal("47"), Decimal("940"), "manual")
    false_reset = raw_observation(60, Decimal("18"), Decimal("960"), "reset")
    raw_observation(40, Decimal("49"), Decimal("980"), "manual")
    raw_observation(20, Decimal("50"), Decimal("1000"), "manual")

    client = Client()
    headers, _ = jwt_login(client)
    response = client.post(
        f"/api/observations/{false_reset.id}/exclude",
        data=json.dumps({"reason": "异常的 18% 快照"}),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    assert response.json()["data"]["rebuilt_observations"] == 3
    false_reset.refresh_from_db()
    assert false_reset.excluded_at is not None
    assert false_reset.exclusion_source == "manual"

    included = list(
        Observation.objects.filter(excluded_at__isnull=True).order_by(
            "observed_at",
            "id",
        )
    )
    assert [item.upstream_used_percent for item in included] == [
        Decimal("47"),
        Decimal("49"),
        Decimal("50"),
    ]
    assert [item.selected_total_cost for item in included] == [
        Decimal("940"),
        Decimal("980"),
        Decimal("1000"),
    ]
    assert included[-1].delta_percent == Decimal("1")
    assert included[-1].delta_cost == Decimal("20")
    latest_snapshot = ParticipantSnapshot.objects.get(
        observation=included[-1],
        participant=participant,
    )
    assert (
        latest_snapshot.charged_percent_lower
        <= latest_snapshot.charged_cycle_percent
        <= latest_snapshot.charged_percent_upper
    )

    listed = client.get("/api/observations", **headers).json()["data"]
    assert listed["summary"]["excluded_count"] == 1
    dashboard = client.get("/api/dashboard", **headers).json()["data"]
    assert dashboard["cycle"]["upstream_used_percent"] == 50.0
    assert dashboard["cycle"]["selected_total_cost"] == 1000.0

    restored = client.post(
        f"/api/observations/{false_reset.id}/restore",
        **headers,
    )
    assert restored.status_code == 200
    assert restored.json()["data"]["included"] is True
    assert restored.json()["data"]["inferred_intervals"] == 1
    false_reset.refresh_from_db()
    assert false_reset.excluded_at is None
    assert false_reset.is_manual_start is True
    assert false_reset.interval_used_percent == Decimal("0")
    assert false_reset.selected_total_cost == Decimal("0")
    assert Observation.objects.filter(excluded_at__isnull=True).count() == 4

    latest = Observation.objects.order_by("-observed_at", "-id").first()
    assert latest.attribution_started_at == false_reset.observed_at
    assert latest.interval_used_percent == Decimal("32")
    assert latest.selected_total_cost == Decimal("40")
    latest_snapshot = ParticipantSnapshot.objects.get(
        observation=latest,
        participant=participant,
    )
    assert (
        latest_snapshot.charged_percent_lower
        <= latest_snapshot.charged_cycle_percent
        <= latest_snapshot.charged_percent_upper
    )

    cleared = client.delete(
        f"/api/observations/{false_reset.id}/manual-start",
        **headers,
    )
    assert cleared.status_code == 200
    assert cleared.json()["data"]["rebuilt_observations"] == 3
    false_reset.refresh_from_db()
    assert false_reset.is_manual_start is False
    assert false_reset.exclusion_source == "automatic"
    assert false_reset.excluded_at is not None


@pytest.mark.django_db
def test_rebuild_api_recomputes_current_interval_without_changing_raw_samples():
    """管理员可只重建派生字段；错误点排除后，后续增量重新衔接上一有效点。"""

    get_user_model().objects.create_superuser(
        username="rebuild-owner",
        password="very-strong-password",
        email="rebuild-owner@example.com",
    )
    config = AppSettings.load()
    create_monitored_account(7)
    config.save()
    participant = create_participant(name="车主",
    sub2api_user_id=1,
    share_percent=100,
    is_owner=True,)
    now = timezone.now()
    reset_at = now + timedelta(days=3)

    def raw_observation(minutes_ago, percent_value, cost_value):
        observation = Observation.objects.create(
            account_id=7,
            source="manual",
            observed_at=now - timedelta(minutes=minutes_ago),
            window_seconds=604800,
            upstream_resets_at=reset_at,
            upstream_used_percent=percent_value,
            raw_selected_total_cost=cost_value,
            selected_total_cost=cost_value,
            total_standard_cost=cost_value,
            total_actual_cost=cost_value,
            effective_usd_per_percent=Decimal("20"),
            raw_window={"rate_method": RATE_METHOD},
        )
        create_participant_snapshot(observation=observation,
        participant=participant,
        raw_selected_cost=cost_value,
        selected_cost=cost_value,
        current_balance_usd=Decimal("1000"),
        remaining_share_percent=Decimal("100"),)
        return observation

    first = raw_observation(60, Decimal("47"), Decimal("940"))
    false_reset = raw_observation(40, Decimal("18"), Decimal("960"))
    recovered = raw_observation(20, Decimal("49"), Decimal("980"))
    rebuild_account(7, config)
    exclude_observation(false_reset, "异常的 18% 快照")

    recovered.refresh_from_db()
    assert recovered.delta_percent == Decimal("2")
    assert recovered.delta_cost == Decimal("40")
    raw_totals_before = list(
        Observation.objects.order_by("observed_at", "id").values_list(
            "raw_selected_total_cost",
            flat=True,
        )
    )

    recovered.selected_total_cost = Decimal("180")
    recovered.delta_percent = Decimal("-29")
    recovered.delta_cost = Decimal("-800")
    recovered.sample_usd_per_percent = Decimal("0.2")
    recovered.save(
        update_fields=[
            "selected_total_cost",
            "delta_percent",
            "delta_cost",
            "sample_usd_per_percent",
        ]
    )
    recovered_snapshot = ParticipantSnapshot.objects.get(
        observation=recovered,
        participant=participant,
    )
    recovered_snapshot.selected_cost = Decimal("180")
    recovered_snapshot.delta_cost = Decimal("-800")
    recovered_snapshot.save(update_fields=["selected_cost", "delta_cost"])

    client = Client()
    headers, _ = jwt_login(client, username="rebuild-owner")
    response = client.post("/api/observations/rebuild", **headers)

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["rebuilt_observations"] == 2
    assert payload["replay_started_at"] is not None
    assert list(
        Observation.objects.order_by("observed_at", "id").values_list(
            "raw_selected_total_cost",
            flat=True,
        )
    ) == raw_totals_before
    first.refresh_from_db()
    false_reset.refresh_from_db()
    recovered.refresh_from_db()
    recovered_snapshot.refresh_from_db()
    assert first.selected_total_cost == Decimal("940")
    assert false_reset.exclusion_source == "manual"
    assert recovered.selected_total_cost == Decimal("980")
    assert recovered.delta_percent == Decimal("2")
    assert recovered.delta_cost == Decimal("40")
    assert recovered_snapshot.selected_cost == Decimal("980")
    assert recovered_snapshot.delta_cost == Decimal("40")


@pytest.mark.django_db
def test_api_requires_admin_jwt_and_accepts_admin_login():
    user = get_user_model().objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    client = Client(enforce_csrf_checks=True)
    unauthorized = client.get("/api/dashboard")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["ok"] is False

    headers, logged_in = jwt_login(client)
    assert "access" in logged_in.json()["data"]
    assert "refresh" not in logged_in.json()["data"]
    assert logged_in.cookies["pinche_refresh"]["httponly"]
    assert "sessionid" not in logged_in.cookies
    assert logged_in.json()["data"]["timezone"] == "Asia/Shanghai"
    assert client.get("/api/auth/me", **headers).json()["data"]["timezone"] == (
        "Asia/Shanghai"
    )
    config = AppSettings.load()
    account = create_monitored_account(7)
    account.last_upstream_check_at = timezone.now() - timedelta(hours=13)
    account.save(update_fields=["last_upstream_check_at", "updated_at"])
    config.stale_warning_hours = 12
    config.save(update_fields=["stale_warning_hours", "updated_at"])

    dashboard = client.get("/api/dashboard", **headers)
    assert dashboard.status_code == 200
    assert dashboard.json()["data"]["quota_query_mode"] == "passive"
    assert dashboard.json()["data"]["snapshot_stale"] is True
    assert user.is_staff
