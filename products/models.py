from decimal import Decimal

from django.db import models
from django.db.models import F, Sum
from django.urls import reverse

from core.models import TimeStamped
from core.utils import ZERO


class Category(models.Model):
    name = models.CharField(max_length=100)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return f"{self.parent} / {self.name}" if self.parent_id else self.name

    def get_absolute_url(self):
        return reverse("products:category_update", args=[self.pk])


class UnitOfMeasure(models.Model):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=50)

    class Meta:
        ordering = ["code"]
        verbose_name = "unit of measure"

    def __str__(self):
        return self.code

    def get_absolute_url(self):
        return reverse("products:unitofmeasure_update", args=[self.pk])


class Product(TimeStamped):
    sku = models.CharField("SKU", max_length=50, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    uom = models.ForeignKey(UnitOfMeasure, null=True, blank=True, on_delete=models.PROTECT, verbose_name="UoM")
    barcode = models.CharField(max_length=50, blank=True)
    # Import / customs attributes
    hs_code = models.CharField("HS code", max_length=20, blank=True, help_text="Harmonized tariff code")
    country_of_origin = models.CharField(max_length=60, blank=True)
    duty_rate = models.DecimalField("Duty rate %", max_digits=6, decimal_places=3, default=ZERO)
    weight_kg = models.DecimalField("Weight (kg)", max_digits=10, decimal_places=3, default=ZERO)
    volume_cbm = models.DecimalField("Volume (m³)", max_digits=10, decimal_places=4, default=ZERO)
    units_per_carton = models.PositiveIntegerField(default=1)
    # Pricing & costing
    list_price = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    standard_cost = models.DecimalField(max_digits=14, decimal_places=4, default=ZERO,
                                        help_text="Reference cost; actual valuation uses moving average landed cost")
    avg_cost = models.DecimalField("Average landed cost", max_digits=14, decimal_places=4, default=ZERO,
                                   editable=False)
    # Replenishment
    preferred_vendor = models.ForeignKey("purchasing.Vendor", null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="preferred_products")
    reorder_point = models.DecimalField(max_digits=12, decimal_places=3, default=ZERO)
    reorder_qty = models.DecimalField(max_digits=12, decimal_places=3, default=ZERO)
    lead_time_days = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sku"]

    def __str__(self):
        return f"{self.sku} — {self.name}"

    def get_absolute_url(self):
        return reverse("products:product_detail", args=[self.pk])

    @property
    def qty_on_hand(self):
        return self.quants.aggregate(t=Sum("quantity"))["t"] or ZERO

    @property
    def qty_on_order(self):
        from purchasing.models import PurchaseOrder

        open_statuses = [PurchaseOrder.SENT, PurchaseOrder.CONFIRMED, PurchaseOrder.PARTIAL, PurchaseOrder.DRAFT]
        agg = self.po_lines.filter(order__status__in=open_statuses).aggregate(
            t=Sum(F("quantity") - F("qty_received"))
        )
        return agg["t"] or ZERO

    @property
    def qty_reserved(self):
        from sales.models import SalesOrder

        agg = self.so_lines.filter(order__status__in=[SalesOrder.CONFIRMED, SalesOrder.PARTIAL]).aggregate(
            t=Sum(F("quantity") - F("qty_shipped"))
        )
        return agg["t"] or ZERO

    @property
    def qty_available(self):
        return self.qty_on_hand - self.qty_reserved

    @property
    def inventory_value(self):
        return (self.qty_on_hand * self.avg_cost).quantize(Decimal("0.01"))

    @property
    def margin_pct(self):
        if not self.list_price:
            return None
        return (self.list_price - self.avg_cost) / self.list_price * 100


class VendorProduct(models.Model):
    vendor = models.ForeignKey("purchasing.Vendor", on_delete=models.CASCADE, related_name="vendor_products")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="vendor_products")
    vendor_sku = models.CharField("Vendor SKU", max_length=50, blank=True)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=ZERO)
    currency = models.ForeignKey("core.Currency", on_delete=models.PROTECT)
    moq = models.DecimalField("MOQ", max_digits=12, decimal_places=3, default=ZERO)
    lead_time_days = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("vendor", "product")
        ordering = ["vendor__name", "product__sku"]
        verbose_name = "vendor price"

    def __str__(self):
        return f"{self.vendor} / {self.product.sku}"

    def get_absolute_url(self):
        return reverse("products:vendorproduct_update", args=[self.pk])


class PriceList(models.Model):
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("products:pricelist_detail", args=[self.pk])


class PriceListItem(models.Model):
    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="price_list_items")
    min_qty = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("1"))
    price = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        unique_together = ("price_list", "product", "min_qty")
        ordering = ["product__sku", "min_qty"]

    def __str__(self):
        return f"{self.product.sku} @ {self.price} (≥{self.min_qty})"


def get_price(product, customer=None, qty=1):
    """Best price for a customer: price list tier (by quantity) else product list price."""
    if customer and customer.price_list_id:
        item = (
            PriceListItem.objects.filter(price_list_id=customer.price_list_id, product=product, min_qty__lte=qty)
            .order_by("-min_qty")
            .first()
        )
        if item:
            return item.price
    return product.list_price
