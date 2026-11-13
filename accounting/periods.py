"""Month-end close: the period lock and the close checklist."""
import calendar
import datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from core.utils import ZERO


def month_end(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def prev_month_end(d):
    return d.replace(day=1) - datetime.timedelta(days=1)


def lock_date(company_id=None):
    from core.models import Company
    from core.tenancy import current_company_id, default_company_id

    cid = company_id or current_company_id() or default_company_id()
    return Company.objects.filter(pk=cid).values_list("lock_date", flat=True).first()


def check_period_open(date, company_id=None):
    if date is None:
        return
    locked = lock_date(company_id)
    if locked and date <= locked:
        raise ValidationError(
            f"The books are closed through {locked:%Y-%m-%d}; nothing can be posted on {date:%Y-%m-%d}. "
            "Use a date in an open period, or reopen the period (Accounting → Month-End Close).")


def checklist(period_end):
    """Items to review before closing the month ending `period_end`. Returns a list of dicts."""
    from accounting.models import (
        Account, BankReconciliation, CustomerInvoice, CustomerPayment, JournalLine, VendorBill, VendorPayment,
    )
    from accounting.services import acct
    from django.db.models import Max, Sum
    from django.urls import reverse
    from inventory.models import StockAdjustment, StockTransfer
    from returns.models import CreditMemo, CustomerReturn, VendorCredit, VendorReturn

    items = []

    def add(ok, title, detail="", url="", severity="warn"):
        items.append({"ok": ok, "title": title, "detail": detail, "url": url, "severity": "ok" if ok else severity})

    # Bank accounts reconciled through month end
    for bank in Account.objects.filter(is_bank=True, is_active=True).order_by("code"):
        if not bank.lines.filter(entry__date__lte=period_end).exists():
            continue
        last = BankReconciliation.objects.filter(account=bank, status=BankReconciliation.COMPLETED) \
            .aggregate(m=Max("statement_date"))["m"]
        ok = bool(last and last >= period_end)
        add(ok, f"Bank reconciled: {bank}",
            f"Last reconciled to {last:%Y-%m-%d}" if last else "Never reconciled",
            reverse("accounting:bankreconciliation_list"))

    # Draft documents dated in or before the period (they would post into a closed month)
    drafts = [
        ("Draft customer invoices", CustomerInvoice.objects.filter(status="draft", invoice_date__lte=period_end),
         "accounting:customerinvoice_list"),
        ("Draft customer payments", CustomerPayment.objects.filter(status="draft", date__lte=period_end),
         "accounting:customerpayment_list"),
        ("Draft vendor bills", VendorBill.objects.filter(status="draft", bill_date__lte=period_end),
         "accounting:vendorbill_list"),
        ("Draft vendor payments", VendorPayment.objects.filter(status="draft", date__lte=period_end),
         "accounting:vendorpayment_list"),
        ("Draft credit memos", CreditMemo.objects.filter(status="draft", date__lte=period_end),
         "returns:creditmemo_list"),
        ("Draft vendor credits", VendorCredit.objects.filter(status="draft", date__lte=period_end),
         "returns:vendorcredit_list"),
        ("Customer returns not received", CustomerReturn.objects.filter(status="draft", date__lte=period_end),
         "returns:customerreturn_list"),
        ("Vendor returns not shipped", VendorReturn.objects.filter(status="draft", date__lte=period_end),
         "returns:vendorreturn_list"),
        ("Draft stock adjustments", StockAdjustment.objects.filter(status="draft", date__lte=period_end),
         "inventory:stockadjustment_list"),
        ("Draft stock transfers", StockTransfer.objects.filter(status="draft", date__lte=period_end),
         "inventory:stocktransfer_list"),
    ]
    for title, qs, url in drafts:
        n = qs.count()
        add(n == 0, title, f"{n} dated on or before {period_end:%Y-%m-%d}" if n else "None", reverse(url) +
            "?status=draft" if n else reverse(url))

    # Ledger in balance
    agg = JournalLine.objects.filter(entry__date__lte=period_end).aggregate(dr=Sum("debit"), cr=Sum("credit"))
    dr, cr = agg["dr"] or ZERO, agg["cr"] or ZERO
    add(dr == cr, "Trial balance in balance", f"Debits {dr:,.2f} / credits {cr:,.2f}",
        reverse("accounting:trial_balance") + f"?as_of={period_end:%Y-%m-%d}", severity="error")

    # Information: goods received not billed, vendor returns awaiting credit
    for key, label in (("GRNI", "Goods received not yet billed"),
                       ("VENDOR_RETURNS", "Vendor returns awaiting credit")):
        bal = acct(key).balance(end=period_end)
        add(bal == 0, label, f"Balance {bal:,.2f} — review before closing" if bal else "Zero",
            reverse("accounting:account_detail", args=[acct(key).pk]), severity="info")
    return items


@transaction.atomic
def close_period(period_end, user=None, notes="", force=False):
    from core.models import Company

    from .models import PeriodClose

    company = Company.objects.select_for_update().get(pk=Company.get().pk)
    period_end = month_end(period_end)
    if company.lock_date and period_end <= company.lock_date:
        raise ValidationError(f"The books are already closed through {company.lock_date:%Y-%m-%d}.")
    if period_end >= datetime.date.today():
        raise ValidationError("You can only close a month that has ended.")
    items = checklist(period_end)
    if any(i["severity"] == "error" for i in items):
        raise ValidationError("The trial balance is out of balance; the period can't be closed.")
    open_items = [f"{i['title']}: {i['detail']}" for i in items if i["severity"] == "warn"]
    if open_items and not force:
        raise ValidationError("Some checklist items are still open. Tick “close anyway” to close regardless.")
    company.lock_date = period_end
    company.save(update_fields=["lock_date"])
    Company.clear_cache()
    return PeriodClose.objects.create(action=PeriodClose.CLOSE, period_end=period_end, lock_date=period_end,
                                      notes=notes, warnings=open_items, user=user)


@transaction.atomic
def reopen_period(user=None, notes=""):
    """Reopen the most recently closed month (lock moves back one month)."""
    from core.models import Company

    from .models import PeriodClose

    if not notes.strip():
        raise ValidationError("Give a reason for reopening the period.")
    company = Company.objects.select_for_update().get(pk=Company.get().pk)
    if not company.lock_date:
        raise ValidationError("No period is closed.")
    reopened = company.lock_date
    company.lock_date = prev_month_end(reopened)
    company.save(update_fields=["lock_date"])
    Company.clear_cache()
    return PeriodClose.objects.create(action=PeriodClose.REOPEN, period_end=reopened, lock_date=company.lock_date,
                                      notes=notes, user=user)
