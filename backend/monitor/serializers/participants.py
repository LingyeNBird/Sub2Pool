"""Global pooled participant contract write serializer."""
from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from ..history_state import fenced_fact_write
from ..models import AccountParticipant, MonitoredAccount, Participant


class ParticipantWriteSerializer(serializers.ModelSerializer):
    """Write one Sub2API user and its channel-wide pooled entitlement."""

    class Meta:
        model = Participant
        fields = (
            "name",
            "email",
            "sub2api_user_id",
            "sub2api_username",
            "sub2api_email",
            "share_percent",
            "is_owner",
            "enabled",
            "notes",
        )
        extra_kwargs = {"sub2api_user_id": {"validators": []}}

    @staticmethod
    def _account_external_ids() -> list[int]:
        return list(
            MonitoredAccount.objects.order_by("external_account_id").values_list(
                "external_account_id",
                flat=True,
            )
        )

    @staticmethod
    def _validate_user_identity(
        *,
        sub2api_user_id: int,
        instance_id: int | None,
    ) -> None:
        duplicate = Participant.objects.select_for_update().filter(
            sub2api_user_id=sub2api_user_id,
        )
        if instance_id is not None:
            duplicate = duplicate.exclude(pk=instance_id)
        if duplicate.exists():
            raise serializers.ValidationError(
                {"sub2api_user_id": "该 Sub2API 用户已绑定其他参与者"}
            )

    @staticmethod
    def _validate_share_total(
        *,
        instance_id: int | None,
        participant_enabled: bool,
        share_percent: Decimal,
    ) -> None:
        enabled = Participant.objects.select_for_update().filter(enabled=True)
        if instance_id is not None:
            enabled = enabled.exclude(pk=instance_id)
        total = sum(enabled.values_list("share_percent", flat=True), Decimal("0"))
        if participant_enabled:
            total += share_percent
        if total > Decimal("100"):
            raise serializers.ValidationError(
                {
                    "share_percent": (
                        f"启用参与者的混池权益合计将达到 {total}%，不能超过 100%"
                    )
                }
            )

    @staticmethod
    def _ensure_account_usage_rows(participant: Participant) -> None:
        existing = set(
            participant.account_memberships.values_list("account_id", flat=True)
        )
        AccountParticipant.objects.bulk_create(
            [
                AccountParticipant(account_id=account_id, participant=participant)
                for account_id in MonitoredAccount.objects.exclude(pk__in=existing)
                .order_by("id")
                .values_list("id", flat=True)
            ],
            ignore_conflicts=True,
        )

    def create(self, validated_data):
        with fenced_fact_write(self._account_external_ids()):
            self._validate_user_identity(
                sub2api_user_id=validated_data["sub2api_user_id"],
                instance_id=None,
            )
            self._validate_share_total(
                instance_id=None,
                participant_enabled=validated_data.get("enabled", True),
                share_percent=validated_data["share_percent"],
            )
            participant = Participant.objects.create(**validated_data)
            self._ensure_account_usage_rows(participant)
            return participant

    def update(self, instance, validated_data):
        with fenced_fact_write(self._account_external_ids()):
            current = Participant.objects.select_for_update().get(pk=instance.pk)
            sub2api_user_id = validated_data.get(
                "sub2api_user_id",
                current.sub2api_user_id,
            )
            participant_enabled = validated_data.get("enabled", current.enabled)
            share_percent = validated_data.get(
                "share_percent",
                current.share_percent,
            )
            self._validate_user_identity(
                sub2api_user_id=sub2api_user_id,
                instance_id=current.pk,
            )
            self._validate_share_total(
                instance_id=current.pk,
                participant_enabled=participant_enabled,
                share_percent=share_percent,
            )
            for field, value in validated_data.items():
                setattr(current, field, value)
            current.save()
            self._ensure_account_usage_rows(current)
            return current
