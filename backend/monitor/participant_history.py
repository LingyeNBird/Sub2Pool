"""把与参与者无关的全量用户原始用量补建为参与者历史账本。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction

from .models import (
    AppSettings,
    Observation,
    Participant,
    ParticipantSnapshot,
    ParticipantUsageSample,
    Sub2APIUserUsageSample,
)
from .replay import rebuild_account

ZERO = Decimal("0")


@transaction.atomic
def sync_participant_history(
    participant: Participant,
    *,
    previous_user_id: int | None = None,
) -> dict[str, int]:
    """按 Sub2API 用户 ID 补建全部已有原始采样，再重放受影响区间。

    用户绑定发生得晚于采样也不会丢失历史用量。若管理员改绑了 Sub2API
    用户，旧用户生成的参与者账本必须先删除，避免把两个人的历史混在一起。
    """

    config = AppSettings.load()
    user_changed = bool(
        previous_user_id is not None
        and previous_user_id != participant.sub2api_user_id
    )
    if user_changed:
        ParticipantSnapshot.objects.filter(participant=participant).delete()
        ParticipantUsageSample.objects.filter(participant=participant).delete()

    if not participant.enabled:
        return {"snapshots": 0, "usage_samples": 0, "accounts": 0}

    raw_samples = list(
        Sub2APIUserUsageSample.objects.filter(
            sub2api_user_id=participant.sub2api_user_id,
        ).order_by("account_id", "observed_at", "id")
    )
    if not raw_samples:
        return {"snapshots": 0, "usage_samples": 0, "accounts": 0}

    latest_raw = raw_samples[-1]
    identity_fields: list[str] = []
    if participant.sub2api_username != latest_raw.username:
        participant.sub2api_username = latest_raw.username
        identity_fields.append("sub2api_username")
    if participant.sub2api_email != latest_raw.email:
        participant.sub2api_email = latest_raw.email
        identity_fields.append("sub2api_email")
    if identity_fields:
        participant.save(update_fields=identity_fields)

    existing_usage_keys = set(
        ParticipantUsageSample.objects.filter(participant=participant)
        .values_list("account_id", "observed_at")
    )
    usage_rows = []
    for raw in raw_samples:
        key = (raw.account_id, raw.observed_at)
        if key in existing_usage_keys:
            continue
        balance = (
            participant.latest_balance_usd
            if participant.last_checked_at == raw.observed_at
            else None
        )
        raw_selected_cost = raw.selected_cost(config.cost_basis)
        selected_cost = raw.normalized_cost(config.cost_basis)
        usage_rows.append(
            ParticipantUsageSample(
                participant=participant,
                account_id=raw.account_id,
                observed_at=raw.observed_at,
                attribution_started_at=raw.window_started_at,
                balance_usd=balance,
                selected_cost=selected_cost,
                raw_selected_cost=raw_selected_cost,
            )
        )
    ParticipantUsageSample.objects.bulk_create(
        usage_rows,
        ignore_conflicts=True,
        batch_size=500,
    )

    raw_by_account: dict[int, dict[datetime, Sub2APIUserUsageSample]] = (
        defaultdict(dict)
    )
    for raw in raw_samples:
        raw_by_account[raw.account_id][raw.observed_at] = raw

    snapshot_rows = []
    replay_from_by_account: dict[int, datetime] = {}
    for account_id, raw_by_time in raw_by_account.items():
        observed_times = sorted(raw_by_time)
        observations = list(
            Observation.objects.filter(
                account_id=account_id,
                observed_at__gte=observed_times[0],
                observed_at__lte=observed_times[-1],
            ).order_by("observed_at", "id")
        )
        existing_observation_ids = set(
            ParticipantSnapshot.objects.filter(
                participant=participant,
                observation__account_id=account_id,
            ).values_list("observation_id", flat=True)
        )
        matched_observations = []
        for observation in observations:
            raw = raw_by_time.get(observation.observed_at)
            if raw is None:
                continue
            matched_observations.append(observation)
            if observation.pk in existing_observation_ids:
                continue
            raw_selected_cost = raw.selected_cost(config.cost_basis)
            selected_cost = raw.normalized_cost(config.cost_basis)
            balance = (
                participant.latest_balance_usd
                if participant.last_checked_at == observation.observed_at
                else None
            )
            snapshot_rows.append(
                ParticipantSnapshot(
                    observation=observation,
                    participant=participant,
                    raw_selected_cost=raw_selected_cost,
                    selected_cost=selected_cost,
                    current_balance_usd=balance,
                    remaining_share_percent=participant.share_percent,
                )
            )
        if matched_observations:
            replay_from_by_account[account_id] = min(
                observation.upstream_resets_at
                - timedelta(seconds=observation.window_seconds)
                for observation in matched_observations
            )

    ParticipantSnapshot.objects.bulk_create(
        snapshot_rows,
        ignore_conflicts=True,
        batch_size=500,
    )
    for account_id, replay_from in replay_from_by_account.items():
        rebuild_account(account_id, config, replay_from=replay_from)

    return {
        "snapshots": len(snapshot_rows),
        "usage_samples": len(usage_rows),
        "accounts": len(replay_from_by_account),
    }
