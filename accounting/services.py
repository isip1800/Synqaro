"""Double-entry accounting engine: chart of accounts, posting, AR/AP documents and reports."""
import datetime
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from core.utils import ZERO, money

from .models import (
    Account,
    CustomerInvoice,
    CustomerPayment,
    JournalEntry,
    JournalLine,
    VendorBill,
    VendorPayment,
)

# system_key: (code, name, type, is_bank)
SYSTEM_ACCOUNTS = {
    "CASH": ("1000", "Operating Bank Account", Account.ASSET, True),
    "AR": ("1100", "Accounts Receivable", Account.ASSET, False),
    "INVENTORY": ("1200", "Inventory", Account.ASSET, False),
    "VENDOR_DEPOSITS": ("1300", "Vendor Deposits / Prepayments", Account.ASSET, False),
    "AP": ("2000", "Accounts Payable", Account.LIABILITY, False),
    "GRNI": ("2100", "Goods Received Not Invoiced", Account.LIABILITY, False),
    "ACCRUED_LANDED": ("2150", "Accrued Landed Costs (freight, duty, brokerage)", Account.LIABILITY, False),
    "SALES_TAX": ("2200", "Sales Tax Payable", Account.LIABILITY, False),
    "COMMISSIONS_PAYABLE": ("2300", "Commissions Payable", Account.LIABILITY, False),
    "EQUITY": ("3000", "Owner's Equity", Account.EQUITY, False),
    "RETAINED": ("3100", "Retained Earnings", Account.EQUITY, False),
    "OPENING": ("3900", "Opening Balance Equity", Account.EQUITY, False),
    "SALES": ("4000", "Sales Revenue", Account.INCOME, False),
    "COGS": ("5000", "Cost of Goods Sold", Account.EXPENSE, False),
    "INV_ADJ": ("5100", "Inventory Adjustments / Shrinkage", Account.EXPENSE, False),
    "PPV": ("5200", "Purchase Price Variance", Account.EXPENSE, False),
    "COMMISSION_EXP": ("6100", "Sales Commissions", Account.EXPENSE, False),
    "FX": ("7000", "Realized FX Gain / Loss", Account.EXPENSE, False),
}

OTHER_ACCOUNTS = [
    ("1010", "Petty Cash", Account.ASSET, True),
    ("1020", "USD Savings Account", Account.ASSET, True),
    ("1500", "Equipment", Account.ASSET, False),
    ("2500", "Loans Payable", Account.LIABILITY, False),
    ("4100", "Freight Charged to Customers", Account.INCOME, False),
    ("4200", "Other Income", Account.INCOME, False),
    ("6000", "Rent", Account.EXPENSE, False),
    ("6200", "Salaries & Wages", Account.EXPENSE, False),
    ("6300", "Office & Admin", Account.EXPENSE, False),
    ("6400", "Bank Fees", Account.EXPENSE, False),
    ("6500", "Outbound Freight", Account.EXPENSE, False),
    ("6600", "Warehouse Expenses", Account.EXPENSE, False),
]


def setup_chart_of_accounts():
    for key, (code, name, atype, is_bank) in SYSTEM_ACCOUNTS.items():
        acc = Account.objects.filter(system_key=key).first() or Account.objects.filter(code=code).first()
        if acc is None:
            Account.objects.create(code=code, name=name, account_type=atype, is_bank=is_bank, system_key=key)
        elif acc.system_key != key:
            acc.system_key = key
            acc.save(update_fields=["system_key"])
    for code, name, atype, is_bank in OTHER_ACCOUNTS:
        Account.objects.get_or_create(code=code, defaults={"name": name, "account_type": atype, "is_bank": is_bank})


def acct(key):
    try:
        return Account.objects.get(system_key=key)
    except Account.DoesNotExist:
        setup_chart_of_accounts()
        return Account.objects.get(system_key=key)


