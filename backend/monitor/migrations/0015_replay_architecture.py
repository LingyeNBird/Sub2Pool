from collections import defaultdict
from decimal import Decimal

from django.db import migrations, models


ZERO = Decimal("0")


def migrate_cycle_data(apps, _schema_editor):
    Observation = apps.get_model("monitor", "Observation")
    ParticipantSnapshot = apps.get_model("monitor", "ParticipantSnapshot")
    ParticipantUsageSample = apps.get_model("monitor", "ParticipantUsageSample")
    QuotaCycle = apps.get_model("monitor", "QuotaCycle")

    cycles = {cycle.pk: cycle for cycle in QuotaCycle.objects.all()}
    for observation in Observation.objects.all().iterator():
        cycle = cycles[observation.cycle_id]
        observation.account_id = cycle.account_id
        observation.window_seconds = cycle.window_seconds
        observation.upstream_resets_at = cycle.resets_at
        observation.attribution_started_at = cycle.starts_at
        observation.raw_selected_total_cost = observation.selected_total_cost
        if observation.excluded_at is not None:
            candidate_status = observation.raw_window.get(
                "reset_candidate_status",
            )
            observation.exclusion_source = (
                "automatic" if candidate_status else "manual"
            )
        observation.save(
            update_fields=[
                "account_id",
                "window_seconds",
                "upstream_resets_at",
                "attribution_started_at",
                "raw_selected_total_cost",
                "exclusion_source",
            ]
        )

    for snapshot in ParticipantSnapshot.objects.all().iterator():
        snapshot.raw_selected_cost = snapshot.selected_cost
        snapshot.save(update_fields=["raw_selected_cost"])

    for sample in ParticipantUsageSample.objects.all().iterator():
        cycle = cycles[sample.cycle_id]
        sample.account_id = cycle.account_id
        sample.attribution_started_at = cycle.starts_at
        sample.raw_selected_cost = sample.selected_cost
        sample.save(
            update_fields=[
                "account_id",
                "attribution_started_at",
                "raw_selected_cost",
            ]
        )

    # 旧实现遇到同一 reset_at 的百分比回退时会建立另一个周期，并把
    # Sub2API 日期口径成本从零重新累计。删除周期实体前先把这些成本平移
    # 到同一原始坐标，之后重放器才能只依赖采样点判断它是否真是重置。
    grouped = defaultdict(list)
    for cycle in cycles.values():
        grouped[(cycle.account_id, cycle.resets_at)].append(cycle)

    for group in grouped.values():
        group.sort(key=lambda item: (item.starts_at, item.pk))
        earlier_cycle_ids = []
        for cycle in group:
            if not earlier_cycle_ids:
                earlier_cycle_ids.append(cycle.pk)
                continue
            first = (
                Observation.objects.filter(cycle_id=cycle.pk)
                .order_by("observed_at", "id")
                .first()
            )
            if first is None:
                earlier_cycle_ids.append(cycle.pk)
                continue
            base = (
                Observation.objects.filter(
                    cycle_id__in=earlier_cycle_ids,
                    observed_at__lte=first.observed_at,
                    excluded_at__isnull=True,
                )
                .order_by("-observed_at", "-id")
                .first()
            )
            if base is None:
                earlier_cycle_ids.append(cycle.pk)
                continue

            # 同一 reset_at 下后建周期的成本是从该周期自己的零点重新累计，
            # 因此应把上一段末值整体加到新段，而不是把首个非零值抵消掉。
            # 这样即使两个观测之间已经产生消费，也不会在迁移时丢失。
            total_offset = base.raw_selected_total_cost
            standard_offset = base.total_standard_cost
            actual_offset = base.total_actual_cost
            base_snapshots = {
                item.participant_id: item
                for item in ParticipantSnapshot.objects.filter(
                    observation=base,
                )
            }
            participant_offsets = {
                participant_id: base_snapshot.raw_selected_cost
                for participant_id, base_snapshot in base_snapshots.items()
            }

            for observation in Observation.objects.filter(
                cycle_id=cycle.pk,
            ).iterator():
                observation.raw_selected_total_cost += total_offset
                observation.selected_total_cost += total_offset
                observation.total_standard_cost += standard_offset
                observation.total_actual_cost += actual_offset
                raw_window = dict(observation.raw_window)
                raw_window["legacy_cost_rebased"] = True
                observation.raw_window = raw_window
                observation.save(
                    update_fields=[
                        "raw_selected_total_cost",
                        "selected_total_cost",
                        "total_standard_cost",
                        "total_actual_cost",
                        "raw_window",
                    ]
                )
                for snapshot in ParticipantSnapshot.objects.filter(
                    observation=observation,
                ):
                    offset = participant_offsets.get(
                        snapshot.participant_id,
                        ZERO,
                    )
                    snapshot.raw_selected_cost += offset
                    snapshot.selected_cost += offset
                    snapshot.save(
                        update_fields=["raw_selected_cost", "selected_cost"]
                    )

            for sample in ParticipantUsageSample.objects.filter(
                cycle_id=cycle.pk,
            ):
                offset = participant_offsets.get(sample.participant_id, ZERO)
                sample.raw_selected_cost += offset
                sample.selected_cost += offset
                sample.save(
                    update_fields=["raw_selected_cost", "selected_cost"]
                )
            earlier_cycle_ids.append(cycle.pk)


    # 旧唯一约束包含 cycle。删除周期外键后，同一账号的误周期有可能留下
    # 完全相同时间戳的重复趋势点；新约束建立前保留最新一条即可。
    seen_usage_keys = set()
    for sample in ParticipantUsageSample.objects.order_by("-id").iterator():
        key = (sample.participant_id, sample.account_id, sample.observed_at)
        if key in seen_usage_keys:
            sample.delete()
            continue
        seen_usage_keys.add(key)


