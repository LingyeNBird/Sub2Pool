from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("monitor", "0017_all_user_usage_and_quota_model")]

    operations = [
        migrations.AddField(
            model_name="appsettings",
            name="fast_correction_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="observation",
            name="fast_correction_actual_cost",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                max_digits=18,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="observation",
            name="fast_correction_standard_cost",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                max_digits=18,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="observation",
            name="fast_correction_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="ObservationFastCorrection",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("sub2api_user_id", models.BigIntegerField(db_index=True)),
                ("fast_request_count", models.PositiveIntegerField(default=0)),
                (
                    "fast_standard_cost",
                    models.DecimalField(decimal_places=6, max_digits=18),
                ),
                (
                    "fast_actual_cost",
                    models.DecimalField(decimal_places=6, max_digits=18),
                ),
                (
                    "standard_correction_cost",
                    models.DecimalField(decimal_places=6, max_digits=18),
                ),
                (
                    "actual_correction_cost",
                    models.DecimalField(decimal_places=6, max_digits=18),
                ),
                (
                    "observation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fast_corrections",
                        to="monitor.observation",
                    ),
                ),
            ],
            options={
                "ordering": ["sub2api_user_id"],
                "indexes": [
                    models.Index(
                        fields=["sub2api_user_id", "observation"],
                        name="fast_correction_user_obs",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("observation", "sub2api_user_id"),
                        name="unique_observation_fast_user",
                    )
                ],
            },
        ),
    ]