def post_journal(date, memo, lines, source="manual", reference="", user=None):
    """Create a balanced journal entry. `lines` are dicts with account, debit/credit, optional customer/vendor."""
    clean = []
    for line in lines:
        debit, credit = money(line.get("debit", ZERO)), money(line.get("credit", ZERO))
        if debit < 0:
            debit, credit = ZERO, credit - debit
        if credit < 0:
            debit, credit = debit - credit, ZERO
        if debit == 0 and credit == 0:
            continue
        clean.append({**line, "debit": debit, "credit": credit})
    total_dr = sum((line["debit"] for line in clean), ZERO)
    total_cr = sum((line["credit"] for line in clean), ZERO)
    if total_dr != total_cr:
        raise ValidationError(f"Journal entry is not balanced (debits {total_dr} ≠ credits {total_cr}).")
    if not clean:
        return None
    entry = JournalEntry.objects.create(date=date, memo=memo[:255], source=source, reference=reference,
                                        created_by=user)
    JournalLine.objects.bulk_create([
        JournalLine(
            entry=entry,
            account=line["account"],
            debit=line["debit"],
            credit=line["credit"],
            description=line.get("description", "")[:255],
            customer=line.get("customer"),
            vendor=line.get("vendor"),
        )
        for line in clean
    ])
    return entry


def validate_manual_entry(entry):
    agg = entry.lines.aggregate(dr=Sum("debit"), cr=Sum("credit"))
    if (agg["dr"] or ZERO) != (agg["cr"] or ZERO):
        raise ValidationError(f"Entry is not balanced: debits {agg['dr'] or 0} vs credits {agg['cr'] or 0}.")
    if not entry.lines.exists():
        raise ValidationError("Entry has no lines.")


# --------------------------------------------------------------------------- Receivables

@transaction.atomic
def post_customer_invoice(invoice, user=None):
    invoice = CustomerInvoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != CustomerInvoice.DRAFT:
        raise ValidationError("Only draft invoices can be posted.")
    lines = list(invoice.lines.select_related("so_line", "account"))
    if not lines:
        raise ValidationError("Invoice has no lines.")
    for line in lines:
        if line.so_line_id:
            so_line = type(line.so_line).objects.select_for_update().get(pk=line.so_line_id)
            if line.quantity > so_line.qty_to_invoice:
                raise ValidationError(
                    f"{so_line.product.sku}: invoicing {line.quantity} but only {so_line.qty_to_invoice} "
                    f"shipped and not yet invoiced."
                )
            line.unit_cost = so_line.avg_unit_cost_shipped
            line.save(update_fields=["unit_cost"])
            so_line.qty_invoiced += line.quantity
            so_line.save(update_fields=["qty_invoiced"])
    if not invoice.due_date:
        invoice.due_date = invoice.invoice_date + datetime.timedelta(days=invoice.customer.payment_terms_days)

    income = defaultdict(lambda: ZERO)
    sales = acct("SALES")
    for line in lines:
        income[line.account or sales] += line.amount
    tax = invoice.tax
    journal = [{"account": acct("AR"), "debit": invoice.subtotal + tax, "customer": invoice.customer,
                "description": f"Invoice {invoice.number}"}]
    journal += [{"account": a, "credit": amt, "description": f"Invoice {invoice.number}"} for a, amt in income.items()]
    if tax:
        journal.append({"account": acct("SALES_TAX"), "credit": tax, "description": f"Tax {invoice.number}"})
    invoice.journal_entry = post_journal(invoice.invoice_date, f"Invoice {invoice.number} — {invoice.customer}",
                                         journal, "customer_invoice", invoice.number, user)
    invoice.status = CustomerInvoice.POSTED if invoice.total > 0 else CustomerInvoice.PAID
    invoice.save()
    if invoice.sales_order_id:
        invoice.sales_order.refresh_status()

    from salesteam.models import SalesRep
    from salesteam.services import accrue_commission

    if invoice.sales_rep and invoice.sales_rep.commission_trigger == SalesRep.ON_INVOICE:
        accrue_commission(invoice, 1, invoice.invoice_date, user)
    return invoice


def _apply_invoice_payment(invoice, amount):
    invoice.amount_paid += amount
    invoice.status = CustomerInvoice.PAID if invoice.amount_paid >= invoice.total else CustomerInvoice.PARTIAL
    invoice.save(update_fields=["amount_paid", "status"])


