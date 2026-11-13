"""Posting logic for returns, credit memos, chargebacks and vendor credits."""
import datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import CustomerInvoice, VendorBill
from accounting.services import acct, post_journal
from core.utils import ZERO, cost, money

from .models import (
    Chargeback,
    CreditMemo,
    CreditMemoAllocation,
    CreditMemoLine,
    CustomerReturn,
    VendorCredit,
    VendorCreditAllocation,
    VendorCreditLine,
    VendorReturn,
)


def _apply_to_invoice(invoice, amount):
    invoice.amount_paid += amount
    invoice.status = CustomerInvoice.PAID if invoice.amount_paid >= invoice.total else CustomerInvoice.PARTIAL
    invoice.save(update_fields=["amount_paid", "status"])


# ----------------------------------------------------------------------------- customer returns / credit memos

@transaction.atomic
def receive_customer_return(ret, user=None):
    """Goods back in: restocked lines return to stock at their original cost (COGS reversed)."""
    from inventory.services import receive_stock

    ret = CustomerReturn.objects.select_for_update().get(pk=ret.pk)
    if ret.status != CustomerReturn.DRAFT:
        raise ValidationError("Only draft returns can be received.")
    lines = list(ret.lines.select_related("product"))
    if not lines:
        raise ValidationError("The return has no lines.")
    value = ZERO
    for line in lines:
        so_line = None
        if ret.sales_order_id:
            so_line = ret.sales_order.lines.filter(product=line.product).first()
        line.unit_cost = cost(so_line.avg_unit_cost_shipped if so_line and so_line.qty_shipped else
                              line.product.avg_cost)
        line.save(update_fields=["unit_cost"])
        if line.disposition == line.RESTOCK:
            receive_stock(line.product, ret.warehouse, line.quantity, line.unit_cost, ret.date, ret.number,
                          move_type="customer_return")
            value += money(line.quantity * line.unit_cost)
    if value:
        ret.journal_entry = post_journal(
            ret.date, f"Customer return {ret.number} — {ret.customer}",
            [{"account": acct("INVENTORY"), "debit": value, "description": "Restocked returns"},
             {"account": acct("COGS"), "credit": value, "description": "COGS reversed"}],
            "stock_adjustment", ret.number, user)
    ret.status = CustomerReturn.RECEIVED
    ret.save(update_fields=["status", "journal_entry"])
    return ret


def create_credit_memo_from_return(ret):
    if ret.status not in (CustomerReturn.RECEIVED, CustomerReturn.DRAFT):
        raise ValidationError("This return was already credited or cancelled.")
    if ret.credit_memos.exclude(status=CreditMemo.VOID).exists():
        raise ValidationError("A credit memo already exists for this return.")
    so = ret.sales_order
    memo = CreditMemo.objects.create(customer=ret.customer, customer_return=ret, invoice=ret.invoice,
                                     sales_rep=(so.sales_rep if so else None) or ret.customer.sales_rep,
                                     date=datetime.date.today(), reason=ret.reason,
                                     tax_rate=so.tax_rate if so else ZERO)
    for line in ret.lines.select_related("product"):
        CreditMemoLine.objects.create(credit_memo=memo, product=line.product,
                                      description=f"Return {ret.number}: {line.product}", quantity=line.quantity,
                                      unit_price=line.unit_price)
    return memo


