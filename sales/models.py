from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.urls import reverse

from core.models import AddressMixin, NumberedMixin, TimeStamped
from core.utils import ZERO, money


class Customer(AddressMixin, TimeStamped):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    contact_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    shipping_address = models.TextField(blank=True, help_text="Leave blank to use billing address")
    tax_id = models.CharField(max_length=50, blank=True)
    tax_exempt = models.BooleanField(default=False, help_text="Resale certificate on file")
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO,
                                       help_text="0 = no limit")
    payment_terms_days = models.PositiveIntegerField(default=30)
    price_list = models.ForeignKey("products.PriceList", null=True, blank=True, on_delete=models.SET_NULL)
    sales_rep = models.ForeignKey("salesteam.SalesRep", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="customers")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("sales:customer_detail", args=[self.pk])

    @property
    def balance(self):
        """Open receivable balance (debit balance on AR)."""
        from accounting.models import JournalLine
        from accounting.services import acct

        agg = JournalLine.objects.filter(customer=self, account=acct("AR")).aggregate(
            dr=Sum("debit"), cr=Sum("credit")
        )
        return (agg["dr"] or ZERO) - (agg["cr"] or ZERO)

    @property
    def open_orders_value(self):
        """Value of confirmed sales not yet invoiced."""
        total = ZERO
        for order in self.sales_orders.filter(status__in=SalesOrder.OPEN_STATUSES).prefetch_related("lines"):
            total += order.uninvoiced_total
        return total

    @property
    def credit_available(self):
        if not self.credit_limit:
            return None
        return self.credit_limit - self.balance - self.open_orders_value


class CustomerBuyer(models.Model):
    """A buyer (purchasing contact) at a customer. A customer can have several."""

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="buyers")
    name = models.CharField(max_length=100)
    title = models.CharField(max_length=100, blank=True, help_text="e.g. Senior Buyer — Baby & Kids")
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["customer__name", "name"]
        verbose_name = "buyer"

    def __str__(self):
        return self.name

    @property
    def open_order_count(self):
        return self.sales_orders.filter(status__in=SalesOrder.OPEN_STATUSES).count()


class SalesOrder(NumberedMixin, TimeStamped):
    SEQUENCE_KEY = "so"
    SEQUENCE_PREFIX = "SO-"
    QUOTE, PENDING, CONFIRMED, PARTIAL, BACKORDER, SHIPPED, CLOSED, CANCELLED = (
        "quote", "pending", "confirmed", "partial", "backorder", "shipped", "closed", "cancelled")
    STATUSES = [
        (QUOTE, "Quotation"),
        (PENDING, "Pending approval"),
        (CONFIRMED, "Open"),
        (PARTIAL, "Partially shipped"),
        (BACKORDER, "Backordered"),
        (SHIPPED, "Shipped"),
        (CLOSED, "Closed (invoiced)"),
        (CANCELLED, "Cancelled"),
    ]
    EDITABLE_STATUSES = (QUOTE,)
    # Orders whose unshipped quantity is committed (reserves stock / counts as open orders)
    RESERVING_STATUSES = (PENDING, CONFIRMED, PARTIAL, BACKORDER)
    SHIPPABLE_STATUSES = (CONFIRMED, PARTIAL, BACKORDER)
    OPEN_STATUSES = (PENDING, CONFIRMED, PARTIAL, BACKORDER, SHIPPED)

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="sales_orders")
    buyer = models.ForeignKey(CustomerBuyer, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="sales_orders", help_text="The customer's buyer who placed the order")
    order_date = models.DateField()
    requested_date = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True, help_text="Quote expiry")
    sales_rep = models.ForeignKey("salesteam.SalesRep", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="sales_orders")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, verbose_name="Default warehouse",
                                  help_text="Pre-fills the warehouse on new lines; each line can ship from its own warehouse")
    customer_po = models.CharField("Customer PO #", max_length=60, blank=True)
    tax_rate = models.DecimalField("Tax %", max_digits=6, decimal_places=3, default=ZERO)
    shipping_address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=QUOTE, editable=False)
    credit_override = models.BooleanField(default=False, editable=False)
    approval_reason = models.CharField(max_length=255, blank=True, editable=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", editable=False)
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   editable=False)

    class Meta:
        ordering = ["-order_date", "-id"]

    def get_absolute_url(self):
        return reverse("sales:salesorder_detail", args=[self.pk])

    @property
    def subtotal(self):
        return money(sum((line.amount for line in self.lines.all()), ZERO))

    @property
    def tax(self):
        return money(self.subtotal * self.tax_rate / 100)

    @property
    def total(self):
        return self.subtotal + self.tax

    @property
    def uninvoiced_total(self):
        sub = sum((line.unit_net * (line.quantity - line.qty_invoiced) for line in self.lines.all()), ZERO)
        return money(sub * (1 + self.tax_rate / 100))

    @property
    def estimated_margin(self):
        return money(sum((line.amount - line.quantity * line.product.avg_cost for line in self.lines.all()), ZERO))

    def refresh_status(self):
        if self.status in (self.QUOTE, self.PENDING, self.CANCELLED):
            return
        self.status = self.status_from_lines(list(self.lines.all()))
        self.save(update_fields=["status"])

    @classmethod
    def status_from_lines(cls, lines):
        """Closed > Shipped > Backordered (all shipped qty invoiced, balance open) > Partially shipped > Open."""
        if lines and all(line.qty_invoiced >= line.quantity for line in lines):
            return cls.CLOSED
        if lines and all(line.qty_shipped >= line.quantity for line in lines):
            return cls.SHIPPED
        if any(line.qty_shipped > 0 for line in lines):
            if all(line.qty_invoiced >= line.qty_shipped for line in lines):
                return cls.BACKORDER
            return cls.PARTIAL
        return cls.CONFIRMED

    @property
    def qty_backordered(self):
        if self.status != self.BACKORDER:
            return 0
        return sum((line.qty_to_ship for line in self.lines.all()), ZERO)


