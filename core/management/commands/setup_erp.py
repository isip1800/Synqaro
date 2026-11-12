from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounting.services import setup_chart_of_accounts
from core.models import Company, Currency


class Command(BaseCommand):
    help = "Initialise company, base currency and chart of accounts (safe to run repeatedly)."

    def add_arguments(self, parser):
        parser.add_argument("--admin", help="Create a superuser with this username (password = --password)")
        parser.add_argument("--password", default="admin12345")

    def handle(self, *args, **opts):
        for code, name, sym in [("USD", "US Dollar", "$"), ("EUR", "Euro", "€"), ("CNY", "Chinese Yuan", "¥"),
                                ("GBP", "British Pound", "£")]:
            Currency.objects.get_or_create(code=code, defaults={"name": name, "symbol": sym})
        Company.get()
        setup_chart_of_accounts()
        if opts.get("admin"):
            User = get_user_model()
            if not User.objects.filter(username=opts["admin"]).exists():
                User.objects.create_superuser(opts["admin"], password=opts["password"])
                self.stdout.write(f"Superuser '{opts['admin']}' created.")
        self.stdout.write(self.style.SUCCESS("Synqaro is set up."))