@transaction.atomic
def post_credit_memo(memo, user=None):
    from salesteam.services import accrue_commission

    memo = CreditMemo.objects.select_for_update().get(pk=memo.pk)
    if memo.status != CreditMemo.DRAFT:
        raise ValidationError("Only draft credit memos can be posted.")
    lines = list(memo.lines.select_related("account"))
    if not lines:
        raise ValidationError("The credit memo has no lines.")
    by_account = {}
    for line in lines:
        acc = line.account or acct("SALES_RETURNS")
        by_account[acc] = by_account.get(acc, ZERO) + line.amount
    subtotal, tax = memo.subtotal, memo.tax
    journal = [{"account": a, "debit": amt, "description": f"Credit memo {memo.number}"} for a, amt in
               by_account.items()]
    if tax:
        journal.append({"account": acct("SALES_TAX"), "debit": tax, "description": f"Tax {memo.number}"})
    journal.append({"account": acct("AR"), "credit": subtotal + tax, "customer": memo.customer,
                    "description": f"Credit memo {memo.number}"})
    memo.journal_entry = post_journal(memo.date, f"Credit memo {memo.number} — {memo.customer}", journal,
                                      "customer_invoice", memo.number, user)
    memo.posted_total = subtotal + tax
    memo.status = CreditMemo.POSTED
    memo.save()
    if memo.customer_return_id:
        CustomerReturn.objects.filter(pk=memo.customer_return_id).update(status=CustomerReturn.CREDITED)
    # Claw back commission paid on the original invoice, proportionally
    inv = memo.invoice
    if inv and inv.sales_rep_id and inv.subtotal:
        accrue_commission(inv, -(subtotal / inv.subtotal), memo.date, user)
    return memo


@transaction.atomic
def apply_credit_memo(memo, invoice, amount, date=None):
    memo = CreditMemo.objects.select_for_update().get(pk=memo.pk)
    invoice = CustomerInvoice.objects.select_for_update().get(pk=invoice.pk)
    if memo.status not in CreditMemo.OPEN_STATUSES:
        raise ValidationError("The credit memo must be posted and not fully applied.")
    if invoice.customer_id != memo.customer_id:
        raise ValidationError("The invoice belongs to another customer.")
    if invoice.status not in CustomerInvoice.OPEN_STATUSES:
        raise ValidationError(f"Invoice {invoice.number} is not open.")
    if amount <= 0 or amount > memo.unapplied or amount > invoice.balance_due:
        raise ValidationError(f"Amount must be between 0 and {min(memo.unapplied, invoice.balance_due)}.")
    CreditMemoAllocation.objects.create(credit_memo=memo, invoice=invoice, amount=amount,
                                        date=date or datetime.date.today())
    _apply_to_invoice(invoice, amount)
    memo.amount_applied += amount
    memo.status = CreditMemo.APPLIED if memo.amount_applied >= memo.total else CreditMemo.PARTIAL
    memo.save(update_fields=["amount_applied", "status"])
    return memo


def auto_apply_credit_memo(memo):
    """Apply to the original invoice first, then the oldest open invoices."""
    invoices = list(CustomerInvoice.objects.filter(customer=memo.customer, status__in=CustomerInvoice.OPEN_STATUSES)
                    .order_by("due_date", "invoice_date", "id"))
    if memo.invoice_id:
        invoices.sort(key=lambda i: i.pk != memo.invoice_id)
    for inv in invoices:
        memo.refresh_from_db()
        if memo.unapplied <= 0:
            break
        amt = min(memo.unapplied, inv.balance_due)
        if amt > 0:
            apply_credit_memo(memo, inv, amt)
    return memo


# ----------------------------------------------------------------------------- chargebacks

@transaction.atomic
def accept_chargeback(cb, amount=None, notes="", user=None):
    """Write the deduction off: chargeback expense against AR, applied to the invoice when known."""
    cb = Chargeback.objects.select_for_update().get(pk=cb.pk)
    if cb.status not in (Chargeback.OPEN, Chargeback.DISPUTED):
        raise ValidationError("Only open or disputed chargebacks can be accepted.")
    amount = money(amount if amount not in (None, "") else cb.amount)
    if amount <= 0 or amount > cb.amount:
        raise ValidationError(f"Accepted amount must be between 0 and {cb.amount}.")
    cb.journal_entry = post_journal(
        datetime.date.today(), f"Chargeback {cb.number} {cb.get_chargeback_type_display()} — {cb.customer}",
        [{"account": acct("CHARGEBACKS"), "debit": amount, "description": cb.deduction_reference},
         {"account": acct("AR"), "credit": amount, "customer": cb.customer, "description": cb.number}],
        "customer_payment", cb.number, user)
    # Clear the deduction off open invoices: the chargeback's own invoice first, then the oldest
    invoices = list(CustomerInvoice.objects.select_for_update()
                    .filter(customer=cb.customer, status__in=CustomerInvoice.OPEN_STATUSES)
                    .order_by("due_date", "invoice_date", "id"))
    invoices.sort(key=lambda i: i.pk != cb.invoice_id)
    remaining = amount
    for inv in invoices:
        if remaining <= 0:
            break
        part = min(remaining, inv.balance_due)
        if part > 0:
            _apply_to_invoice(inv, part)
            remaining -= part
    cb.status, cb.accepted_amount, cb.resolved_at = Chargeback.ACCEPTED, amount, datetime.date.today()
    cb.resolution_notes = (cb.resolution_notes + "\n" + notes).strip() if notes else cb.resolution_notes
    cb.save()
    return cb


