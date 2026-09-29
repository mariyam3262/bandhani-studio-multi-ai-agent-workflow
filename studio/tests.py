import io
import tempfile
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from PIL import Image

from . import qc
from .generator import MockProvider
from .models import AdAsset, Campaign, Generation, Product, ProductPhoto
from .packager import package_generation
from .tasks import MAX_PAID_CALLS_PER_CAMPAIGN, run_full_pipeline


def image_bytes(color, size=(240, 320)):
	output = io.BytesIO()
	Image.new("RGB", size, color).save(output, format="JPEG")
	return output.getvalue()


class PipelineTests(TestCase):
	def setUp(self):
		temporary_media = tempfile.TemporaryDirectory()
		self.addCleanup(temporary_media.cleanup)
		media_settings = override_settings(MEDIA_ROOT=temporary_media.name)
		media_settings.enable()
		self.addCleanup(media_settings.disable)
		self.mail_patcher = patch("studio.tasks.mail_admins")
		self.mail_sender = self.mail_patcher.start()
		self.addCleanup(self.mail_patcher.stop)
		self.queue_patcher = patch("studio.tasks.run_full_pipeline.delay")
		self.queue_patcher.start()
		self.addCleanup(self.queue_patcher.stop)
		self.product = Product.objects.create(
			name="Red Bandhani Dupatta",
			pattern_type=Product.Pattern.EKDALI,
			colors=["red", "white"],
		)
		self.photo = ProductPhoto.objects.create(
			product=self.product,
			kind=ProductPhoto.Kind.FLATLAY,
			image=ContentFile(image_bytes((180, 20, 30)), name="flatlay.jpg"),
		)
		self.campaign = Campaign.objects.get(product=self.product)

	def test_flatlay_upload_queues_one_campaign_pipeline(self):
		self.queue_patcher.stop()
		with patch("studio.tasks.run_full_pipeline.delay") as queue_task, self.captureOnCommitCallbacks(execute=True):
			ProductPhoto.objects.create(
				product=self.product,
				kind=ProductPhoto.Kind.FLATLAY,
				image=ContentFile(image_bytes((180, 20, 30)), name="flatlay-again.jpg"),
			)
		self.assertEqual(Campaign.objects.filter(product=self.product).count(), 1)
		queue_task.assert_called_once_with(self.campaign.pk, 1)

	def test_qc_failure_queues_corrected_new_generation_and_success_packages_once(self):
		class QCSequence:
			def __init__(self):
				self.calls = 0

			def evaluate(self, reference_path, generated_path):
				self.calls += 1
				score = 0.2 if self.calls == 1 else 0.95
				return {"score": score, "passed": score >= 0.75, "notes": "hand-tied structure assessment"}

		with patch("studio.tasks.IMAGE_PROVIDER", MockProvider()), patch("studio.qc.QC_PROVIDER", QCSequence()), patch(
			"studio.tasks.run_full_pipeline.delay"
		) as queue_task:
			run_full_pipeline.apply(args=(self.campaign.pk, 1, ""), throw=True)
			queue_task.assert_called_once()
			queued_args = queue_task.call_args.args
			self.assertEqual(queued_args[:2], (self.campaign.pk, 2))
			self.assertIn("Structural similarity", queued_args[2])

			run_full_pipeline.apply(args=queued_args, throw=True)

		generations = list(Generation.objects.filter(campaign=self.campaign).order_by("attempt"))
		self.assertEqual([generation.attempt for generation in generations], [1, 2])
		self.assertEqual(generations[0].status, Generation.Status.FAILED_QC)
		self.assertIn("hand-tied structure assessment", generations[1].prompt)
		self.assertEqual(generations[1].status, Generation.Status.PASSED_QC)
		self.assertEqual(AdAsset.objects.filter(generation=generations[1]).count(), 3)
		self.assertEqual(Campaign.objects.get(pk=self.campaign.pk).status, Campaign.Status.REVIEW)

		package_generation(generations[1])
		self.assertEqual(AdAsset.objects.filter(generation=generations[1]).count(), 3)

	def test_color_similarity_is_deterministic_and_distinguishes_palettes(self):
		with tempfile.NamedTemporaryFile(suffix=".jpg") as red, tempfile.NamedTemporaryFile(suffix=".jpg") as same, tempfile.NamedTemporaryFile(suffix=".jpg") as blue:
			red.write(image_bytes((220, 20, 30)))
			red.flush()
			same.write(image_bytes((220, 20, 30)))
			same.flush()
			blue.write(image_bytes((10, 30, 220)))
			blue.flush()
			self.assertGreaterEqual(qc.color_similarity(red.name, same.name), qc.COLOR_PASS_THRESHOLD)
			self.assertLess(qc.color_similarity(red.name, blue.name), qc.COLOR_PASS_THRESHOLD)

	def test_third_qc_failure_flags_campaign_for_human_review(self):
		for attempt in (1, 2):
			Generation.objects.create(
				campaign=self.campaign,
				attempt=attempt,
				prompt=f"Attempt {attempt}",
				status=Generation.Status.FAILED_QC,
				qc_notes="Pattern structure mismatch",
				pipeline_managed=True,
			)

		def fail_qc(generation):
			generation.status = Generation.Status.FAILED_QC
			generation.qc_score = 0.4
			generation.qc_notes = "Pattern structure mismatch"
			generation.save(update_fields=["status", "qc_score", "qc_notes"])
			return generation

		with patch("studio.tasks.IMAGE_PROVIDER", MockProvider()), patch("studio.tasks.run_qc", side_effect=fail_qc):
			run_full_pipeline.apply(args=(self.campaign.pk, 3, "Correct the pattern structure"), throw=True)

		campaign = Campaign.objects.get(pk=self.campaign.pk)
		final_generation = Generation.objects.get(campaign=campaign, attempt=3)
		self.assertEqual(campaign.status, Campaign.Status.REVIEW)
		self.assertEqual(final_generation.status, Generation.Status.FAILED_QC)
		self.mail_sender.assert_called_once()

	def test_paid_generation_cap_fails_campaign_before_provider_call(self):
		class PaidProvider:
			name = "gemini:test-model"

			def generate(self, prompt, reference_image_path):
				raise AssertionError("The paid provider must not be called after the cap")

		Campaign.objects.filter(pk=self.campaign.pk).update(paid_calls_used=MAX_PAID_CALLS_PER_CAMPAIGN)
		with patch("studio.tasks.IMAGE_PROVIDER", PaidProvider()):
			run_full_pipeline.apply(args=(self.campaign.pk, 1, ""), throw=True)

		self.assertEqual(Campaign.objects.get(pk=self.campaign.pk).status, Campaign.Status.FAILED)
		self.mail_sender.assert_called_once()

	def test_model_rejects_unapproved_color_values(self):
		product = Product(name="Bad Color", pattern_type=Product.Pattern.OTHER, colors=["purpal"])
		with self.assertRaises(ValidationError):
			product.full_clean()