@transaction.atomic
def post_customer_payment(payment, user=None):
    payment = CustomerPayment.objects.select_for_update().get(pk=payment.pk)
    if payment.status != CustomerPayment.DRAFT:
        raise ValidationError("Payment already posted.")
    if payment.amount <= 0:
        raise ValidationError("Payment amount must be positive.")
    allocations = list(payment.allocations.select_related("invoice"))
    if sum((a.amount for a in allocations), ZERO) > payment.amount:
        raise ValidationError("Allocated amount exceeds the payment amount.")
    for alloc in allocations:
        inv = CustomerInvoice.objects.select_for_update().get(pk=alloc.invoice_id)
        if inv.customer_id != payment.customer_id:
            raise ValidationError(f"Invoice {inv.number} belongs to another customer.")
        if inv.status not in CustomerInvoice.OPEN_STATUSES:
            raise ValidationError(f"Invoice {inv.number} is not open.")
        if alloc.amount <= 0 or alloc.amount > inv.balance_due:
            raise ValidationError(f"Allocation to {inv.number} must be between 0 and {inv.balance_due}.")
        alloc.invoice = inv

    payment.journal_entry = post_journal(
        payment.date,
        f"Payment {payment.number} from {payment.customer}",
        [
            {"account": payment.deposit_account, "debit": payment.amount},
            {"account": acct("AR"), "credit": payment.amount, "customer": payment.customer},
        ],
        "customer_payment", payment.number, user,
    )
    payment.status = CustomerPayment.POSTED
    payment.save()

    from salesteam.models import SalesRep
    from salesteam.services import accrue_commission

    for alloc in allocations:
        inv = alloc.invoice
        _apply_invoice_payment(inv, alloc.amount)
        if inv.sales_rep and inv.sales_rep.commission_trigger == SalesRep.ON_PAYMENT and inv.total:
            accrue_commission(inv, alloc.amount / inv.total, payment.date, user)
    return payment


def auto_allocate_customer_payment(payment):
    """Allocate unapplied payment to the customer's oldest open invoices."""
    from .models import CustomerPaymentAllocation

    remaining = payment.unapplied
    already = set(payment.allocations.values_list("invoice_id", flat=True))
    for inv in CustomerInvoice.objects.filter(customer=payment.customer, status__in=CustomerInvoice.OPEN_STATUSES) \
            .exclude(pk__in=already).order_by("due_date", "invoice_date", "id"):
        if remaining <= 0:
            break
        amt = min(remaining, inv.balance_due)
        if amt > 0:
            CustomerPaymentAllocation.objects.create(payment=payment, invoice=inv, amount=amt)
            remaining -= amt


# --------------------------------------------------------------------------- Payables

@transaction.atomic
def post_vendor_bill(bill, user=None):
    from purchasing.models import PurchaseOrderLine

    bill = VendorBill.objects.select_for_update().get(pk=bill.pk)
    if bill.status != VendorBill.DRAFT:
        raise ValidationError("Only draft bills can be posted.")
    lines = list(bill.lines.select_related("po_line__order", "po_line__product", "account"))
    if not lines:
        raise ValidationError("Bill has no lines.")
    if VendorBill.objects.filter(vendor=bill.vendor, vendor_invoice_number=bill.vendor_invoice_number) \
            .exclude(pk=bill.pk).exclude(status__in=[VendorBill.DRAFT, VendorBill.VOID]).exists():
        raise ValidationError(f"Vendor invoice {bill.vendor_invoice_number} was already posted for {bill.vendor}.")

    journal = []
    total_base = ZERO
    for line in lines:
        base = money(line.amount * bill.exchange_rate)
        total_base += base
        if line.po_line_id:
            pol = PurchaseOrderLine.objects.select_for_update().get(pk=line.po_line_id)
            if pol.order.vendor_id != bill.vendor_id:
                raise ValidationError(f"PO line {pol} belongs to another vendor.")
            if pol.order.currency_id != bill.currency_id:
                raise ValidationError(f"Bill currency must match PO {pol.order.number} currency.")
            if line.quantity > pol.quantity - pol.qty_billed:
                raise ValidationError(f"{pol.product.sku}: billing {line.quantity} exceeds unbilled PO qty "
                                      f"{pol.quantity - pol.qty_billed}.")
            grni = money(line.quantity * pol.unit_price * pol.order.exchange_rate)
            journal.append({"account": acct("GRNI"), "debit": grni, "description": line.description,
                            "vendor": bill.vendor})
            if base != grni:
                journal.append({"account": acct("PPV"), "debit": base - grni,
                                "description": f"Price / FX variance {pol.product.sku}"})
            pol.qty_billed += line.quantity
            pol.save(update_fields=["qty_billed"])
        else:
            if not line.account_id:
                raise ValidationError(f"Line '{line.description}' needs an account (no PO line).")
            journal.append({"account": line.account, "debit": base, "description": line.description})
    journal.append({"account": acct("AP"), "credit": total_base, "vendor": bill.vendor,
                    "description": f"Bill {bill.vendor_invoice_number}"})
    if not bill.due_date:
        bill.due_date = bill.bill_date + datetime.timedelta(days=bill.vendor.payment_terms_days)
    bill.journal_entry = post_journal(bill.bill_date, f"Bill {bill.vendor_invoice_number} — {bill.vendor}",
                                      journal, "vendor_bill", bill.number, user)
    bill.status = VendorBill.POSTED
    bill.save()
    return bill


