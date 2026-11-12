from django.core.management.base import BaseCommand

from products.sample_setup import load_sample_setup


class Command(BaseCommand):
    help = "Load sample item classes, item-detail lookups and HTS codes (safe to re-run; never overwrites)."

    def handle(self, *args, **opts):
        load_sample_setup()
        self.stdout.write(self.style.SUCCESS("Sample product setup loaded."))
