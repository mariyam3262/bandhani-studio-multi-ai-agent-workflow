from django.core.management.base import BaseCommand, CommandError

from studio.generator import prepare_generation
from studio.models import Campaign


class Command(BaseCommand):
    help = "Create a pending generation and print the prompt + reference photo path"

    def add_arguments(self, parser):
        parser.add_argument("campaign_id", type=int)

    def handle(self, *args, **options):
        try:
            campaign = Campaign.objects.get(pk=options["campaign_id"])
        except Campaign.DoesNotExist:
            raise CommandError("Campaign not found")

        generation, photo = prepare_generation(campaign)

        self.stdout.write(self.style.SUCCESS(f"\nGeneration {generation.id} created (status: pending)\n"))
        self.stdout.write("Reference photo to attach in Gemini:")
        self.stdout.write(f"  {photo.image.path}\n")
        self.stdout.write("Prompt to paste into Gemini app / AI Studio:")
        self.stdout.write("-" * 60)
        self.stdout.write(generation.prompt)
        self.stdout.write("-" * 60)
        self.stdout.write(
            f"\nWhen you have the downloaded image, run:\n"
            f"  docker-compose run --rm web python manage.py import_generation {generation.id} <path-to-downloaded-file>"
        )