class Migration(migrations.Migration):
    dependencies = [("monitor", "0014_observation_exclusion")]


    operations = [
        migrations.AddField(
            model_name="observation",
            name="account_id",
            field=models.BigIntegerField(db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="observation",
            name="attribution_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="observation",
            name="exclusion_source",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "未排除"),
                    ("manual", "管理员排除"),
                    ("automatic", "异常检测排除"),
                ],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="observation",
            name="force_included",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="observation",
            name="raw_selected_total_cost",
            field=models.DecimalField(
                decimal_places=6,
                max_digits=18,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="observation",
            name="upstream_resets_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name="observation",
            name="window_seconds",
            field=models.PositiveIntegerField(default=604800),
        ),
        migrations.AddField(
            model_name="participantsnapshot",
            name="raw_selected_cost",
            field=models.DecimalField(
                decimal_places=6,
                max_digits=18,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="participantusagesample",
            name="account_id",
            field=models.BigIntegerField(db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="participantusagesample",
            name="attribution_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="participantusagesample",
            name="raw_selected_cost",
            field=models.DecimalField(
                decimal_places=6,
                max_digits=18,
                null=True,
            ),
        ),
        migrations.RunPython(migrate_cycle_data, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="participantusagesample",
            name="unique_participant_cycle_sample",
        ),
        migrations.RemoveIndex(
            model_name="observation",
            name="monitor_obs_cycle_i_35779c_idx",
        ),
        migrations.RemoveField(model_name="observation", name="cycle"),
        migrations.RemoveField(
            model_name="participantusagesample",
            name="cycle",
        ),
        migrations.DeleteModel(name="QuotaCycle"),
        migrations.AlterField(
            model_name="observation",
            name="account_id",
            field=models.BigIntegerField(db_index=True),
        ),
        migrations.AlterField(
            model_name="observation",
            name="raw_selected_total_cost",
            field=models.DecimalField(decimal_places=6, max_digits=18),
        ),
        migrations.AlterField(
            model_name="observation",
            name="upstream_resets_at",
            field=models.DateTimeField(),
        ),
        migrations.AlterField(
            model_name="participantsnapshot",
            name="raw_selected_cost",
            field=models.DecimalField(decimal_places=6, max_digits=18),
        ),
        migrations.AlterField(
            model_name="participantusagesample",
            name="account_id",
            field=models.BigIntegerField(db_index=True),
        ),
        migrations.AlterField(
            model_name="participantusagesample",
            name="raw_selected_cost",
            field=models.DecimalField(decimal_places=6, max_digits=18),
        ),
        migrations.AddConstraint(
            model_name="participantusagesample",
            constraint=models.UniqueConstraint(
                fields=("participant", "account_id", "observed_at"),
                name="unique_participant_account_sample",
            ),
        ),
        migrations.AddIndex(
            model_name="observation",
            index=models.Index(
                fields=["account_id", "-observed_at"],
                name="observation_account_time",
            ),
        ),
        migrations.AddIndex(
            model_name="observation",
            index=models.Index(
                fields=["account_id", "attribution_started_at"],
                name="observation_replay_segment",
            ),
        ),
    ]
