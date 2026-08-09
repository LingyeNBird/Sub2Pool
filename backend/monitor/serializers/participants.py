"""Participant write serializers."""
from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from ..models import Participant
from ..participant_history import sync_participant_history


class ParticipantWriteSerializer(serializers.ModelSerializer):
    """参与者写入契约，并维护启用参与者权益总和不超过 100%。"""

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

    def validate(self, attrs):
        instance = self.instance
        enabled = attrs.get("enabled", instance.enabled if instance else True)
        share = attrs.get(
            "share_percent",
            instance.share_percent if instance else Decimal("0"),
        )
        queryset = Participant.objects.filter(enabled=True)
        if instance:
            queryset = queryset.exclude(pk=instance.pk)
        total = sum((item.share_percent for item in queryset), Decimal("0"))
        if enabled and total + share > Decimal("100"):
            raise serializers.ValidationError(
                {
                    "share_percent": (
                        f"启用参与者的权益合计将达到 {total + share}%，"
                        "不能超过 100%"
                    )
                }
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        participant = super().create(validated_data)
        sync_participant_history(participant)
        return participant

    @transaction.atomic
    def update(self, instance, validated_data):
        previous_user_id = instance.sub2api_user_id
        needs_history_sync = any(
            field in validated_data
            for field in ("sub2api_user_id", "share_percent", "enabled")
        )
        participant = super().update(instance, validated_data)
        if needs_history_sync:
            sync_participant_history(
                participant,
                previous_user_id=previous_user_id,
            )
        return participant
