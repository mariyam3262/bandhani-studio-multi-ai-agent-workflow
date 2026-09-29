import json

from django.db import migrations, models
import django.core.validators
from studio.models import validate_colors


def normalize_product_colors(apps, schema_editor):
    Product = apps.get_model("studio", "Product")
    allowed = {"black", "blue", "green", "orange", "pink", "purple", "red", "white", "yellow", "multicolor", "other"}
    for product in Product.objects.all().iterator():
        values = [item.strip().lower() for item in product.colors.split(",") if item.strip()]
        normalized = [value for value in values if value in allowed]
        product.colors = json.dumps(list(dict.fromkeys(normalized)) or ["other"])
        product.save(update_fields=["colors"])


class Migration(migrations.Migration):
    dependencies = [("studio", "0001_initial")]

    operations = [
        migrations.RunPython(normalize_product_colors, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="product",
            name="colors",
            field=models.JSONField(default=list, validators=[validate_colors]),
        ),
        migrations.AlterField(
            model_name="campaign",
            name="occasion",
            field=models.CharField(default="Everyday", help_text="e.g. Navratri, Diwali, wedding", max_length=100),
        ),
        migrations.AlterField(
            model_name="campaign",
            name="status",
            field=models.CharField(choices=[("draft", "Draft"), ("running", "Running"), ("review", "Awaiting approval"), ("done", "Done"), ("failed", "Failed")], default="draft", max_length=10),
        ),
        migrations.AddField(
            model_name="campaign",
            name="paid_calls_used",
            field=models.PositiveSmallIntegerField(default=0, validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(model_name="campaign", name="ready_email_sent", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="campaign", name="review_email_sent", field=models.BooleanField(default=False)),
        migrations.AlterField(
            model_name="generation",
            name="status",
            field=models.CharField(choices=[("pending", "Pending"), ("generating", "Generating"), ("failed_qc", "Failed QC"), ("passed_qc", "Passed QC"), ("approved", "Approved"), ("rejected", "Rejected")], default="pending", max_length=10),
        ),
        migrations.AddField(model_name="generation", name="provider_used", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="generation", name="active_task_id", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="generation", name="pipeline_managed", field=models.BooleanField(default=False)),
        migrations.AddConstraint(
            model_name="generation",
            constraint=models.UniqueConstraint(
                condition=models.Q(pipeline_managed=True),
                fields=("campaign", "attempt"),
                name="unique_pipeline_generation_attempt",
            ),
        ),
        migrations.AlterField(
            model_name="adasset",
            name="aspect_ratio",
            field=models.CharField(choices=[("1:1", "1:1"), ("4:5", "4:5"), ("9:16", "9:16")], max_length=10),
        ),
        migrations.AddConstraint(
            model_name="adasset",
            constraint=models.UniqueConstraint(fields=("generation", "aspect_ratio"), name="unique_generation_ad_aspect_ratio"),
        ),
    ]
