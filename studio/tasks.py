import logging
import os
from pathlib import Path

from celery import shared_task
from celery import Task
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import mail_admins
from django.db import transaction

from .models import Campaign, Generation
from .generator import IMAGE_PROVIDER, QuotaExceededError, build_prompt
from .packager import package_generation
from .qc import run_qc

logger = logging.getLogger("studio")

MAX_ATTEMPTS = 3
MAX_PAID_CALLS_PER_CAMPAIGN = int(os.environ.get("MAX_PAID_CALLS_PER_CAMPAIGN", "9"))


def _send_campaign_email(campaign, subject, message, marker):
    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().get(pk=campaign.pk)
        if getattr(campaign, marker):
            return
        mail_admins(subject, message)
        setattr(campaign, marker, True)
        campaign.save(update_fields=[marker])


def _notify_ready(campaign):
    _send_campaign_email(
        campaign,
        f"Campaign ready for approval: {campaign.product.name} ({campaign.occasion})",
        f"Campaign #{campaign.id} has passed QC and been packaged. Review it in Django admin.",
        "ready_email_sent",
    )
    logger.info("Approval notification sent for Campaign %s", campaign.id)


def _notify_review(campaign):
    _send_campaign_email(
        campaign,
        f"Campaign needs QC review: {campaign.product.name} ({campaign.occasion})",
        f"Campaign #{campaign.id} exhausted {MAX_ATTEMPTS} QC attempts. Review its generation history in Django admin.",
        "review_email_sent",
    )


def _mark_failed(campaign_id, error):
    Campaign.objects.filter(pk=campaign_id).exclude(status=Campaign.Status.DONE).update(status=Campaign.Status.FAILED)
    try:
        campaign = Campaign.objects.select_related("product").get(pk=campaign_id)
        mail_admins(
            f"Campaign pipeline failed: {campaign.product.name}",
            f"Campaign #{campaign.id} failed permanently: {error}",
        )
    except Campaign.DoesNotExist:
        pass
    logger.error("Pipeline permanently failed for Campaign %s: %s", campaign_id, error)


class PipelineTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        if args and self.name.endswith("run_full_pipeline"):
            _mark_failed(args[0], exc)


def _transient_error(error):
    text = str(error).lower()
    return any(token in text for token in ("timeout", "timed out", "connection", "temporarily unavailable", "429", "500", "502", "503", "504", "rate limit"))


def _reserve_paid_call(campaign_id):
    with transaction.atomic():
        campaign = Campaign.objects.select_for_update().get(pk=campaign_id)
        if campaign.paid_calls_used >= MAX_PAID_CALLS_PER_CAMPAIGN:
            raise QuotaExceededError(
                f"Per-campaign paid image-call limit ({MAX_PAID_CALLS_PER_CAMPAIGN}) reached"
            )
        campaign.paid_calls_used += 1
        campaign.save(update_fields=["paid_calls_used"])


def _get_or_create_generation(campaign, attempt, prompt):
    with transaction.atomic():
        Campaign.objects.select_for_update().get(pk=campaign.pk)
        generation = Generation.objects.filter(
            campaign=campaign,
            attempt=attempt,
            pipeline_managed=True,
        ).order_by("pk").first()
        if generation is None:
            generation = Generation.objects.create(
                campaign=campaign,
                attempt=attempt,
                prompt=prompt,
                status=Generation.Status.PENDING,
                pipeline_managed=True,
            )
        return generation


def _claim_generation(generation_id, task_id):
    with transaction.atomic():
        generation = Generation.objects.select_for_update().get(pk=generation_id)
        if generation.status == Generation.Status.GENERATING and generation.active_task_id not in ("", task_id):
            return False
        if generation.status not in (Generation.Status.PENDING, Generation.Status.GENERATING):
            return False
        generation.status = Generation.Status.GENERATING
        generation.active_task_id = task_id or ""
        generation.provider_used = IMAGE_PROVIDER.name
        generation.save(update_fields=["status", "active_task_id", "provider_used"])
        return True


def _handle_qc_failure(campaign, generation, attempt):
    if attempt < MAX_ATTEMPTS:
        corrective_feedback = (
            "Correct the specific QC failures from the previous image: "
            f"{generation.qc_notes} Preserve the original garment's irregular, hand-tied pattern."
        )
        logger.warning("Scheduling corrected generation for Campaign %s attempt %s", campaign.pk, attempt + 1)
        run_full_pipeline.delay(campaign.pk, attempt + 1, corrective_feedback)
        return
    campaign.status = Campaign.Status.REVIEW
    campaign.save(update_fields=["status"])
    _notify_review(campaign)
    logger.error("Campaign %s exhausted QC after %s attempts", campaign.pk, MAX_ATTEMPTS)


