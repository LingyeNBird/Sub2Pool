"""Non-admin system-user identity and permission serializers."""

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from ..models import (
    PARTICIPANT_SCOPED_PAGE_PERMISSIONS,
    MonitoredAccount,
    PagePermission,
    Participant,
    SystemUserPageAccess,
)

User = get_user_model()


class SystemUserWriteSerializer(serializers.Serializer):
    """Ordinary system-user identity; access grants are managed separately."""

    username = serializers.CharField(
        max_length=150,
        trim_whitespace=True,
        validators=User._meta.get_field("username").validators,
    )
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        write_only=True,
    )
    is_active = serializers.BooleanField(default=True)

    def validate_username(self, value: str) -> str:
        queryset = User.objects.filter(username__iexact=value)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("该用户名已存在")
        return value

    def validate(self, attrs):
        unexpected_fields = set(self.initial_data) - set(self.fields)
        if unexpected_fields:
            raise serializers.ValidationError(
                {
                    field: (
                        "请通过系统用户权限接口修改该字段"
                        if field in {"account_ids", "participant_ids"}
                        else "不支持该字段"
                    )
                    for field in sorted(unexpected_fields)
                }
            )
        password = attrs.get("password", "")
        if self.instance is None and not password:
            raise serializers.ValidationError({"password": "添加用户时必须设置密码"})
        if password:
            candidate = User(
                username=attrs.get(
                    "username",
                    self.instance.username if self.instance else "",
                ),
                email=attrs.get(
                    "email",
                    self.instance.email if self.instance else "",
                ),
            )
            try:
                validate_password(password, candidate)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(
                    {"password": list(exc.messages)}
                ) from exc
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(
            **validated_data,
            is_staff=False,
            is_superuser=False,
        )
        user.set_password(password)
        user.save()
        return user

    @transaction.atomic
    def update(self, instance, validated_data):
        password = validated_data.pop("password", "")
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class SystemUserPermissionSerializer(serializers.Serializer):
    page_permissions = serializers.ListField(
        child=serializers.ChoiceField(choices=PagePermission.choices),
        allow_empty=True,
    )
    participant_ids = serializers.PrimaryKeyRelatedField(
        source="participants",
        many=True,
        allow_empty=True,
        queryset=Participant.objects.all(),
    )
    account_ids = serializers.PrimaryKeyRelatedField(
        source="accounts",
        many=True,
        allow_empty=True,
        queryset=MonitoredAccount.objects.all(),
    )

    def validate_page_permissions(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("页面权限不能重复")
        return value

    def validate(self, attrs):
        unexpected_fields = set(self.initial_data) - set(self.fields)
        if unexpected_fields:
            raise serializers.ValidationError(
                {
                    field: "不支持该字段"
                    for field in sorted(unexpected_fields)
                }
            )
        page_permissions = set(attrs["page_permissions"])
        if (
            page_permissions & PARTICIPANT_SCOPED_PAGE_PERMISSIONS
            and not attrs["participants"]
        ):
            raise serializers.ValidationError(
                {
                    "participant_ids": (
                        "已开放包含参与者数据的页面，请至少选择一个可查看的参与者"
                    )
                }
            )
        if (
            PagePermission.ACCOUNT_STATUS in page_permissions
            and not attrs["accounts"]
        ):
            raise serializers.ValidationError(
                {
                    "account_ids": (
                        "已开放账号状态页面，请至少选择一个可查看的账号"
                    )
                }
            )
        return attrs

    @transaction.atomic
    def update(self, instance, validated_data):
        page_permissions = validated_data["page_permissions"]
        participants = validated_data["participants"]
        accounts = validated_data["accounts"]
        SystemUserPageAccess.objects.filter(user=instance).delete()
        SystemUserPageAccess.objects.bulk_create(
            [
                SystemUserPageAccess(user=instance, page_code=page_code)
                for page_code in page_permissions
            ]
        )
        instance.quota_participants.set(participants)
        instance.visible_monitored_accounts.set(accounts)
        prefetch_cache = getattr(instance, "_prefetched_objects_cache", {})
        prefetch_cache.pop("page_accesses", None)
        prefetch_cache.pop("quota_participants", None)
        prefetch_cache.pop("visible_monitored_accounts", None)
        return instance