def dispute_chargeback(cb, notes="", user=None):
    if cb.status not in (Chargeback.OPEN, Chargeback.DISPUTED):
        raise ValidationError("Only open chargebacks can be disputed.")
    cb.status = Chargeback.DISPUTED
    if notes:
        cb.resolution_notes = (cb.resolution_notes + f"\n[{datetime.date.today()}] Dispute: {notes}").strip()
    cb.save(update_fields=["status", "resolution_notes"])
    return cb


def win_chargeback(cb, notes="", user=None):
    """The customer reversed the deduction (will pay it)."""
    if cb.status not in (Chargeback.OPEN, Chargeback.DISPUTED):
        raise ValidationError("Only open or disputed chargebacks can be won.")
    cb.status, cb.resolved_at = Chargeback.WON, datetime.date.today()
    if notes:
        cb.resolution_notes = (cb.resolution_notes + f"\n[{datetime.date.today()}] Won: {notes}").strip()
    cb.save(update_fields=["status", "resolved_at", "resolution_notes"])
    return cb


def void_chargeback(cb, user=None):
    if cb.status not in (Chargeback.OPEN, Chargeback.DISPUTED):
        raise ValidationError("Only open or disputed chargebacks can be voided.")
    cb.status = Chargeback.VOID
    cb.save(update_fields=["status"])
    return cb


# ----------------------------------------------------------------------------- vendor returns / credits

@transaction.atomic
def ship_vendor_return(vr, user=None):
    """Goods leave inventory at average cost; the value expected back is booked as Vendor Returns Receivable."""
    from inventory.services import issue_stock

    vr = VendorReturn.objects.select_for_update().get(pk=vr.pk)
    if vr.status != VendorReturn.DRAFT:
        raise ValidationError("Only draft vendor returns can be shipped.")
    lines = list(vr.lines.select_related("product"))
    if not lines:
        raise ValidationError("The return has no lines.")
    inventory_value = receivable = ZERO
    for line in lines:
        avg = issue_stock(line.product, vr.warehouse, line.quantity, vr.date, vr.number, move_type="vendor_return")
        if line.unit_cost is None:
            line.unit_cost = avg
            line.save(update_fields=["unit_cost"])
        inventory_value += money(line.quantity * avg)
        receivable += line.amount
    journal = [{"account": acct("VENDOR_RETURNS"), "debit": receivable, "vendor": vr.vendor},
               {"account": acct("INVENTORY"), "credit": inventory_value}]
    if receivable != inventory_value:
        journal.append({"account": acct("PPV"), "debit": inventory_value - receivable,
                        "description": "Return value vs average cost"})
    vr.journal_entry = post_journal(vr.date, f"Return to vendor {vr.number} — {vr.vendor}", journal,
                                    "stock_adjustment", vr.number, user)
    vr.status = VendorReturn.SHIPPED
    vr.save(update_fields=["status", "journal_entry"])
    return vr


def create_vendor_credit_from_return(vr):
    from core.models import Company

    if vr.status != VendorReturn.SHIPPED:
        raise ValidationError("Ship the return first.")
    if vr.credits.exclude(status=VendorCredit.VOID).exists():
        raise ValidationError("A vendor credit already exists for this return.")
    vc = VendorCredit.objects.create(vendor=vr.vendor, vendor_return=vr, date=datetime.date.today(),
                                     currency_id=Company.get().base_currency_id, exchange_rate=1)
    for line in vr.lines.select_related("product"):
        VendorCreditLine.objects.create(vendor_credit=vc, description=f"Return {vr.number}: {line.product}",
                                        quantity=line.quantity, unit_price=line.unit_cost or ZERO)
    return vc