def _package_and_notify(generation):
    campaign = generation.campaign
    package_generation(generation)
    campaign.refresh_from_db()
    _notify_ready(campaign)


@shared_task(bind=True, base=PipelineTask, max_retries=3, default_retry_delay=30, acks_late=True, reject_on_worker_lost=True)
def run_full_pipeline(self, campaign_id, attempt=1, corrective_feedback=""):
    try:
        campaign = Campaign.objects.select_related("product").get(pk=campaign_id)
    except Campaign.DoesNotExist:
        logger.error("Campaign %s not found", campaign_id)
        return

    if campaign.status in (Campaign.Status.DONE, Campaign.Status.FAILED):
        return

    generation = _get_or_create_generation(campaign, attempt, build_prompt(campaign, corrective_feedback))
    if generation.status in (Generation.Status.PASSED_QC, Generation.Status.APPROVED):
        _package_and_notify(generation)
        return
    if generation.status == Generation.Status.REJECTED:
        return
    if attempt > 1:
        previous = Generation.objects.filter(campaign=campaign, attempt=attempt - 1, pipeline_managed=True).first()
        if previous is None or previous.status != Generation.Status.FAILED_QC:
            logger.warning("Skipping out-of-sequence attempt %s for Campaign %s", attempt, campaign_id)
            return
    if generation.status == Generation.Status.FAILED_QC:
        _handle_qc_failure(campaign, generation, attempt)
        return

    campaign.status = Campaign.Status.RUNNING
    campaign.save(update_fields=["status"])
    reference = campaign.product.photos.filter(kind="flatlay").first()
    if reference is None:
        raise ValueError(f"Product {campaign.product_id} has no flat-lay reference photo")

    if not _claim_generation(generation.pk, self.request.id):
        logger.info("Generation %s is already owned by another task", generation.pk)
        return
    generation.status = Generation.Status.GENERATING
    generation.provider_used = IMAGE_PROVIDER.name

    if not generation.image:
        try:
            if IMAGE_PROVIDER.name != "mock":
                _reserve_paid_call(campaign_id)
            image_bytes = IMAGE_PROVIDER.generate(generation.prompt, reference.image.path)
        except QuotaExceededError as exc:
            _mark_failed(campaign_id, exc)
            return
        except Exception as exc:
            if _transient_error(exc):
                logger.warning("Transient provider failure for Campaign %s attempt %s: %s", campaign_id, attempt, exc)
                raise self.retry(exc=exc)
            raise
        suffix = Path(reference.image.name).suffix or ".jpg"
        generation.image.save(f"generation_{generation.id}{suffix}", ContentFile(image_bytes), save=False)
        generation.save(update_fields=["image"])
        logger.info("Generated Campaign %s attempt %s with %s", campaign_id, attempt, IMAGE_PROVIDER.name)

    try:
        generation = run_qc(generation)
    except Exception as exc:
        if _transient_error(exc):
            logger.warning("Transient QC failure for Campaign %s attempt %s: %s", campaign_id, attempt, exc)
            raise self.retry(exc=exc)
        raise
    logger.info("QC Campaign %s attempt %s: status=%s score=%s notes=%s", campaign_id, attempt, generation.status, generation.qc_score, generation.qc_notes)
    campaign.refresh_from_db()

    if generation.status == Generation.Status.PASSED_QC:
        _package_and_notify(generation)
        return

    _handle_qc_failure(campaign, generation, attempt)


@shared_task(bind=True, base=PipelineTask, max_retries=3, default_retry_delay=30)
def run_qc_and_package(self, generation_id):
    """Compatibility task for generations imported by the management command."""
    try:
        generation = Generation.objects.select_related("campaign", "campaign__product").get(pk=generation_id)
    except Generation.DoesNotExist:
        logger.error("Generation %s not found", generation_id)
        return
    if generation.status in (Generation.Status.PASSED_QC, Generation.Status.APPROVED):
        _package_and_notify(generation)
        return
    generation = run_qc(generation)
    if generation.status == Generation.Status.PASSED_QC:
        _package_and_notify(generation)