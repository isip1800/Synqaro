import datetime
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from core.models import Company
from core.testing import TODAY, Fixture

from . import banking, periods
from .models import Account, BankReconciliation, CustomerPayment, JournalEntry, PeriodClose
from .services import acct, post_journal


def cash(amount, date, memo="x"):
    amount = D(amount)
    return post_journal(date, memo, [{"account": acct("CASH"), "debit": amount},
                                     {"account": acct("SALES"), "credit": amount}])


class PeriodLockTests(TestCase):
    def setUp(self):
        self.f = Fixture()

    def lock(self, d):
        Company.objects.update(lock_date=d)
        Company.clear_cache()

    def test_posting_blocked_in_closed_period(self):
        cash(100, datetime.date(2026, 4, 10))
        self.lock(datetime.date(2026, 4, 30))
        with self.assertRaises(ValidationError) as ctx:
            cash(50, datetime.date(2026, 4, 30))
        self.assertIn("closed through 2026-04-30", str(ctx.exception))
        cash(50, datetime.date(2026, 5, 1))  # open period is fine
        e = JournalEntry.objects.filter(date=datetime.date(2026, 4, 10)).get()
        with self.assertRaises(ValidationError):
            e.delete()
        e.date = datetime.date(2026, 5, 2)  # moving an entry out of a closed period is also blocked
        with self.assertRaises(ValidationError):
            e.save()

    def test_document_posting_blocked(self):
        from .services import post_customer_payment

        self.lock(TODAY)
        pay = CustomerPayment.objects.create(customer=self.f.customer, date=TODAY, amount=D(10),
                                             deposit_account=acct("CASH"))
        with self.assertRaises(ValidationError):
            post_customer_payment(pay)
        pay.refresh_from_db()
        self.assertEqual(pay.status, CustomerPayment.DRAFT)  # rolled back

    def test_close_and_reopen(self):
        today = datetime.date.today()
        end = periods.prev_month_end(today)
        with self.assertRaises(ValidationError):
            periods.close_period(today, self.f.user)  # month not over
        cash(10, end)
        CustomerPayment.objects.create(customer=self.f.customer, date=end, amount=D(10),
                                       deposit_account=acct("CASH"))
        items = periods.checklist(end)
        self.assertTrue(any(not i["ok"] and "Draft customer payments" in i["title"] for i in items))
        self.assertTrue(any(not i["ok"] and "Bank reconciled" in i["title"] for i in items))
        with self.assertRaises(ValidationError):
            periods.close_period(end, self.f.user)  # open items, not forced
        pc = periods.close_period(end, self.f.user, "month end", force=True)
        self.assertEqual(periods.lock_date(), end)
        self.assertTrue(pc.warnings)
        with self.assertRaises(ValidationError):
            cash(5, end)
        with self.assertRaises(ValidationError):
            periods.reopen_period(self.f.user, "")  # reason required
        periods.reopen_period(self.f.user, "late bill")
        self.assertEqual(periods.lock_date(), periods.prev_month_end(end))
        cash(5, end)
        self.assertEqual(PeriodClose.objects.count(), 2)

    def test_manual_journal_form_shows_error(self):
        self.lock(TODAY)
        self.client.force_login(self.f.user)
        r = self.client.post(reverse("accounting:journalentry_create"), {
            "date": TODAY.isoformat(), "memo": "test", "lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "0",
            "lines-0-account": acct("CASH").pk, "lines-0-debit": "5", "lines-0-credit": "0",
            "lines-1-account": acct("SALES").pk, "lines-1-debit": "0", "lines-1-credit": "5"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "closed through")
        self.assertFalse(JournalEntry.objects.exists())

    def test_close_permissions_and_page(self):
        self.client.force_login(self.f.user)
        self.assertEqual(self.client.get(reverse("accounting:period_close")).status_code, 200)
        self.f.member("ro", "Read Only")
        self.client.login(username="ro", password="pw-test-1234")
        self.assertEqual(self.client.get(reverse("accounting:period_close")).status_code, 200)
        r = self.client.post(reverse("accounting:period_close_action"),
                             {"period": periods.prev_month_end(datetime.date.today()).isoformat(), "force": "1"})
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(periods.lock_date())


class BankRecTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        self.bank = acct("CASH")
        self.d = datetime.date(2026, 5, 1)
        self.e1 = cash(1000, self.d)                       # deposit
        self.e2 = post_journal(self.d + datetime.timedelta(days=3), "rent",
                               [{"account": Account.objects.get(code="6000"), "debit": D(300)},
                                {"account": self.bank, "credit": D(300)}])
        self.e3 = cash(200, datetime.date(2026, 5, 30))    # deposit in transit

    def line(self, entry):
        return entry.lines.get(account=self.bank)

    def test_reconcile_with_fee_and_outstanding(self):
        rec = banking.start_reconciliation(self.bank, datetime.date(2026, 5, 31), D("685"), self.f.user)
        banking.set_cleared(rec, [self.line(self.e1).pk, self.line(self.e2).pk])
        rec.refresh_from_db()
        self.assertEqual(rec.cleared_balance, D("700"))
        self.assertEqual(rec.difference, D("-15"))
        with self.assertRaises(ValidationError):
            banking.complete_reconciliation(rec)
        banking.record_bank_item(rec, Account.objects.get(code="6400"), D("-15"), "Wire fee")
        banking.complete_reconciliation(rec, self.f.user)
        rec.refresh_from_db()
        self.assertEqual(rec.status, BankReconciliation.COMPLETED)
        out = list(banking.outstanding(rec))
        self.assertEqual([ln.debit for ln in out], [D("200.00")])
        self.assertEqual(rec.statement_balance + D(200), self.bank.balance(end=rec.statement_date))

        # Next month starts from the statement balance; cleared lines aren't offered again
        with self.assertRaises(ValidationError):
            banking.start_reconciliation(self.bank, datetime.date(2026, 5, 15), D(0))
        rec2 = banking.start_reconciliation(self.bank, datetime.date(2026, 6, 30), D("885"))
        self.assertEqual(rec2.beginning_balance, D("685"))
        self.assertEqual(list(banking.candidate_lines(rec2)), [self.line(self.e3)])
        with self.assertRaises(ValidationError):
            banking.undo_reconciliation(rec)  # rec2 in progress
        banking.delete_reconciliation(rec2)
        banking.undo_reconciliation(rec)
        rec.refresh_from_db()
        self.assertEqual(rec.status, BankReconciliation.IN_PROGRESS)

    def test_import_statement_csv(self):
        rec = banking.start_reconciliation(self.bank, datetime.date(2026, 5, 31), D("700"))
        csv = ("Account activity export\n"
               "Posting Date,Description,Withdrawals,Deposits,Balance\n"
               "05/02/2026,DEPOSIT,,\"1,000.00\",1000.00\n"
               "05/06/2026,CHECK 1001,300.00,,700.00\n"
               "05/20/2026,MYSTERY,12.00,,688.00\n")
        matched, unmatched = banking.import_statement(rec, csv.encode())
        self.assertEqual(matched, 2)
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]["amount"], "-12.00")
        rec.refresh_from_db()
        self.assertEqual(rec.difference, D(0))
        rows = banking.parse_statement(b"Date,Amount,Description\n2026-05-02,(45.10),Fee\n2026-05-03,$20,Int\n")
        self.assertEqual([r["amount"] for r in rows], [D("-45.10"), D("20.00")])

    def test_screens_and_permissions(self):
        self.client.force_login(self.f.user)
        r = self.client.post(reverse("accounting:bankrec_start"), {"account": self.bank.pk,
                                                                    "statement_date": "2026-05-31",
                                                                    "statement_balance": "700"})
        rec = BankReconciliation.objects.get()
        self.assertRedirects(r, rec.get_absolute_url())
        self.assertEqual(self.client.get(reverse("accounting:bankreconciliation_list")).status_code, 200)
        self.assertContains(self.client.get(rec.get_absolute_url()), "Finish reconciliation")
        self.client.post(reverse("accounting:bankrec_save", args=[rec.pk]),
                         {"cleared": [self.line(self.e1).pk, self.line(self.e2).pk], "finish": "1"})
        rec.refresh_from_db()
        self.assertEqual(rec.status, BankReconciliation.COMPLETED)
        self.assertContains(self.client.get(rec.get_absolute_url()), "Deposits in transit")
        self.f.member("sales", "Sales")
        self.client.login(username="sales", password="pw-test-1234")
        self.assertEqual(self.client.get(reverse("accounting:bankreconciliation_list")).status_code, 403)
