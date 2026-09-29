from django.core.management.base import BaseCommand, CommandError

from studio.models import Generation
from studio.packager import package_generation


class Command(BaseCommand):
    help = "Crop an approved generation into ad sizes and attach a caption"

    def add_arguments(self, parser):
        parser.add_argument("generation_id", type=int)

    def handle(self, *args, **options):
        try:
            generation = Generation.objects.get(pk=options["generation_id"])
        except Generation.DoesNotExist:
            raise CommandError("Generation not found")

        assets = package_generation(generation)

        self.stdout.write(self.style.SUCCESS(f"Created {len(assets)} ad assets:"))
        for asset in assets:
            self.stdout.write(f"  {asset.aspect_ratio}: {asset.image.name}")
        self.stdout.write("\nCaption:")
        self.stdout.write(assets[0].caption)