@transaction.atomic
def post_vendor_payment(payment, user=None):
    payment = VendorPayment.objects.select_for_update().get(pk=payment.pk)
    if payment.status != VendorPayment.DRAFT:
        raise ValidationError("Payment already posted.")
    if payment.amount <= 0:
        raise ValidationError("Payment amount must be positive.")
    allocations = list(payment.allocations.select_related("bill"))
    if sum((a.amount for a in allocations), ZERO) > payment.amount:
        raise ValidationError("Allocated amount exceeds the payment amount.")
    ap_debit = ZERO
    for alloc in allocations:
        bill = VendorBill.objects.select_for_update().get(pk=alloc.bill_id)
        if bill.vendor_id != payment.vendor_id:
            raise ValidationError(f"Bill {bill.number} belongs to another vendor.")
        if bill.currency_id != payment.currency_id:
            raise ValidationError(f"Bill {bill.number} is in {bill.currency}; payment is in {payment.currency}.")
        if bill.status not in VendorBill.OPEN_STATUSES:
            raise ValidationError(f"Bill {bill.number} is not open.")
        if alloc.amount <= 0 or alloc.amount > bill.balance_due:
            raise ValidationError(f"Allocation to {bill.number} must be between 0 and {bill.balance_due}.")
        alloc.bill = bill
        ap_debit += money(alloc.amount * bill.exchange_rate)
    ap_debit += money(payment.unapplied * payment.exchange_rate)
    cash = money(payment.amount * payment.exchange_rate)
    fx = cash - ap_debit
    journal = [
        {"account": acct("AP"), "debit": ap_debit, "vendor": payment.vendor},
        {"account": payment.paid_from, "credit": cash},
    ]
    if fx:
        journal.append({"account": acct("FX"), "debit": fx, "description": "Realized FX difference"})
    payment.journal_entry = post_journal(payment.date, f"Payment {payment.number} to {payment.vendor}", journal,
                                         "vendor_payment", payment.number, user)
    payment.status = VendorPayment.POSTED
    payment.save()
    for alloc in allocations:
        bill = alloc.bill
        bill.amount_paid += alloc.amount
        bill.status = VendorBill.PAID if bill.amount_paid >= bill.total else VendorBill.PARTIAL
        bill.save(update_fields=["amount_paid", "status"])
    return payment


def auto_allocate_vendor_payment(payment):
    from .models import VendorPaymentAllocation

    remaining = payment.unapplied
    already = set(payment.allocations.values_list("bill_id", flat=True))
    for bill in VendorBill.objects.filter(vendor=payment.vendor, currency=payment.currency,
                                          status__in=VendorBill.OPEN_STATUSES) \
            .exclude(pk__in=already).order_by("due_date", "bill_date", "id"):
        if remaining <= 0:
            break
        amt = min(remaining, bill.balance_due)
        if amt > 0:
            VendorPaymentAllocation.objects.create(payment=payment, bill=bill, amount=amt)
            remaining -= amt


# --------------------------------------------------------------------------- Reports

def _sums(start=None, end=None):
    qs = JournalLine.objects.all()
    if start:
        qs = qs.filter(entry__date__gte=start)
    if end:
        qs = qs.filter(entry__date__lte=end)
    return {
        row["account"]: (row["dr"] or ZERO, row["cr"] or ZERO)
        for row in qs.values("account").annotate(dr=Sum("debit"), cr=Sum("credit"))
    }


def trial_balance(as_of=None):
    sums = _sums(end=as_of)
    rows = []
    for acc in Account.objects.all():
        dr, cr = sums.get(acc.pk, (ZERO, ZERO))
        if dr == 0 and cr == 0:
            continue
        net = dr - cr
        rows.append({"account": acc, "debit": net if net > 0 else ZERO, "credit": -net if net < 0 else ZERO})
    return {
        "rows": rows,
        "total_debit": sum((r["debit"] for r in rows), ZERO),
        "total_credit": sum((r["credit"] for r in rows), ZERO),
    }


