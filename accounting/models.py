from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.urls import reverse

from core.models import NumberedMixin
from core.utils import ZERO, money


class Account(models.Model):
    ASSET, LIABILITY, EQUITY, INCOME, EXPENSE = "asset", "liability", "equity", "income", "expense"
    TYPES = [(ASSET, "Asset"), (LIABILITY, "Liability"), (EQUITY, "Equity"), (INCOME, "Income"),
             (EXPENSE, "Expense")]
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    account_type = models.CharField(max_length=10, choices=TYPES)
    is_bank = models.BooleanField(default=False, help_text="Cash / bank account usable for payments")
    system_key = models.CharField(max_length=30, blank=True, null=True, unique=True, editable=False,
                                  help_text="Used by automatic postings")
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"

    def get_absolute_url(self):
        return reverse("accounting:account_detail", args=[self.pk])

    @property
    def debit_normal(self):
        return self.account_type in (self.ASSET, self.EXPENSE)

    def balance(self, start=None, end=None):
        qs = self.lines.all()
        if start:
            qs = qs.filter(entry__date__gte=start)
        if end:
            qs = qs.filter(entry__date__lte=end)
        agg = qs.aggregate(dr=Sum("debit"), cr=Sum("credit"))
        dr, cr = agg["dr"] or ZERO, agg["cr"] or ZERO
        return dr - cr if self.debit_normal else cr - dr


class JournalEntry(NumberedMixin):
    SEQUENCE_KEY = "journal"
    SEQUENCE_PREFIX = "JE-"
    SOURCES = [
        ("manual", "Manual entry"),
        ("customer_invoice", "Customer invoice"),
        ("customer_payment", "Customer payment"),
        ("vendor_bill", "Vendor bill"),
        ("vendor_payment", "Vendor payment"),
        ("goods_receipt", "Goods receipt"),
        ("delivery", "Delivery (COGS)"),
        ("landed_cost", "Landed cost"),
        ("stock_adjustment", "Stock adjustment"),
        ("commission", "Commission"),
    ]
    date = models.DateField(db_index=True)
    memo = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=20, choices=SOURCES, default="manual")
    reference = models.CharField(max_length=60, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name_plural = "journal entries"

    def get_absolute_url(self):
        return reverse("accounting:journalentry_detail", args=[self.pk])

    @property
    def total_debit(self):
        return self.lines.aggregate(t=Sum("debit"))["t"] or ZERO


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    description = models.CharField(max_length=255, blank=True)
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    customer = models.ForeignKey("sales.Customer", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="journal_lines")
    vendor = models.ForeignKey("purchasing.Vendor", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="journal_lines")

    class Meta:
        ordering = ["id"]


# --------------------------------------------------------------------------- Receivables

class CustomerInvoice(NumberedMixin):
    SEQUENCE_KEY = "invoice"
    SEQUENCE_PREFIX = "INV-"
    DRAFT, POSTED, PARTIAL, PAID, VOID = "draft", "posted", "partial", "paid", "void"
    STATUSES = [(DRAFT, "Draft"), (POSTED, "Open"), (PARTIAL, "Partially paid"), (PAID, "Paid"),
                (VOID, "Void")]
    EDITABLE_STATUSES = (DRAFT,)
    POSTED_STATUSES = (POSTED, PARTIAL, PAID)
    OPEN_STATUSES = (POSTED, PARTIAL)

    customer = models.ForeignKey("sales.Customer", on_delete=models.PROTECT, related_name="invoices")
    sales_order = models.ForeignKey("sales.SalesOrder", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="invoices")
    sales_rep = models.ForeignKey("salesteam.SalesRep", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="invoices")
    invoice_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    tax_rate = models.DecimalField("Tax %", max_digits=6, decimal_places=3, default=ZERO)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, editable=False, db_index=True)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False)
    posted_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, editable=False,
                                       help_text="Invoice total frozen when posted (posted invoices never change)")
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.SET_NULL,
                                      editable=False)

    class Meta:
        ordering = ["-invoice_date", "-id"]
        verbose_name = "customer invoice"

    def get_absolute_url(self):
        return reverse("accounting:customerinvoice_detail", args=[self.pk])

    @property
    def subtotal(self):
        return money(sum((line.amount for line in self.lines.all()), ZERO))

    @property
    def tax(self):
        return money(self.subtotal * self.tax_rate / 100)

    @property
    def total(self):
        if self.posted_total is not None:
            return self.posted_total
        return self.subtotal + self.tax

    @property
    def balance_due(self):
        return self.total - self.amount_paid

    @property
    def cost_total(self):
        return money(sum(((line.unit_cost or ZERO) * line.quantity for line in self.lines.all()), ZERO))

    @property
    def gross_margin(self):
        return self.subtotal - self.cost_total


class CustomerInvoiceLine(models.Model):
    invoice = models.ForeignKey(CustomerInvoice, on_delete=models.CASCADE, related_name="lines")
    so_line = models.ForeignKey("sales.SalesOrderLine", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="invoice_lines", editable=False)
    product = models.ForeignKey("products.Product", null=True, blank=True, on_delete=models.PROTECT,
                                editable=False)
    description = models.CharField(max_length=255)
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT,
                                help_text="Income account; blank = Sales")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    discount_pct = models.DecimalField("Disc %", max_digits=5, decimal_places=2, default=ZERO)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=ZERO, editable=False)

    class Meta:
        ordering = ["id"]

    @property
    def amount(self):
        return money((self.quantity or ZERO) * (self.unit_price or ZERO) * (1 - (self.discount_pct or ZERO) / 100))


