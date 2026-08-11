"""Participant write serializers."""
from decimal import Decimal

from rest_framework import serializers

from ..history_state import LeaseLostError, fenced_fact_write
from ..models import AppSettings, Participant


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
        extra_kwargs = {
            "sub2api_user_id": {"validators": []},
        }

    def _lock_and_validate(
        self,
        validated_data,
        *,
        instance_id: int | None = None,
    ) -> Participant | None:
        participants = list(
            Participant.objects.select_for_update().order_by("pk")
        )
        current = None
        if instance_id is not None:
            current = next(
                (item for item in participants if item.pk == instance_id),
                None,
            )
            if current is None:
                raise serializers.ValidationError(
                    {"detail": "参与者已被删除，请刷新后重试"}
                )

        sub2api_user_id = validated_data.get(
            "sub2api_user_id",
            current.sub2api_user_id if current else None,
        )
        if any(
            item.sub2api_user_id == sub2api_user_id
            and (current is None or item.pk != current.pk)
            for item in participants
        ):
            raise serializers.ValidationError(
                {"sub2api_user_id": "该 Sub2API 用户已绑定其他参与者"}
            )

        enabled = validated_data.get(
            "enabled",
            current.enabled if current else True,
        )
        share = validated_data.get(
            "share_percent",
            current.share_percent if current else Decimal("0"),
        )
        total = sum(
            (
                item.share_percent
                for item in participants
                if item.enabled
                and (current is None or item.pk != current.pk)
            ),
            Decimal("0"),
        )
        if enabled and total + share > Decimal("100"):
            raise serializers.ValidationError(
                {
                    "share_percent": (
                        f"启用参与者的权益合计将达到 {total + share}%，"
                        "不能超过 100%"
                    )
                }
            )
        return current

    @staticmethod
    def _assert_account_unchanged(account_id: int | None) -> None:
        current_account_id = (
            AppSettings.objects.select_for_update()
            .get(pk=1)
            .openai_account_id
        )
        if current_account_id != account_id:
            raise LeaseLostError(
                "上游账号设置已变化，请刷新后重试参与者写入"
            )

    def create(self, validated_data):
        account_id = AppSettings.load().openai_account_id
        with fenced_fact_write([account_id if account_id is not None else 0]):
            self._assert_account_unchanged(account_id)
            self._lock_and_validate(validated_data)
            return super().create(validated_data)

    def update(self, instance, validated_data):
        account_id = AppSettings.load().openai_account_id
        with fenced_fact_write([account_id if account_id is not None else 0]):
            self._assert_account_unchanged(account_id)
            current = self._lock_and_validate(
                validated_data,
                instance_id=instance.pk,
            )
            return super().update(current, validated_data)
