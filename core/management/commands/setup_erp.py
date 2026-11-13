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
        from core.models import Membership
        from core.permissions import ensure_roles
        from core.tenancy import use_company

        company = Company.get_default()
        # Every company keeps its own chart of accounts and roles: top them up one company at a time
        for c in Company.objects.order_by("pk"):
            with use_company(c):
                setup_chart_of_accounts()
                ensure_roles(c)
        roles = ensure_roles(company)
        if opts.get("admin"):
            User = get_user_model()
            user = User.objects.filter(username=opts["admin"]).first()
            if user is None:
                user = User.objects.create_superuser(opts["admin"], password=opts["password"])
                self.stdout.write(f"Superuser '{opts['admin']}' created.")
            Membership.objects.get_or_create(user=user, company=company, defaults={"role": roles["Owner"]})
        self.stdout.write(self.style.SUCCESS("Synqaro is set up."))
