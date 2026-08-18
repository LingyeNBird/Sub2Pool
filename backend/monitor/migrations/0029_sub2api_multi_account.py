from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


def migrate_single_account(apps, _schema_editor):
    AppSettings = apps.get_model("monitor", "AppSettings")
    MonitoredAccount = apps.get_model("monitor", "MonitoredAccount")
    Participant = apps.get_model("monitor", "Participant")
    AccountParticipant = apps.get_model("monitor", "AccountParticipant")
    ParticipantSnapshot = apps.get_model("monitor", "ParticipantSnapshot")

    config = AppSettings.objects.filter(pk=1).first()
    account = None
    if config is not None and config.openai_account_id:
        account = MonitoredAccount.objects.create(
            external_account_id=config.openai_account_id,
            name=f"OpenAI 账号 {config.openai_account_id}",
            enabled=True,
            quota_query_mode=config.quota_query_mode,
            last_local_check_at=config.last_local_check_at,
            last_upstream_check_at=config.last_upstream_check_at,
            last_success_at=config.last_success_at,
            next_local_check_at=config.next_local_check_at,
            last_error=config.last_error,
        )

    participants = list(Participant.objects.order_by("id"))
    if participants and account is None:
        raise RuntimeError(
            "无法自动迁移现有参与者的混池合同：旧设置未配置 OpenAI 上游账号；"
            "请先在旧版本中完成账号配置后重试"
        )
    if account is not None:
        AccountParticipant.objects.bulk_create(
            [
                AccountParticipant(
                    account=account,
                    participant=participant,
                    share_percent=participant.share_percent,
                    is_owner=participant.is_owner,
                    enabled=participant.enabled,
                    latest_selected_cost=participant.latest_selected_cost,
                    last_checked_at=participant.last_checked_at,
                )
                for participant in participants
            ]
        )

    policy = {
        participant.id: (participant.share_percent, participant.is_owner)
        for participant in participants
    }
    snapshots = list(ParticipantSnapshot.objects.order_by("id"))
    for snapshot in snapshots:
        snapshot.share_percent, snapshot.is_owner = policy[snapshot.participant_id]
    if snapshots:
        ParticipantSnapshot.objects.bulk_update(
            snapshots,
            ["share_percent", "is_owner"],
        )


def restore_single_account(apps, _schema_editor):
    AppSettings = apps.get_model("monitor", "AppSettings")
    MonitoredAccount = apps.get_model("monitor", "MonitoredAccount")
    Participant = apps.get_model("monitor", "Participant")
    AccountParticipant = apps.get_model("monitor", "AccountParticipant")

    account = MonitoredAccount.objects.order_by("id").first()
    config = AppSettings.objects.filter(pk=1).first()
    if config is not None and account is not None:
        config.openai_account_id = account.external_account_id
        config.quota_query_mode = account.quota_query_mode
        config.save(update_fields=["openai_account_id", "quota_query_mode"])

    memberships = {
        row.participant_id: row
        for row in AccountParticipant.objects.select_related("participant").order_by(
            "account_id", "id"
        )
    }
    participants = list(Participant.objects.order_by("id"))
    for participant in participants:
        membership = memberships.get(participant.id)
        if membership is None:
            participant.share_percent = Decimal("0")
            participant.is_owner = False
            participant.latest_selected_cost = None
        else:
            participant.share_percent = membership.share_percent
            participant.is_owner = membership.is_owner
            participant.latest_selected_cost = membership.latest_selected_cost
    if participants:
        Participant.objects.bulk_update(
            participants,
            ["share_percent", "is_owner", "latest_selected_cost"],
        )


class Migration(migrations.Migration):
    dependencies = [("monitor", "0028_manual_start_intervals")]

    operations = [
        migrations.CreateModel(
            name="MonitoredAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("external_account_id", models.BigIntegerField(unique=True)),
                ("name", models.CharField(max_length=160)),
                ("enabled", models.BooleanField(default=True)),
                ("quota_query_mode", models.CharField(choices=[("passive", "仅读取 Sub2API 被动快照"), ("direct", "调用上游账号额度接口")], default="passive", max_length=16)),
                ("last_local_check_at", models.DateTimeField(blank=True, null=True)),
                ("last_upstream_check_at", models.DateTimeField(blank=True, null=True)),
                ("last_success_at", models.DateTimeField(blank=True, null=True)),
                ("next_local_check_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "监控上游账号", "verbose_name_plural": "监控上游账号", "ordering": ["name", "external_account_id"]},
        ),
        migrations.CreateModel(
            name="AccountParticipant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("share_percent", models.DecimalField(decimal_places=3, max_digits=7, validators=[django.core.validators.MinValueValidator(Decimal("0")), django.core.validators.MaxValueValidator(Decimal("100"))])),
                ("is_owner", models.BooleanField(default=False)),
                ("enabled", models.BooleanField(default=True)),
                ("latest_selected_cost", models.DecimalField(blank=True, decimal_places=6, max_digits=16, null=True)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memberships", to="monitor.monitoredaccount")),
                ("participant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="account_memberships", to="monitor.participant")),
            ],
            options={"ordering": ["account_id", "-is_owner", "participant_id"]},
        ),
        migrations.AddConstraint(
            model_name="accountparticipant",
            constraint=models.UniqueConstraint(fields=("account", "participant"), name="unique_account_participant"),
        ),
        migrations.AddField(
            model_name="participantsnapshot",
            name="share_percent",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=7, null=True, validators=[django.core.validators.MinValueValidator(Decimal("0")), django.core.validators.MaxValueValidator(Decimal("100"))]),
        ),
        migrations.AddField(
            model_name="participantsnapshot",
            name="is_owner",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="participant",
            name="share_percent",
            field=models.DecimalField(decimal_places=3, max_digits=7, null=True, validators=[django.core.validators.MinValueValidator(Decimal("0")), django.core.validators.MaxValueValidator(Decimal("100"))]),
        ),
        migrations.RunPython(migrate_single_account, restore_single_account),
        migrations.AlterField(
            model_name="participantsnapshot",
            name="share_percent",
            field=models.DecimalField(decimal_places=3, max_digits=7, validators=[django.core.validators.MinValueValidator(Decimal("0")), django.core.validators.MaxValueValidator(Decimal("100"))]),
        ),
        migrations.RemoveField(model_name="participant", name="share_percent"),
        migrations.RemoveField(model_name="participant", name="is_owner"),
        migrations.RemoveField(model_name="participant", name="latest_selected_cost"),
        migrations.RemoveField(model_name="appsettings", name="openai_account_id"),
        migrations.RemoveField(model_name="appsettings", name="quota_query_mode"),
        migrations.AlterModelOptions(
            name="participant",
            options={
                "ordering": ["id"],
                "verbose_name": "拼车参与者",
                "verbose_name_plural": "拼车参与者",
            },
        ),
    ]