def _section(sums, account_type):
    rows = []
    for acc in Account.objects.filter(account_type=account_type):
        dr, cr = sums.get(acc.pk, (ZERO, ZERO))
        bal = dr - cr if acc.debit_normal else cr - dr
        if bal:
            rows.append({"account": acc, "amount": bal})
    return rows, sum((r["amount"] for r in rows), ZERO)


def income_statement(start, end):
    sums = _sums(start, end)
    income, total_income = _section(sums, Account.INCOME)
    expenses, total_expense = _section(sums, Account.EXPENSE)
    cogs_keys = {"COGS", "PPV", "INV_ADJ"}
    cogs = [r for r in expenses if r["account"].system_key in cogs_keys]
    opex = [r for r in expenses if r["account"].system_key not in cogs_keys]
    total_cogs = sum((r["amount"] for r in cogs), ZERO)
    return {
        "income": income, "total_income": total_income,
        "cogs": cogs, "total_cogs": total_cogs,
        "gross_profit": total_income - total_cogs,
        "expenses": opex, "total_opex": total_expense - total_cogs,
        "net_income": total_income - total_expense,
    }


def balance_sheet(as_of):
    sums = _sums(end=as_of)
    assets, total_assets = _section(sums, Account.ASSET)
    liabilities, total_liabilities = _section(sums, Account.LIABILITY)
    equity, total_equity = _section(sums, Account.EQUITY)
    _, inc = _section(sums, Account.INCOME)
    _, exp = _section(sums, Account.EXPENSE)
    earnings = inc - exp
    return {
        "assets": assets, "total_assets": total_assets,
        "liabilities": liabilities, "total_liabilities": total_liabilities,
        "equity": equity, "earnings": earnings, "total_equity": total_equity + earnings,
        "total_liabilities_equity": total_liabilities + total_equity + earnings,
    }


AGING_BUCKETS = [("current", "Current"), ("d30", "1–30"), ("d60", "31–60"), ("d90", "61–90"), ("d90p", "90+")]


def _bucket(days):
    if days <= 0:
        return "current"
    if days <= 30:
        return "d30"
    if days <= 60:
        return "d60"
    if days <= 90:
        return "d90"
    return "d90p"


def _aging(docs, as_of, party_attr, amount_fn, due_fn):
    parties = {}
    totals = {k: ZERO for k, _ in AGING_BUCKETS}
    totals["total"] = ZERO
    for doc in docs:
        party = getattr(doc, party_attr)
        due = due_fn(doc)
        bucket = _bucket((as_of - due).days)
        amt = amount_fn(doc)
        row = parties.setdefault(party.pk, {"party": party, "docs": [], "total": ZERO,
                                            **{k: ZERO for k, _ in AGING_BUCKETS}})
        row[bucket] += amt
        row["total"] += amt
        row["docs"].append({"doc": doc, "bucket": bucket, "amount": amt, "days": (as_of - due).days})
        totals[bucket] += amt
        totals["total"] += amt
    return {"rows": sorted(parties.values(), key=lambda r: -r["total"]), "totals": totals, "buckets": AGING_BUCKETS}


def ar_aging(as_of):
    docs = CustomerInvoice.objects.filter(status__in=CustomerInvoice.OPEN_STATUSES, invoice_date__lte=as_of) \
        .select_related("customer").prefetch_related("lines")
    return _aging(docs, as_of, "customer", lambda d: d.balance_due, lambda d: d.due_date or d.invoice_date)


def ap_aging(as_of):
    docs = VendorBill.objects.filter(status__in=VendorBill.OPEN_STATUSES, bill_date__lte=as_of) \
        .select_related("vendor").prefetch_related("lines")
    return _aging(docs, as_of, "vendor", lambda d: d.balance_due_base, lambda d: d.due_date or d.bill_date)


def account_ledger(account, start=None, end=None):
    opening = ZERO
    if start:
        opening = account.balance(end=start - datetime.timedelta(days=1))
    qs = account.lines.select_related("entry", "customer", "vendor").order_by("entry__date", "entry_id", "id")
    if start:
        qs = qs.filter(entry__date__gte=start)
    if end:
        qs = qs.filter(entry__date__lte=end)
    running = opening
    rows = []
    for line in qs:
        running += (line.debit - line.credit) if account.debit_normal else (line.credit - line.debit)
        rows.append({"line": line, "balance": running})
    return {"opening": opening, "rows": rows, "closing": running}
