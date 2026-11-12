import datetime
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting import services
from accounting.models import CustomerInvoice, CustomerInvoiceLine
from core.testing import TODAY, Fixture


class LedgerTests(TestCase):
    def setUp(self):
        self.f = Fixture()

    def test_unbalanced_entry_rejected(self):
        with self.assertRaises(ValidationError):
            services.post_journal(TODAY, "bad", [{"account": services.acct("CASH"), "debit": D(10)},
                                                 {"account": services.acct("EQUITY"), "credit": D(9)}])

    def test_reports_balance_and_aging(self):
        services.post_journal(TODAY, "capital", [{"account": services.acct("CASH"), "debit": D(1000)},
                                                 {"account": services.acct("EQUITY"), "credit": D(1000)}])
        inv = CustomerInvoice.objects.create(customer=self.f.customer, invoice_date=TODAY - datetime.timedelta(70))
        CustomerInvoiceLine.objects.create(invoice=inv, description="Consulting", unit_price=D("500"))
        services.post_customer_invoice(inv)
        tb = services.trial_balance()
        self.assertEqual(tb["total_debit"], tb["total_credit"])
        bs = services.balance_sheet(TODAY)
        self.assertEqual(bs["total_assets"], bs["total_liabilities_equity"])
        self.assertEqual(bs["earnings"], D("500"))
        pl = services.income_statement(TODAY - datetime.timedelta(365), TODAY)
        self.assertEqual(pl["net_income"], D("500"))
        aging = services.ar_aging(TODAY)
        self.assertEqual(aging["totals"]["d60"], D("500.00"))  # due 40 days ago

    def test_ledger_running_balance(self):
        cash = services.acct("CASH")
        for amt in (100, 50):
            services.post_journal(TODAY, "in", [{"account": cash, "debit": D(amt)},
                                                {"account": services.acct("EQUITY"), "credit": D(amt)}])
        ledger = services.account_ledger(cash, TODAY, TODAY)
        self.assertEqual([r["balance"] for r in ledger["rows"]], [D(100), D(150)])
