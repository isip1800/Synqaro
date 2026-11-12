from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.urls import reverse

from core.models import NumberedMixin
from core.utils import ZERO


class SalesRep(models.Model):
    REVENUE, MARGIN = "revenue", "margin"
    BASES = [(REVENUE, "% of net revenue"), (MARGIN, "% of gross margin")]
    ON_INVOICE, ON_PAYMENT = "invoiced", "paid"
    TRIGGERS = [(ON_INVOICE, "When invoiced"), (ON_PAYMENT, "When customer pays")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="sales_rep", help_text="Login used by this sales person")
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    territory = models.CharField(max_length=100, blank=True)
    manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="team")
    commission_rate = models.DecimalField("Commission %", max_digits=6, decimal_places=3, default=Decimal("0"))
    commission_basis = models.CharField(max_length=10, choices=BASES, default=REVENUE)
    commission_trigger = models.CharField(max_length=10, choices=TRIGGERS, default=ON_INVOICE)
    monthly_target = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    hire_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "sales person"
        verbose_name_plural = "sales people"

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("salesteam:salesrep_detail", args=[self.pk])

    def sales_between(self, start, end):
        from accounting.models import CustomerInvoice

        invoices = CustomerInvoice.objects.filter(
            sales_rep=self, invoice_date__gte=start, invoice_date__lte=end,
            status__in=CustomerInvoice.POSTED_STATUSES,
        ).prefetch_related("lines")
        revenue = sum((inv.subtotal for inv in invoices), ZERO)
        margin = sum((inv.gross_margin for inv in invoices), ZERO)
        return revenue, margin, len(invoices)

    @property
    def commission_outstanding(self):
        return self.commissions.exclude(status=Commission.PAID).aggregate(t=Sum("amount"))["t"] or ZERO


class CommissionPayout(NumberedMixin):
    SEQUENCE_KEY = "commission_payout"
    SEQUENCE_PREFIX = "CPO-"
    rep = models.ForeignKey(SalesRep, on_delete=models.PROTECT, related_name="payouts")
    date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    paid_from = models.ForeignKey("accounting.Account", on_delete=models.PROTECT)
    reference = models.CharField(max_length=60, blank=True)
    journal_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-date", "-id"]


class Commission(models.Model):
    ACCRUED, APPROVED, PAID = "accrued", "approved", "paid"
    STATUSES = [(ACCRUED, "Accrued"), (APPROVED, "Approved"), (PAID, "Paid")]
    rep = models.ForeignKey(SalesRep, on_delete=models.PROTECT, related_name="commissions")
    invoice = models.ForeignKey("accounting.CustomerInvoice", on_delete=models.PROTECT, related_name="commissions")
    date = models.DateField()
    basis_amount = models.DecimalField(max_digits=14, decimal_places=2)
    rate = models.DecimalField(max_digits=6, decimal_places=3)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUSES, default=ACCRUED)
    payout = models.ForeignKey(CommissionPayout, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="commissions")
    journal_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.rep} {self.invoice.number} {self.amount}"

    def get_absolute_url(self):
        return self.rep.get_absolute_url()
