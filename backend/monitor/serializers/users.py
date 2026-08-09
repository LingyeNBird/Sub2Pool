"""Non-admin system-user write serializers."""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from ..models import Participant

User = get_user_model()


class SystemUserWriteSerializer(serializers.Serializer):
    """普通系统用户写入契约；管理员账号不通过该接口管理。"""

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
    participant_ids = serializers.PrimaryKeyRelatedField(
        source="participants",
        many=True,
        allow_empty=False,
        queryset=Participant.objects.all(),
    )

    def validate_username(self, value: str) -> str:
        queryset = User.objects.filter(username__iexact=value)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("该用户名已存在")
        return value

    def validate(self, attrs):
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
        participants = validated_data.pop("participants")
        password = validated_data.pop("password")
        user = User(
            **validated_data,
            is_staff=False,
            is_superuser=False,
        )
        user.set_password(password)
        user.save()
        user.quota_participants.set(participants)
        return user

    @transaction.atomic
    def update(self, instance, validated_data):
        participants = validated_data.pop("participants", None)
        password = validated_data.pop("password", "")
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            instance.set_password(password)
        instance.save()
        if participants is not None:
            instance.quota_participants.set(participants)

        return instance
