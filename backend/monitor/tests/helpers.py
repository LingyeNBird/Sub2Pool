import json
from datetime import timedelta
from decimal import Decimal

from django.test import Client
from django.utils import timezone

from monitor.models import Observation, Participant, ParticipantSnapshot


def jwt_login(
    client: Client,
    username: str = "owner",
    password: str = "very-strong-password",
    **extra,
) -> tuple[dict[str, str], object]:
    response = client.post(
        "/api/auth/login",
        data=json.dumps(
            {
                "username": username,
                "password": password,
                **extra,
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    access = response.json()["data"]["access"]
    return {"HTTP_AUTHORIZATION": f"Bearer {access}"}, response


def create_recommendation_snapshot(
    participant: Participant,
    recommended: Decimal = Decimal("123.45"),
) -> ParticipantSnapshot:
    now = timezone.now()
    reset_at = now + timedelta(days=4)
    observation = Observation.objects.create(
        account_id=7,
        observed_at=now,
        window_seconds=604800,
        upstream_resets_at=reset_at,
        attribution_started_at=now - timedelta(days=3),
        upstream_used_percent=Decimal("20"),
        raw_selected_total_cost=Decimal("400"),
        selected_total_cost=Decimal("400"),
        total_standard_cost=Decimal("400"),
        total_actual_cost=Decimal("400"),
        effective_usd_per_percent=Decimal("20"),
    )
    return ParticipantSnapshot.objects.create(
        observation=observation,
        participant=participant,
        raw_selected_cost=Decimal("200"),
        selected_cost=Decimal("200"),
        current_balance_usd=Decimal("80"),
        recommended_balance_usd=recommended,
        balance_difference_usd=recommended - Decimal("80"),
        needs_manual_update=True,
    )
