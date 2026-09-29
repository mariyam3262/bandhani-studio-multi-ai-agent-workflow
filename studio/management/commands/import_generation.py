import shutil
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from studio.models import Generation


class Command(BaseCommand):
    help = "Attach a manually downloaded image to a pending Generation"

    def add_arguments(self, parser):
        parser.add_argument("generation_id", type=int)
        parser.add_argument("file_path", type=str)

    def handle(self, *args, **options):
        try:
            generation = Generation.objects.get(pk=options["generation_id"])
        except Generation.DoesNotExist:
            raise CommandError("Generation not found")

        src = Path(options["file_path"])
        if not src.exists():
            raise CommandError(f"File not found: {src}")

        with open(src, "rb") as f:
            generation.image.save(f"gen_{generation.id}{src.suffix}", ContentFile(f.read()), save=True)

        from studio.tasks import run_qc_and_package
        run_qc_and_package.delay(generation.id)

        self.stdout.write(self.style.SUCCESS(f"Generation {generation.id} image attached: {generation.image.name}"))
        self.stdout.write("QC + packaging queued as a background task - check the worker log for the result.")