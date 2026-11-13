"""Bank reconciliation."""
import csv
import datetime
import io
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.utils import ZERO, money

from .models import BankReconciliation, JournalLine


def last_completed(account, before=None):
    qs = BankReconciliation.objects.filter(account=account, status=BankReconciliation.COMPLETED)
    if before is not None:
        qs = qs.exclude(pk=before.pk)
    return qs.order_by("-statement_date", "-id").first()


def start_reconciliation(account, statement_date, statement_balance, user=None):
    if not account.is_bank:
        raise ValidationError(f"{account} is not a bank account.")
    if BankReconciliation.objects.filter(account=account, status=BankReconciliation.IN_PROGRESS).exists():
        raise ValidationError(f"A reconciliation for {account} is already in progress; finish or delete it first.")
    prev = last_completed(account)
    if prev and statement_date <= prev.statement_date:
        raise ValidationError(f"{account} is already reconciled through {prev.statement_date:%Y-%m-%d}.")
    return BankReconciliation.objects.create(account=account, statement_date=statement_date,
                                             statement_balance=statement_balance,
                                             beginning_balance=prev.statement_balance if prev else ZERO,
                                             created_by=user)


def candidate_lines(rec):
    """Ledger lines on the account that can be cleared in this reconciliation."""
    return (JournalLine.objects.filter(account=rec.account)
            .filter(Q(reconciliation__isnull=True, entry__date__lte=rec.statement_date) | Q(reconciliation=rec))
            .select_related("entry", "customer", "vendor").order_by("entry__date", "entry_id", "id"))


@transaction.atomic
def set_cleared(rec, line_ids):
    """Replace the set of lines cleared in an in-progress reconciliation."""
    rec = BankReconciliation.objects.select_for_update().get(pk=rec.pk)
    if rec.status != BankReconciliation.IN_PROGRESS:
        raise ValidationError("This reconciliation is completed.")
    ids = set(int(i) for i in line_ids)
    allowed = set(candidate_lines(rec).values_list("pk", flat=True))
    ids &= allowed
    JournalLine.objects.filter(reconciliation=rec).exclude(pk__in=ids).update(reconciliation=None)
    JournalLine.objects.filter(pk__in=ids).update(reconciliation=rec)
    return rec


@transaction.atomic
def complete_reconciliation(rec, user=None):
    rec = BankReconciliation.objects.select_for_update().get(pk=rec.pk)
    if rec.status != BankReconciliation.IN_PROGRESS:
        raise ValidationError("This reconciliation is already completed.")
    if rec.difference != 0:
        raise ValidationError(f"The reconciliation is out by {rec.difference:,.2f}. Clear the matching items "
                              "or record bank fees / interest until the difference is zero.")
    rec.status, rec.completed_by, rec.completed_at = BankReconciliation.COMPLETED, user, timezone.now()
    rec.save(update_fields=["status", "completed_by", "completed_at"])
    return rec


@transaction.atomic
def undo_reconciliation(rec, user=None):
    """Reopen the latest completed reconciliation of an account."""
    rec = BankReconciliation.objects.select_for_update().get(pk=rec.pk)
    if rec.status != BankReconciliation.COMPLETED:
        raise ValidationError("Only completed reconciliations can be reopened.")
    latest = last_completed(rec.account)
    if latest.pk != rec.pk:
        raise ValidationError(f"Reopen the latest reconciliation ({latest.number}) first.")
    if BankReconciliation.objects.filter(account=rec.account, status=BankReconciliation.IN_PROGRESS).exists():
        raise ValidationError("Another reconciliation for this account is in progress; delete it first.")
    rec.status, rec.completed_by, rec.completed_at = BankReconciliation.IN_PROGRESS, None, None
    rec.save(update_fields=["status", "completed_by", "completed_at"])
    return rec


def delete_reconciliation(rec):
    if rec.status != BankReconciliation.IN_PROGRESS:
        raise ValidationError("Completed reconciliations can't be deleted; reopen it first.")
    JournalLine.objects.filter(reconciliation=rec).update(reconciliation=None)
    rec.delete()


