from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("monitor", "0008_appsettings_next_local_check_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="appsettings",
            name="daily_estimate_min_percent_span",
            field=models.DecimalField(
                decimal_places=3,
                default=Decimal("5"),
                max_digits=6,
                validators=[
                    MinValueValidator(Decimal("1")),
                    MaxValueValidator(Decimal("100")),
                ],
            ),
        ),
    ]
