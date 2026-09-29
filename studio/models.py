from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models


COLOR_CHOICES = (
    ("black", "Black"), ("blue", "Blue"), ("green", "Green"),
    ("orange", "Orange"), ("pink", "Pink"), ("purple", "Purple"),
    ("red", "Red"), ("white", "White"), ("yellow", "Yellow"),
    ("multicolor", "Multicolor"), ("other", "Other"),
)


def validate_colors(value):
    valid_colors = {choice[0] for choice in COLOR_CHOICES}
    if not isinstance(value, list) or not value or any(color not in valid_colors for color in value):
        raise ValidationError("Choose one or more colors from the approved palette.")


class Product(models.Model):
    class Pattern(models.TextChoices):
        LEHERIYA = "leheriya", "Leheriya"
        EKDALI = "ekdali", "Ekdali"
        CHAUBASI = "chaubasi", "Chaubasi"
        SHIKARI = "shikari", "Shikari"
        OTHER = "other", "Other"

    name = models.CharField(max_length=200)
    pattern_type = models.CharField(max_length=20, choices=Pattern.choices)
    colors = models.JSONField(default=list, validators=[validate_colors])
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        validate_colors(self.colors)


class ProductPhoto(models.Model):
    class Kind(models.TextChoices):
        FLATLAY = "flatlay", "Flat-lay"
        DETAIL = "detail", "Fabric close-up"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="products/")
    kind = models.CharField(max_length=10, choices=Kind.choices)


class Campaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RUNNING = "running", "Running"
        REVIEW = "review", "Awaiting approval"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="campaigns")
    occasion = models.CharField(max_length=100, default="Everyday", help_text="e.g. Navratri, Diwali, wedding")
    brief = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    paid_calls_used = models.PositiveSmallIntegerField(default=0, validators=[MinValueValidator(0)])
    ready_email_sent = models.BooleanField(default=False)
    review_email_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class Generation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        GENERATING = "generating", "Generating"
        FAILED_QC = "failed_qc", "Failed QC"
        PASSED_QC = "passed_qc", "Passed QC"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="generations")
    prompt = models.TextField()
    image = models.ImageField(upload_to="generations/", blank=True)
    attempt = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    qc_score = models.FloatField(null=True, blank=True)
    qc_notes = models.TextField(blank=True)
    provider_used = models.CharField(max_length=100, blank=True)
    active_task_id = models.CharField(max_length=255, blank=True)
    pipeline_managed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=("campaign", "attempt"),
            condition=models.Q(pipeline_managed=True),
            name="unique_pipeline_generation_attempt",
        )]


class AdAsset(models.Model):
    generation = models.ForeignKey(Generation, on_delete=models.CASCADE, related_name="assets")
    aspect_ratio = models.CharField(max_length=10, choices=(("1:1", "1:1"), ("4:5", "4:5"), ("9:16", "9:16")))
    image = models.ImageField(upload_to="ads/")
    caption = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("generation", "aspect_ratio"), name="unique_generation_ad_aspect_ratio")]