"""Participant identity and quota-pool allocation serializers."""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from ..history_state import fenced_fact_write
from ..models import (
    AccountParticipant,
    MonitoredAccount,
    Participant,
    PoolParticipant,
    QuotaPool,
)


class ParticipantWriteSerializer(serializers.ModelSerializer):
    """Write one Sub2API user; pool percentages are managed separately."""

    class Meta:
        model = Participant
        fields = (
            "name",
            "email",
            "sub2api_user_id",
            "sub2api_username",
            "sub2api_email",
            "is_owner",
            "enabled",
            "notes",
        )
        extra_kwargs = {"sub2api_user_id": {"validators": []}}

    @staticmethod
    def _account_external_ids() -> list[int]:
        return [
            account.fact_key
            for account in MonitoredAccount.objects.filter(
                provider="sub2api"
            ).order_by("external_account_id")
        ]

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
    def _ensure_account_usage_rows(participant: Participant) -> None:
        existing = set(
            participant.account_memberships.values_list("account_id", flat=True)
        )
        AccountParticipant.objects.bulk_create(
            [
                AccountParticipant(account_id=account_id, participant=participant)
                for account_id in MonitoredAccount.objects.filter(
                    provider="sub2api"
                )
                .exclude(pk__in=existing)
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
            self._validate_user_identity(
                sub2api_user_id=sub2api_user_id,
                instance_id=current.pk,
            )
            for field, value in validated_data.items():
                setattr(current, field, value)
            current.save()
            self._ensure_account_usage_rows(current)
            return current


class PoolAllocationEntrySerializer(serializers.Serializer):
    participant_id = serializers.IntegerField(min_value=1)
    share_percent = serializers.DecimalField(
        max_digits=7,
        decimal_places=3,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
    )


class QuotaPoolWriteSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False, min_value=1)
    name = serializers.CharField(
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        max_length=160,
    )
    account_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
    )
    allocations = PoolAllocationEntrySerializer(many=True, required=False)