class SalesOrderLine(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="so_lines")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="so_lines",
                                  help_text="Warehouse this line ships from")
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                     help_text="Blank = price list / list price")
    discount_pct = models.DecimalField("Disc %", max_digits=5, decimal_places=2, default=ZERO)
    qty_shipped = models.DecimalField(max_digits=14, decimal_places=3, default=ZERO, editable=False)
    qty_invoiced = models.DecimalField(max_digits=14, decimal_places=3, default=ZERO, editable=False)
    cost_shipped = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False,
                                       help_text="Total COGS of quantities shipped")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.order.number} / {self.product.sku}"

    def save(self, *args, **kwargs):
        if not self.warehouse_id and self.order_id:
            self.warehouse_id = self.order.warehouse_id
        if self.unit_price is None and self.product_id:
            from products.models import get_price

            self.unit_price = get_price(self.product, self.order.customer, self.quantity)
        super().save(*args, **kwargs)

    @property
    def unit_net(self):
        return (self.unit_price or ZERO) * (1 - (self.discount_pct or ZERO) / 100)

    @property
    def amount(self):
        return money(self.unit_net * (self.quantity or ZERO))

    @property
    def qty_to_ship(self):
        return self.quantity - self.qty_shipped

    @property
    def qty_to_invoice(self):
        return self.qty_shipped - self.qty_invoiced

    @property
    def avg_unit_cost_shipped(self):
        return (self.cost_shipped / self.qty_shipped) if self.qty_shipped else self.product.avg_cost


class Delivery(NumberedMixin):
    SEQUENCE_KEY = "delivery"
    SEQUENCE_PREFIX = "DEL-"
    order = models.ForeignKey(SalesOrder, on_delete=models.PROTECT, related_name="deliveries")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT)
    date = models.DateField()
    carrier = models.CharField(max_length=100, blank=True)
    tracking_number = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    journal_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name_plural = "deliveries"

    def get_absolute_url(self):
        return reverse("sales:delivery_detail", args=[self.pk])

    @property
    def total_cost(self):
        return money(sum((line.quantity * line.unit_cost for line in self.lines.all()), ZERO))


class DeliveryLine(models.Model):
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="lines")
    so_line = models.ForeignKey(SalesOrderLine, on_delete=models.PROTECT, related_name="delivery_lines")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
