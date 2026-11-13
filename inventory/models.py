from django.conf import settings
from django.db import models
from django.urls import reverse

from core.models import AddressMixin, NumberedMixin
from core.tenancy import TenantModel
from core.utils import ZERO


class Warehouse(TenantModel, AddressMixin):
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("company", "code")
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"

    def get_absolute_url(self):
        return reverse("inventory:warehouse_detail", args=[self.pk])


class StockQuant(TenantModel):
    product = models.ForeignKey("products.Product", on_delete=models.CASCADE, related_name="quants")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name="quants")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=ZERO)

    class Meta:
        unique_together = ("product", "warehouse")

    def __str__(self):
        return f"{self.product.sku} @ {self.warehouse.code}: {self.quantity}"


class StockMove(TenantModel):
    RECEIPT = "receipt"
    SHIPMENT = "shipment"
    ADJUSTMENT = "adjustment"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    TYPES = [
        (RECEIPT, "Receipt"),
        (SHIPMENT, "Customer shipment"),
        (ADJUSTMENT, "Adjustment"),
        (TRANSFER_IN, "Transfer in"),
        (TRANSFER_OUT, "Transfer out"),
    ]
    date = models.DateField(db_index=True)
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="moves")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="moves")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, help_text="Positive in, negative out")
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=ZERO)
    move_type = models.CharField(max_length=20, choices=TYPES)
    reference = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.date} {self.product.sku} {self.quantity}"

    @property
    def total_cost(self):
        return self.quantity * self.unit_cost


class StockAdjustment(NumberedMixin):
    SEQUENCE_KEY = "adjustment"
    SEQUENCE_PREFIX = "ADJ-"
    DRAFT, POSTED = "draft", "posted"
    STATUSES = [(DRAFT, "Draft"), (POSTED, "Posted")]
    EDITABLE_STATUSES = (DRAFT,)
    REASONS = [
        ("count", "Cycle / physical count"),
        ("damage", "Damaged goods"),
        ("loss", "Loss / shrinkage"),
        ("found", "Found stock"),
        ("opening", "Opening balance"),
        ("other", "Other"),
    ]
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT)
    date = models.DateField()
    reason = models.CharField(max_length=20, choices=REASONS, default="count")
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   editable=False)

    class Meta:
        unique_together = ("company", "number")
        ordering = ["-date", "-id"]

    def get_absolute_url(self):
        return reverse("inventory:stockadjustment_detail", args=[self.pk])


class StockAdjustmentLine(TenantModel):
    adjustment = models.ForeignKey(StockAdjustment, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT)
    quantity = models.DecimalField("Qty change (+/-)", max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True,
                                    help_text="For increases; blank = current average cost")


class StockTransfer(NumberedMixin):
    SEQUENCE_KEY = "transfer"
    SEQUENCE_PREFIX = "TRF-"
    DRAFT, POSTED = "draft", "posted"
    STATUSES = [(DRAFT, "Draft"), (POSTED, "Posted")]
    EDITABLE_STATUSES = (DRAFT,)
    from_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="transfers_out")
    to_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="transfers_in")
    date = models.DateField()
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   editable=False)

    class Meta:
        unique_together = ("company", "number")
        ordering = ["-date", "-id"]

    def get_absolute_url(self):
        return reverse("inventory:stocktransfer_detail", args=[self.pk])


class StockTransferLine(TenantModel):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