class QuotaAllocationWriteSerializer(serializers.Serializer):
    """Atomically replace the complete account partition and pool contracts."""

    pools = QuotaPoolWriteSerializer(many=True)

    def validate(self, attrs):
        pools = attrs["pools"]
        configured_account_ids = set(
            MonitoredAccount.objects.filter(provider="sub2api").values_list(
                "id",
                flat=True,
            )
        )
        configured_participant_ids = set(
            Participant.objects.values_list("id", flat=True)
        )
        existing_pool_ids = set(
            QuotaPool.objects.filter(accounts__provider="sub2api")
            .distinct()
            .values_list("id", flat=True)
        )

        seen_account_ids: set[int] = set()
        seen_pool_ids: set[int] = set()
        for index, pool in enumerate(pools):
            pool_id = pool.get("id")
            if pool_id is not None:
                if pool_id not in existing_pool_ids:
                    raise serializers.ValidationError(
                        {"pools": {index: {"id": "额度池不存在"}}}
                    )
                if pool_id in seen_pool_ids:
                    raise serializers.ValidationError(
                        {"pools": {index: {"id": "同一额度池不能重复提交"}}}
                    )
                seen_pool_ids.add(pool_id)

            account_ids = pool["account_ids"]
            if len(account_ids) != len(set(account_ids)):
                raise serializers.ValidationError(
                    {"pools": {index: {"account_ids": "池内账号不能重复"}}}
                )
            duplicated_accounts = seen_account_ids.intersection(account_ids)
            if duplicated_accounts:
                raise serializers.ValidationError(
                    {
                        "pools": {
                            index: {
                                "account_ids": (
                                    "账号不能同时属于多个额度池："
                                    f"{sorted(duplicated_accounts)}"
                                )
                            }
                        }
                    }
                )
            seen_account_ids.update(account_ids)

            allocations = pool.get("allocations", [])
            participant_ids = [row["participant_id"] for row in allocations]
            if len(participant_ids) != len(set(participant_ids)):
                raise serializers.ValidationError(
                    {"pools": {index: {"allocations": "参与者不能重复"}}}
                )
            unknown_participants = set(participant_ids) - configured_participant_ids
            if unknown_participants:
                raise serializers.ValidationError(
                    {
                        "pools": {
                            index: {
                                "allocations": (
                                    "参与者不存在："
                                    f"{sorted(unknown_participants)}"
                                )
                            }
                        }
                    }
                )
            total = sum(
                (row["share_percent"] for row in allocations),
                Decimal("0"),
            )
            if total > Decimal("100"):
                raise serializers.ValidationError(
                    {
                        "pools": {
                            index: {
                                "allocations": (
                                    f"池内份额合计为 {total}%，不能超过 100%"
                                )
                            }
                        }
                    }
                )

        missing_accounts = configured_account_ids - seen_account_ids
        unknown_accounts = seen_account_ids - configured_account_ids
        if missing_accounts or unknown_accounts:
            messages = []
            if missing_accounts:
                messages.append(f"缺少账号 {sorted(missing_accounts)}")
            if unknown_accounts:
                messages.append(f"未知账号 {sorted(unknown_accounts)}")
            raise serializers.ValidationError({"pools": "；".join(messages)})
        if configured_account_ids and not pools:
            raise serializers.ValidationError({"pools": "每个账号都必须属于一个池"})
        return attrs

    @staticmethod
    def _generated_name(
        account_ids: list[int],
        accounts_by_id: dict[int, MonitoredAccount],
        used_names: set[str],
    ) -> str:
        if len(account_ids) == 1:
            return f"{accounts_by_id[account_ids[0]].name} 独立池"
        index = 1
        while f"混池 {index}" in used_names:
            index += 1
        return f"混池 {index}"

    @transaction.atomic
    def apply(self) -> list[QuotaPool]:
        accounts = list(
            MonitoredAccount.objects.select_for_update()
            .filter(provider="sub2api")
            .select_related("pool")
            .order_by("id")
        )
        accounts_by_id = {account.id: account for account in accounts}
        existing_pools = {
            pool.id: pool
            for pool in QuotaPool.objects.select_for_update()
            .filter(accounts__provider="sub2api")
            .prefetch_related("allocations")
            .distinct()
            .order_by("id")
        }
        requested_account_ids = {
            account_id
            for spec in self.validated_data["pools"]
            for account_id in spec["account_ids"]
        }
        if requested_account_ids != set(accounts_by_id):
            raise serializers.ValidationError(
                {"pools": "监控账号集合已变化，请刷新后重试"}
            )
        requested_pool_ids = {
            spec["id"]
            for spec in self.validated_data["pools"]
            if "id" in spec
        }
        if not requested_pool_ids.issubset(existing_pools):
            raise serializers.ValidationError(
                {"pools": "额度池集合已变化，请刷新后重试"}
            )
        requested_participant_ids = {
            row["participant_id"]
            for spec in self.validated_data["pools"]
            for row in spec.get("allocations", [])
        }
        locked_participant_ids = set(
            Participant.objects.select_for_update()
            .filter(pk__in=requested_participant_ids)
            .values_list("id", flat=True)
        )
        if requested_participant_ids != locked_participant_ids:
            raise serializers.ValidationError(
                {"pools": "参与者集合已变化，请刷新后重试"}
            )
        used_names = {pool.name for pool in existing_pools.values()}
        applied: list[tuple[QuotaPool, dict, bool]] = []

        for spec in self.validated_data["pools"]:
            account_ids = spec["account_ids"]
            pool_id = spec.get("id")
            pool = existing_pools.get(pool_id) if pool_id is not None else None
            requested_name = spec.get("name", "").strip()
            if pool is None:
                name = requested_name or self._generated_name(
                    account_ids,
                    accounts_by_id,
                    used_names,
                )
                pool = QuotaPool.objects.create(name=name)
                used_names.add(name)
                changed = True
            else:
                name = requested_name or pool.name
                current_account_ids = set(
                    account.id for account in accounts if account.pool_id == pool.id
                )
                current_allocations = {
                    row.participant_id: row.share_percent
                    for row in pool.allocations.all()
                }
                requested_allocations = {
                    row["participant_id"]: row["share_percent"]
                    for row in spec.get("allocations", [])
                    if row["share_percent"] > 0
                }
                changed = (
                    pool.name != name
                    or current_account_ids != set(account_ids)
                    or current_allocations != requested_allocations
                )
                if changed:
                    pool.name = name
                    pool.contract_revision += 1
                    pool.save(
                        update_fields=["name", "contract_revision", "updated_at"]
                    )
            applied.append((pool, spec, changed))

        for pool, spec, _changed in applied:
            for account_id in spec["account_ids"]:
                accounts_by_id[account_id].pool_id = pool.id
        MonitoredAccount.objects.bulk_update(accounts, ["pool"])

        for pool, spec, changed in applied:
            if not changed:
                continue
            pool.allocations.all().delete()
            PoolParticipant.objects.bulk_create(
                [
                    PoolParticipant(
                        pool=pool,
                        participant_id=row["participant_id"],
                        share_percent=row["share_percent"],
                    )
                    for row in spec.get("allocations", [])
                    if row["share_percent"] > 0
                ]
            )

        retained_pool_ids = {pool.id for pool, _spec, _changed in applied}
        QuotaPool.objects.exclude(id__in=retained_pool_ids).filter(
            accounts__isnull=True
        ).delete()
        return [pool for pool, _spec, _changed in applied]