class CustomerPayment(NumberedMixin):
    SEQUENCE_KEY = "customer_payment"
    SEQUENCE_PREFIX = "RCPT-"
    METHODS = [("wire", "Wire transfer"), ("ach", "ACH"), ("check", "Check"), ("card", "Card"),
               ("cash", "Cash"), ("other", "Other")]
    DRAFT, POSTED = "draft", "posted"
    STATUSES = [(DRAFT, "Draft"), (POSTED, "Posted")]
    EDITABLE_STATUSES = (DRAFT,)
    customer = models.ForeignKey("sales.Customer", on_delete=models.PROTECT, related_name="payments")
    date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=10, choices=METHODS, default="wire")
    reference = models.CharField(max_length=60, blank=True)
    deposit_account = models.ForeignKey(Account, on_delete=models.PROTECT, limit_choices_to={"is_bank": True})
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, editable=False)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.SET_NULL,
                                      editable=False)

    class Meta:
        ordering = ["-date", "-id"]

    def get_absolute_url(self):
        return reverse("accounting:customerpayment_detail", args=[self.pk])

    @property
    def allocated(self):
        return self.allocations.aggregate(t=Sum("amount"))["t"] or ZERO

    @property
    def unapplied(self):
        return self.amount - self.allocated


class CustomerPaymentAllocation(models.Model):
    payment = models.ForeignKey(CustomerPayment, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(CustomerInvoice, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=14, decimal_places=2)


# --------------------------------------------------------------------------- Payables

class VendorBill(NumberedMixin):
    SEQUENCE_KEY = "bill"
    SEQUENCE_PREFIX = "BILL-"
    DRAFT, POSTED, PARTIAL, PAID, VOID = "draft", "posted", "partial", "paid", "void"
    STATUSES = [(DRAFT, "Draft"), (POSTED, "Open"), (PARTIAL, "Partially paid"), (PAID, "Paid"),
                (VOID, "Void")]
    EDITABLE_STATUSES = (DRAFT,)
    OPEN_STATUSES = (POSTED, PARTIAL)

    vendor = models.ForeignKey("purchasing.Vendor", on_delete=models.PROTECT, related_name="bills")
    vendor_invoice_number = models.CharField("Vendor invoice #", max_length=60)
    purchase_order = models.ForeignKey("purchasing.PurchaseOrder", null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="bills")
    shipment = models.ForeignKey("purchasing.Shipment", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="bills", help_text="For freight / duty / broker bills")
    bill_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    currency = models.ForeignKey("core.Currency", on_delete=models.PROTECT)
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, editable=False, db_index=True)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False,
                                      help_text="In bill currency")
    submitted_via_portal = models.BooleanField(default=False, editable=False)
    posted_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, editable=False,
                                       help_text="Bill total (bill currency) frozen when posted")
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.SET_NULL,
                                      editable=False)

    class Meta:
        ordering = ["-bill_date", "-id"]
        verbose_name = "vendor bill"

    def get_absolute_url(self):
        return reverse("accounting:vendorbill_detail", args=[self.pk])

    @property
    def total(self):
        if self.posted_total is not None:
            return self.posted_total
        return money(sum((line.amount for line in self.lines.all()), ZERO))

    @property
    def total_base(self):
        return money(self.total * self.exchange_rate)

    @property
    def balance_due(self):
        return self.total - self.amount_paid

    @property
    def balance_due_base(self):
        return money(self.balance_due * self.exchange_rate)


class VendorBillLine(models.Model):
    bill = models.ForeignKey(VendorBill, on_delete=models.CASCADE, related_name="lines")
    po_line = models.ForeignKey("purchasing.PurchaseOrderLine", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="bill_lines")
    description = models.CharField(max_length=255)
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT,
                                help_text="Required for non-PO lines (e.g. Accrued landed costs, expenses)")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=14, decimal_places=4)

    class Meta:
        ordering = ["id"]

    @property
    def amount(self):
        return money((self.quantity or ZERO) * (self.unit_price or ZERO))


class VendorPayment(NumberedMixin):
    SEQUENCE_KEY = "vendor_payment"
    SEQUENCE_PREFIX = "PAY-"
    METHODS = CustomerPayment.METHODS + [("lc", "Letter of credit")]
    DRAFT, POSTED = "draft", "posted"
    STATUSES = [(DRAFT, "Draft"), (POSTED, "Posted")]
    EDITABLE_STATUSES = (DRAFT,)
    vendor = models.ForeignKey("purchasing.Vendor", on_delete=models.PROTECT, related_name="payments")
    date = models.DateField()
    currency = models.ForeignKey("core.Currency", on_delete=models.PROTECT)
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"),
                                        help_text="Rate on payment date")
    amount = models.DecimalField(max_digits=14, decimal_places=2, help_text="In payment currency")
    method = models.CharField(max_length=10, choices=METHODS, default="wire")
    reference = models.CharField(max_length=60, blank=True)
    paid_from = models.ForeignKey(Account, on_delete=models.PROTECT, limit_choices_to={"is_bank": True})
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, editable=False)
    journal_entry = models.ForeignKey(JournalEntry, null=True, blank=True, on_delete=models.SET_NULL,
                                      editable=False)

    class Meta:
        ordering = ["-date", "-id"]

    def get_absolute_url(self):
        return reverse("accounting:vendorpayment_detail", args=[self.pk])

    @property
    def allocated(self):
        return self.allocations.aggregate(t=Sum("amount"))["t"] or ZERO

    @property
    def unapplied(self):
        return self.amount - self.allocated


class VendorPaymentAllocation(models.Model):
    payment = models.ForeignKey(VendorPayment, on_delete=models.CASCADE, related_name="allocations")
    bill = models.ForeignKey(VendorBill, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=14, decimal_places=2, help_text="In bill currency")
