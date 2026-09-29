from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Campaign, ProductPhoto


@receiver(post_save, sender=ProductPhoto)
def queue_flatlay_pipeline(sender, instance, created, **kwargs):
    if created and instance.kind == ProductPhoto.Kind.FLATLAY:
        campaign, _ = Campaign.objects.get_or_create(
            product=instance.product,
            defaults={"occasion": "Everyday"},
        )
        from .tasks import run_full_pipeline
        transaction.on_commit(lambda: run_full_pipeline.delay(campaign.pk, 1))
