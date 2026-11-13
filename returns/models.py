"""Customer returns, credit memos, chargebacks, vendor returns and vendor credits."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse

from core.models import NumberedMixin
from core.tenancy import TenantModel
from core.utils import ZERO, money

RETURN_REASONS = [("damaged", "Damaged"), ("defective", "Defective"), ("wrong_item", "Wrong item shipped"),
                  ("overstock", "Overstock / unsold"), ("late", "Arrived late"), ("other", "Other")]


# ----------------------------------------------------------------------------- customer side

class CustomerReturn(NumberedMixin):
    SEQUENCE_KEY, SEQUENCE_PREFIX = "customer_return", "RMA-"
    DRAFT, RECEIVED, CREDITED, CANCELLED = "draft", "received", "credited", "cancelled"
    STATUSES = [(DRAFT, "Draft"), (RECEIVED, "Received"), (CREDITED, "Credited"), (CANCELLED, "Cancelled")]
    EDITABLE_STATUSES = (DRAFT,)
    customer = models.ForeignKey("sales.Customer", on_delete=models.PROTECT, related_name="returns")
    sales_order = models.ForeignKey("sales.SalesOrder", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="returns")
    invoice = models.ForeignKey("accounting.CustomerInvoice", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="returns", help_text="Original invoice (optional)")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT,
                                  help_text="Warehouse receiving the goods back")
    date = models.DateField()
    reason = models.CharField(max_length=20, choices=RETURN_REASONS, default="damaged")
    customer_reference = models.CharField("Customer RA / reference", max_length=60, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, editable=False)
    journal_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL,
                                      editable=False, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   editable=False, related_name="+")

    class Meta:
        unique_together = ("company", "number")
        ordering = ["-date", "-id"]
        verbose_name = "customer return"

    def get_absolute_url(self):
        return reverse("returns:customerreturn_detail", args=[self.pk])

    @property
    def total(self):
        return money(sum((ln.amount for ln in self.lines.all()), ZERO))


class CustomerReturnLine(TenantModel):
    RESTOCK, SCRAP = "restock", "scrap"
    DISPOSITIONS = [(RESTOCK, "Restock"), (SCRAP, "Scrap / destroy")]
    customer_return = models.ForeignKey(CustomerReturn, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField("Credit price", max_digits=14, decimal_places=2,
                                     help_text="Price to credit per unit")
    disposition = models.CharField(max_length=10, choices=DISPOSITIONS, default=RESTOCK)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=ZERO, editable=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.customer_return.number} / {self.product.sku}"

    @property
    def amount(self):
        return money((self.quantity or ZERO) * (self.unit_price or ZERO))


class CreditMemo(NumberedMixin):
    SEQUENCE_KEY, SEQUENCE_PREFIX = "credit_memo", "CM-"
    DRAFT, POSTED, PARTIAL, APPLIED, VOID = "draft", "posted", "partial", "applied", "void"
    STATUSES = [(DRAFT, "Draft"), (POSTED, "Open"), (PARTIAL, "Partially applied"), (APPLIED, "Applied"),
                (VOID, "Void")]
    EDITABLE_STATUSES = (DRAFT,)
    OPEN_STATUSES = (POSTED, PARTIAL)
    customer = models.ForeignKey("sales.Customer", on_delete=models.PROTECT, related_name="credit_memos")
    customer_return = models.ForeignKey(CustomerReturn, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="credit_memos")
    invoice = models.ForeignKey("accounting.CustomerInvoice", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="credit_memos", help_text="Original invoice (optional)")
    sales_rep = models.ForeignKey("salesteam.SalesRep", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    date = models.DateField()
    reason = models.CharField(max_length=20, choices=RETURN_REASONS + [("allowance", "Allowance"),
                                                                        ("pricing", "Pricing adjustment")],
                              default="damaged")
    tax_rate = models.DecimalField("Tax %", max_digits=6, decimal_places=3, default=ZERO)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, editable=False, db_index=True)
    amount_applied = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False)
    posted_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, editable=False)
    journal_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL,
                                      editable=False, related_name="+")

    class Meta:
        unique_together = ("company", "number")
        ordering = ["-date", "-id"]
        verbose_name = "credit memo"

    def get_absolute_url(self):
        return reverse("returns:creditmemo_detail", args=[self.pk])

    @property
    def subtotal(self):
        return money(sum((ln.amount for ln in self.lines.all()), ZERO))

    @property
    def tax(self):
        return money(self.subtotal * self.tax_rate / 100)

    @property
    def total(self):
        return self.posted_total if self.posted_total is not None else self.subtotal + self.tax

    @property
    def unapplied(self):
        return self.total - self.amount_applied


class CreditMemoLine(TenantModel):
    credit_memo = models.ForeignKey(CreditMemo, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")
    description = models.CharField(max_length=255)
    account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+", help_text="Blank = Sales Returns & Allowances")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["id"]

    @property
    def amount(self):
        return money((self.quantity or ZERO) * (self.unit_price or ZERO))


class CreditMemoAllocation(TenantModel):
    credit_memo = models.ForeignKey(CreditMemo, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey("accounting.CustomerInvoice", on_delete=models.PROTECT,
                                related_name="credit_allocations")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    date = models.DateField()


class Chargeback(NumberedMixin):
    """A retailer deduction (compliance fine, shortage, allowance...) taken against what the customer owes."""

    SEQUENCE_KEY, SEQUENCE_PREFIX = "chargeback", "CB-"
    OPEN, DISPUTED, ACCEPTED, WON, VOID = "open", "disputed", "accepted", "won", "void"
    STATUSES = [(OPEN, "Open"), (DISPUTED, "Disputed"), (ACCEPTED, "Accepted (written off)"),
                (WON, "Won / recovered"), (VOID, "Void")]
    EDITABLE_STATUSES = (OPEN, DISPUTED)
    TYPES = [
        ("asn", "ASN / EDI non-compliance"), ("labeling", "Labeling / UCC-128"), ("routing", "Routing / carrier"),
        ("late", "Late / early shipment"), ("shortage", "Shortage"), ("damage", "Damage"),
        ("pricing", "Pricing discrepancy"), ("coop", "Co-op / advertising"), ("markdown", "Markdown allowance"),
        ("return", "Return to vendor deduction"), ("other", "Other"),
    ]
    customer = models.ForeignKey("sales.Customer", on_delete=models.PROTECT, related_name="chargebacks")
    invoice = models.ForeignKey("accounting.CustomerInvoice", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="chargebacks")
    sales_order = models.ForeignKey("sales.SalesOrder", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="chargebacks")
    date = models.DateField()
    deduction_reference = models.CharField("Deduction / debit memo #", max_length=60, blank=True)
    chargeback_type = models.CharField("Type", max_length=12, choices=TYPES, default="asn")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    dispute_deadline = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=OPEN, editable=False, db_index=True)
    accepted_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False)
    resolution_notes = models.TextField(blank=True, editable=False)
    resolved_at = models.DateField(null=True, blank=True, editable=False)
    journal_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL,
                                      editable=False, related_name="+")

    class Meta:
        unique_together = ("company", "number")
        ordering = ["-date", "-id"]

    def get_absolute_url(self):
        return reverse("returns:chargeback_detail", args=[self.pk])


# ----------------------------------------------------------------------------- vendor side

class VendorReturn(NumberedMixin):
    SEQUENCE_KEY, SEQUENCE_PREFIX = "vendor_return", "VR-"
    DRAFT, SHIPPED, CREDITED, CANCELLED = "draft", "shipped", "credited", "cancelled"
    STATUSES = [(DRAFT, "Draft"), (SHIPPED, "Shipped to vendor"), (CREDITED, "Credited"),
                (CANCELLED, "Cancelled")]
    EDITABLE_STATUSES = (DRAFT,)
    vendor = models.ForeignKey("purchasing.Vendor", on_delete=models.PROTECT, related_name="returns")
    purchase_order = models.ForeignKey("purchasing.PurchaseOrder", null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="returns")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT,
                                  help_text="Warehouse the goods leave from")
    date = models.DateField()
    reason = models.CharField(max_length=20, choices=RETURN_REASONS, default="defective")
    vendor_rma = models.CharField("Vendor RA #", max_length=60, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, editable=False)
    journal_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL,
                                      editable=False, related_name="+")

    class Meta:
        unique_together = ("company", "number")
        ordering = ["-date", "-id"]
        verbose_name = "vendor return"

    def get_absolute_url(self):
        return reverse("returns:vendorreturn_detail", args=[self.pk])

    @property
    def total(self):
        return money(sum((ln.amount for ln in self.lines.all()), ZERO))


class VendorReturnLine(TenantModel):
    vendor_return = models.ForeignKey(VendorReturn, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField("Credit value / unit", max_digits=14, decimal_places=4, null=True, blank=True,
                                    help_text="Base currency value expected back; blank = average cost")

    class Meta:
        ordering = ["id"]

    @property
    def amount(self):
        return money((self.quantity or ZERO) * (self.unit_cost or ZERO))


class VendorCredit(NumberedMixin):
    SEQUENCE_KEY, SEQUENCE_PREFIX = "vendor_credit", "VC-"
    DRAFT, POSTED, PARTIAL, APPLIED, VOID = "draft", "posted", "partial", "applied", "void"
    STATUSES = [(DRAFT, "Draft"), (POSTED, "Open"), (PARTIAL, "Partially applied"), (APPLIED, "Applied"),
                (VOID, "Void")]
    EDITABLE_STATUSES = (DRAFT,)
    OPEN_STATUSES = (POSTED, PARTIAL)
    vendor = models.ForeignKey("purchasing.Vendor", on_delete=models.PROTECT, related_name="credits")
    vendor_credit_number = models.CharField("Vendor credit note #", max_length=60, blank=True)
    vendor_return = models.ForeignKey(VendorReturn, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="credits")
    date = models.DateField()
    currency = models.ForeignKey("core.Currency", on_delete=models.PROTECT)
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, editable=False, db_index=True)
    amount_applied = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False,
                                         help_text="In credit currency")
    posted_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, editable=False)
    journal_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL,
                                      editable=False, related_name="+")

    class Meta:
        unique_together = ("company", "number")
        ordering = ["-date", "-id"]
        verbose_name = "vendor credit"

    def get_absolute_url(self):
        return reverse("returns:vendorcredit_detail", args=[self.pk])

    @property
    def total(self):
        if self.posted_total is not None:
            return self.posted_total
        return money(sum((ln.amount for ln in self.lines.all()), ZERO))

    @property
    def unapplied(self):
        return self.total - self.amount_applied


class VendorCreditLine(TenantModel):
    vendor_credit = models.ForeignKey(VendorCredit, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=255)
    account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+", help_text="Blank = Vendor Returns Receivable")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=14, decimal_places=4)

    class Meta:
        ordering = ["id"]

    @property
    def amount(self):
        return money((self.quantity or ZERO) * (self.unit_price or ZERO))


class VendorCreditAllocation(TenantModel):
    vendor_credit = models.ForeignKey(VendorCredit, on_delete=models.CASCADE, related_name="allocations")
    bill = models.ForeignKey("accounting.VendorBill", on_delete=models.PROTECT, related_name="credit_allocations")
    amount = models.DecimalField(max_digits=14, decimal_places=2, help_text="In credit currency")
    date = models.DateField()

