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


class SalesOrder(NumberedMixin, TimeStamped):
    SEQUENCE_KEY = "so"
    SEQUENCE_PREFIX = "SO-"
    QUOTE, CONFIRMED, PARTIAL, SHIPPED, CLOSED, CANCELLED = (
        "quote", "confirmed", "partial", "shipped", "closed", "cancelled")
    STATUSES = [
        (QUOTE, "Quotation"),
        (CONFIRMED, "Confirmed"),
        (PARTIAL, "Partially shipped"),
        (SHIPPED, "Shipped"),
        (CLOSED, "Closed (invoiced)"),
        (CANCELLED, "Cancelled"),
    ]
    EDITABLE_STATUSES = (QUOTE,)
    OPEN_STATUSES = (CONFIRMED, PARTIAL, SHIPPED)

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="sales_orders")
    order_date = models.DateField()
    requested_date = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True, help_text="Quote expiry")
    sales_rep = models.ForeignKey("salesteam.SalesRep", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="sales_orders")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT)
    customer_po = models.CharField("Customer PO #", max_length=60, blank=True)
    tax_rate = models.DecimalField("Tax %", max_digits=6, decimal_places=3, default=ZERO)
    shipping_address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=QUOTE, editable=False)
    credit_override = models.BooleanField(default=False, editable=False)
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
        if self.status in (self.QUOTE, self.CANCELLED):
            return
        lines = list(self.lines.all())
        if lines and all(line.qty_invoiced >= line.quantity for line in lines):
            self.status = self.CLOSED
        elif lines and all(line.qty_shipped >= line.quantity for line in lines):
            self.status = self.SHIPPED
        elif any(line.qty_shipped > 0 for line in lines):
            self.status = self.PARTIAL
        else:
            self.status = self.CONFIRMED
        self.save(update_fields=["status"])


class SalesOrderLine(models.Model):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="so_lines")
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
