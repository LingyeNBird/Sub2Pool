"""Security audit write serializers."""
from rest_framework import serializers

from ..models import BlockedIPAddress, LoginEvent


class BlockedIPAddressSerializer(serializers.ModelSerializer):
    """IP 封禁写入契约；地址由 GenericIPAddressField 统一规范化。"""

    login_event_id = serializers.PrimaryKeyRelatedField(
        source="login_event",
        queryset=LoginEvent.objects.all(),
        required=False,
        allow_null=True,
    )
    source_label = serializers.CharField(
        source="get_source_type_display",
        read_only=True,
    )

    class Meta:
        model = BlockedIPAddress
        fields = (
            "id",
            "address",
            "source_type",
            "source_label",
            "notes",
            "login_event_id",
            "created_at",
        )
        read_only_fields = ("id", "created_at")