@transaction.atomic
def post_vendor_credit(vc, user=None):
    vc = VendorCredit.objects.select_for_update().get(pk=vc.pk)
    if vc.status != VendorCredit.DRAFT:
        raise ValidationError("Only draft vendor credits can be posted.")
    lines = list(vc.lines.select_related("account"))
    if not lines:
        raise ValidationError("The vendor credit has no lines.")
    journal, total_base = [], ZERO
    for line in lines:
        base = money(line.amount * vc.exchange_rate)
        total_base += base
        journal.append({"account": line.account or acct("VENDOR_RETURNS"), "credit": base,
                        "description": line.description, "vendor": vc.vendor if not line.account else None})
    journal.append({"account": acct("AP"), "debit": total_base, "vendor": vc.vendor,
                    "description": f"Vendor credit {vc.vendor_credit_number or vc.number}"})
    vc.journal_entry = post_journal(vc.date, f"Vendor credit {vc.number} — {vc.vendor}", journal, "vendor_bill",
                                    vc.number, user)
    vc.posted_total = money(sum((ln.amount for ln in lines), ZERO))
    vc.status = VendorCredit.POSTED
    vc.save()
    if vc.vendor_return_id:
        VendorReturn.objects.filter(pk=vc.vendor_return_id).update(status=VendorReturn.CREDITED)
    return vc


@transaction.atomic
def apply_vendor_credit(vc, bill, amount, date=None, user=None):
    vc = VendorCredit.objects.select_for_update().get(pk=vc.pk)
    bill = VendorBill.objects.select_for_update().get(pk=bill.pk)
    if vc.status not in VendorCredit.OPEN_STATUSES:
        raise ValidationError("The vendor credit must be posted and not fully applied.")
    if bill.vendor_id != vc.vendor_id:
        raise ValidationError("The bill belongs to another vendor.")
    if bill.currency_id != vc.currency_id:
        raise ValidationError(f"Bill {bill.number} is in {bill.currency}; the credit is in {vc.currency}.")
    if bill.status not in VendorBill.OPEN_STATUSES:
        raise ValidationError(f"Bill {bill.number} is not open.")
    if amount <= 0 or amount > vc.unapplied or amount > bill.balance_due:
        raise ValidationError(f"Amount must be between 0 and {min(vc.unapplied, bill.balance_due)}.")
    date = date or datetime.date.today()
    VendorCreditAllocation.objects.create(vendor_credit=vc, bill=bill, amount=amount, date=date)
    # AP was debited at the credit's rate and credited at the bill's rate: settle the difference to FX
    diff = money(amount * bill.exchange_rate) - money(amount * vc.exchange_rate)
    if diff:
        post_journal(date, f"FX on applying {vc.number} to {bill.number}",
                     [{"account": acct("AP"), "debit": diff, "vendor": vc.vendor},
                      {"account": acct("FX"), "credit": diff}], "vendor_payment", vc.number, user)
    bill.amount_paid += amount
    bill.status = VendorBill.PAID if bill.amount_paid >= bill.total else VendorBill.PARTIAL
    bill.save(update_fields=["amount_paid", "status"])
    vc.amount_applied += amount
    vc.status = VendorCredit.APPLIED if vc.amount_applied >= vc.total else VendorCredit.PARTIAL
    vc.save(update_fields=["amount_applied", "status"])
    return vc


def auto_apply_vendor_credit(vc, user=None):
    for bill in VendorBill.objects.filter(vendor=vc.vendor, currency=vc.currency,
                                          status__in=VendorBill.OPEN_STATUSES).order_by("due_date", "bill_date"):
        vc.refresh_from_db()
        if vc.unapplied <= 0:
            break
        amt = min(vc.unapplied, bill.balance_due)
        if amt > 0:
            apply_vendor_credit(vc, bill, amt, user=user)
    return vc