@transaction.atomic
def record_bank_item(rec, account, amount, memo, user=None):
    """Post a bank fee (amount < 0) or interest / deposit (amount > 0) seen on the statement, already cleared."""
    from .services import post_journal

    if rec.status != BankReconciliation.IN_PROGRESS:
        raise ValidationError("This reconciliation is completed.")
    amount = money(amount)
    if amount == 0:
        raise ValidationError("Enter a non-zero amount.")
    if account.pk == rec.account_id:
        raise ValidationError("Pick the offsetting account (e.g. Bank Fees), not the bank account itself.")
    lines = [{"account": rec.account, "debit": amount, "description": memo},
             {"account": account, "credit": amount, "description": memo}]
    entry = post_journal(rec.statement_date, memo or "Bank statement item", lines, "manual", rec.number, user)
    entry.lines.filter(account=rec.account).update(reconciliation=rec)
    return entry


# ----------------------------------------------------------------------------- statement import

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y", "%d-%b-%Y", "%b %d, %Y")


def _parse_date(s):
    s = (s or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(s):
    s = (s or "").strip().replace("$", "").replace(",", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    return -v if neg else v


def parse_statement(data):
    """Read a bank CSV export. Understands Date/Description and either Amount or Debit/Credit (Withdrawal/Deposit)
    columns. Returns [{date, description, amount}] with deposits positive and withdrawals negative."""
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    rows = list(csv.reader(io.StringIO(text)))
    header_idx = None
    for i, row in enumerate(rows[:15]):
        low = [c.strip().lower() for c in row]
        if any("date" in c for c in low) and any(k in c for c in low for k in ("amount", "debit", "credit",
                                                                                  "withdraw", "deposit")):
            header_idx = i
            break
    if header_idx is None:
        raise ValidationError("Couldn't find a header row with Date and Amount (or Debit/Credit) columns.")
    head = [c.strip().lower() for c in rows[header_idx]]

    def col(*names, exclude=()):
        for idx, h in enumerate(head):
            if any(n in h for n in names) and not any(x in h for x in exclude):
                return idx
        return None

    c_date = col("posting date", "post date", "transaction date", "date")
    c_desc = col("description", "memo", "payee", "details", "name")
    c_amt = col("amount", exclude=("balance",))
    c_out = col("withdraw", "debit", "payment", exclude=("balance",))
    c_in = col("deposit", "credit", exclude=("balance", "card"))
    out = []
    for row in rows[header_idx + 1:]:
        if not row or c_date is None or c_date >= len(row):
            continue
        d = _parse_date(row[c_date])
        if not d:
            continue
        if c_amt is not None and c_amt < len(row) and row[c_amt].strip():
            amt = _parse_amount(row[c_amt])
        else:
            w = _parse_amount(row[c_out]) if c_out is not None and c_out < len(row) else None
            dep = _parse_amount(row[c_in]) if c_in is not None and c_in < len(row) else None
            amt = (dep or ZERO) - abs(w or ZERO)
        if not amt:
            continue
        desc = row[c_desc].strip() if c_desc is not None and c_desc < len(row) else ""
        out.append({"date": d, "description": desc[:200], "amount": money(amt)})
    if not out:
        raise ValidationError("No transactions found in the file.")
    return out


@transaction.atomic
def import_statement(rec, data, window_days=7):
    """Clear ledger lines matching statement transactions (same amount, date within the window, closest first).
    Statement lines with no match are kept on the reconciliation for follow-up."""
    if rec.status != BankReconciliation.IN_PROGRESS:
        raise ValidationError("This reconciliation is completed.")
    stmt = parse_statement(data)
    pool = [ln for ln in candidate_lines(rec) if ln.reconciliation_id is None]
    matched, unmatched = 0, []
    for s in sorted(stmt, key=lambda r: r["date"]):
        best = None
        for ln in pool:
            if ln.debit - ln.credit != s["amount"]:
                continue
            gap = abs((ln.entry.date - s["date"]).days)
            if gap <= window_days and (best is None or gap < best[0]):
                best = (gap, ln)
        if best:
            pool.remove(best[1])
            JournalLine.objects.filter(pk=best[1].pk).update(reconciliation=rec)
            matched += 1
        else:
            unmatched.append({"date": s["date"].isoformat(), "description": s["description"],
                              "amount": str(s["amount"])})
    rec.unmatched = unmatched
    rec.save(update_fields=["unmatched"])
    return matched, unmatched


def outstanding(rec):
    """Items in the books by the statement date that the bank had not cleared (as at this reconciliation)."""
    qs = JournalLine.objects.filter(account=rec.account, entry__date__lte=rec.statement_date).filter(
        Q(reconciliation__isnull=True) | Q(reconciliation__statement_date__gt=rec.statement_date)
        | Q(reconciliation__status=BankReconciliation.IN_PROGRESS)).exclude(reconciliation=rec)
    return qs.select_related("entry", "customer", "vendor").order_by("entry__date", "id")
