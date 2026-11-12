from django.core.exceptions import ValidationError
from django.db import transaction

from core.utils import ZERO, money

from .models import Commission, CommissionPayout, SalesRep


def accrue_commission(invoice, fraction, date, user=None):
    """Accrue commission for the invoice's rep (fraction = share of invoice being recognized)."""
    from accounting.services import acct, post_journal

    rep = invoice.sales_rep
    if not rep or not rep.commission_rate:
        return None
    basis_total = invoice.gross_margin if rep.commission_basis == SalesRep.MARGIN else invoice.subtotal
    basis = money(basis_total * fraction)
    amount = money(basis * rep.commission_rate / 100)
    if amount <= 0:
        return None
    entry = post_journal(
        date, f"Commission {rep} on {invoice.number}",
        [{"account": acct("COMMISSION_EXP"), "debit": amount},
         {"account": acct("COMMISSIONS_PAYABLE"), "credit": amount}],
        "commission", invoice.number, user,
    )
    return Commission.objects.create(rep=rep, invoice=invoice, date=date, basis_amount=basis,
                                     rate=rep.commission_rate, amount=amount, journal_entry=entry)


@transaction.atomic
def pay_commissions(rep, commissions, paid_from, date, reference="", user=None):
    from accounting.services import acct, post_journal

    commissions = [c for c in commissions if c.status != Commission.PAID and c.rep_id == rep.pk]
    if not commissions:
        raise ValidationError("No unpaid commissions selected.")
    total = sum((c.amount for c in commissions), ZERO)
    payout = CommissionPayout.objects.create(rep=rep, date=date, amount=total, paid_from=paid_from,
                                             reference=reference)
    payout.journal_entry = post_journal(
        date, f"Commission payout {payout.number} — {rep}",
        [{"account": acct("COMMISSIONS_PAYABLE"), "debit": total}, {"account": paid_from, "credit": total}],
        "commission", payout.number, user,
    )
    payout.save(update_fields=["journal_entry"])
    for c in commissions:
        c.status = Commission.PAID
        c.payout = payout
        c.save(update_fields=["status", "payout"])
    return payout
