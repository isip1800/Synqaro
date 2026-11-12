from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.urls import reverse

from core.models import AddressMixin, NumberedMixin, TimeStamped
from core.utils import ZERO, money

INCOTERMS = [
    ("EXW", "EXW — Ex Works"),
    ("FCA", "FCA — Free Carrier"),
    ("FAS", "FAS — Free Alongside Ship"),
    ("FOB", "FOB — Free On Board"),
    ("CFR", "CFR — Cost and Freight"),
    ("CIF", "CIF — Cost, Insurance & Freight"),
    ("CPT", "CPT — Carriage Paid To"),
    ("CIP", "CIP — Carriage & Insurance Paid"),
    ("DAP", "DAP — Delivered At Place"),
    ("DPU", "DPU — Delivered At Place Unloaded"),
    ("DDP", "DDP — Delivered Duty Paid"),
]


class Vendor(AddressMixin, TimeStamped):
    SUPPLIER, FORWARDER, BROKER, CARRIER, OTHER = "supplier", "forwarder", "broker", "carrier", "other"
    TYPES = [
        (SUPPLIER, "Supplier / manufacturer"),
        (FORWARDER, "Freight forwarder"),
        (BROKER, "Customs broker"),
        (CARRIER, "Carrier / trucking"),
        (OTHER, "Other"),
    ]
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200)
    vendor_type = models.CharField(max_length=20, choices=TYPES, default=SUPPLIER)
    contact_name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    website = models.URLField(blank=True)
    tax_id = models.CharField(max_length=50, blank=True)
    currency = models.ForeignKey("core.Currency", on_delete=models.PROTECT, default="USD")
    payment_terms_days = models.PositiveIntegerField(default=30)
    default_incoterm = models.CharField(max_length=3, choices=INCOTERMS, blank=True)
    bank_details = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("purchasing:vendor_detail", args=[self.pk])

    @property
    def balance(self):
        """Open payables in base currency (credit balance on AP)."""
        from accounting.models import JournalLine
        from accounting.services import acct

        agg = JournalLine.objects.filter(vendor=self, account=acct("AP")).aggregate(
            dr=Sum("debit"), cr=Sum("credit")
        )
        return (agg["cr"] or ZERO) - (agg["dr"] or ZERO)


class PurchaseOrder(NumberedMixin, TimeStamped):
    SEQUENCE_KEY = "po"
    SEQUENCE_PREFIX = "PO-"
    DRAFT, SENT, CONFIRMED, PARTIAL, RECEIVED, CLOSED, CANCELLED = (
        "draft", "sent", "confirmed", "partial", "received", "closed", "cancelled")
    STATUSES = [
        (DRAFT, "Draft"),
        (SENT, "Sent to vendor"),
        (CONFIRMED, "Confirmed by vendor"),
        (PARTIAL, "Partially received"),
        (RECEIVED, "Received"),
        (CLOSED, "Closed"),
        (CANCELLED, "Cancelled"),
    ]
    EDITABLE_STATUSES = (DRAFT, SENT)
    OPEN_STATUSES = (SENT, CONFIRMED, PARTIAL)

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="purchase_orders")
    order_date = models.DateField()
    expected_date = models.DateField(null=True, blank=True, help_text="Expected ready / ship date")
    currency = models.ForeignKey("core.Currency", on_delete=models.PROTECT)
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"),
                                        help_text="Base currency per 1 unit of PO currency")
    incoterm = models.CharField(max_length=3, choices=INCOTERMS, blank=True)
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, verbose_name="Default warehouse",
                                  help_text="Pre-fills the warehouse on new lines; each line can go to its own warehouse")
    payment_terms = models.CharField(max_length=100, blank=True, help_text="e.g. 30% deposit, 70% against B/L")
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=DRAFT, editable=False)
    vendor_reference = models.CharField(max_length=60, blank=True, help_text="Vendor's proforma / order no.")
    vendor_confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)
    vendor_confirmed_ship_date = models.DateField(null=True, blank=True, editable=False)
    vendor_notes = models.TextField(blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   editable=False)

    class Meta:
        ordering = ["-order_date", "-id"]

    def get_absolute_url(self):
        return reverse("purchasing:purchaseorder_detail", args=[self.pk])

    @property
    def total(self):
        return money(sum((line.amount for line in self.lines.all()), ZERO))

    @property
    def total_base(self):
        return money(self.total * self.exchange_rate)

    @property
    def fs_totals(self):
        lines = list(self.lines.all())
        return {"usd": sum((ln.ext_fs_cost_usd for ln in lines), ZERO),
                "rmb": sum((ln.ext_fs_cost_rmb for ln in lines), ZERO),
                "any": any(ln.fs_cost_usd for ln in lines)}

    def refresh_receipt_status(self):
        if self.status in (self.CANCELLED, self.CLOSED, self.DRAFT):
            return
        lines = list(self.lines.all())
        received_any = any(line.qty_received > 0 for line in lines)
        received_all = lines and all(line.qty_received >= line.quantity for line in lines)
        if received_all:
            self.status = self.RECEIVED
        elif received_any:
            self.status = self.PARTIAL
        self.save(update_fields=["status"])


class PurchaseOrderLine(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="po_lines")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="po_lines",
                                  help_text="Warehouse this line is received into")
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=4, help_text="In PO currency")
    fs_cost_usd = models.DecimalField("FS cost USD", max_digits=14, decimal_places=4, default=ZERO,
                                      help_text="First Sale (factory) unit cost in USD; used as the dutiable value")
    fs_cost_rmb = models.DecimalField("FS cost RMB", max_digits=14, decimal_places=4, default=ZERO, editable=False,
                                      help_text="FS cost USD converted at the RMB (CNY) exchange rate")
    qty_received = models.DecimalField(max_digits=14, decimal_places=3, default=ZERO, editable=False)
    qty_billed = models.DecimalField(max_digits=14, decimal_places=3, default=ZERO, editable=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.order.number} / {self.product.sku}" + (f" @ {self.warehouse.code}" if self.warehouse_id else "")

    def save(self, *args, **kwargs):
        if not self.warehouse_id and self.order_id:
            self.warehouse_id = self.order.warehouse_id
        self.fs_cost_rmb = self.compute_fs_rmb()
        super().save(*args, **kwargs)

    def fs_rmb_rate(self):
        """USD (base) per 1 RMB: the PO's own rate for RMB POs, otherwise the CNY rate on the order date."""
        from core.models import ExchangeRate

        if self.order.currency_id == "CNY":
            return self.order.exchange_rate
        return ExchangeRate.get_rate("CNY", self.order.order_date)

    def compute_fs_rmb(self):
        from core.utils import cost

        if not self.fs_cost_usd:
            return ZERO
        rate = self.fs_rmb_rate()
        return cost(self.fs_cost_usd / rate) if rate and rate != 1 else ZERO

    @property
    def ext_fs_cost_usd(self):
        return money((self.quantity or ZERO) * (self.fs_cost_usd or ZERO))

    @property
    def ext_fs_cost_rmb(self):
        return money((self.quantity or ZERO) * (self.fs_cost_rmb or ZERO))

    def dutiable_unit_value(self):
        """First Sale cost when entered, otherwise the PO price in base currency."""
        return self.fs_cost_usd if self.fs_cost_usd else self.unit_cost_base

    @property
    def amount(self):
        return money((self.quantity or ZERO) * (self.unit_price or ZERO))

    @property
    def qty_open(self):
        return self.quantity - self.qty_received

    @property
    def qty_on_shipments(self):
        """Quantity booked on shipments not yet received."""
        return self.shipment_lines.exclude(
            shipment__status__in=[Shipment.RECEIVED, Shipment.CANCELLED]
        ).aggregate(t=Sum("quantity"))["t"] or ZERO

    @property
    def qty_unshipped(self):
        return self.qty_open - self.qty_on_shipments

    @property
    def unit_cost_base(self):
        return self.unit_price * self.order.exchange_rate


class Shipment(NumberedMixin, TimeStamped):
    """An inbound import shipment (container, air freight, etc.) carrying goods from one or more POs."""

    SEQUENCE_KEY = "shipment"
    SEQUENCE_PREFIX = "SHP-"
    PLANNED, BOOKED, IN_TRANSIT, AT_PORT, CUSTOMS, CLEARED, RECEIVED, CANCELLED = (
        "planned", "booked", "in_transit", "at_port", "customs", "cleared", "received", "cancelled")
    STATUSES = [
        (PLANNED, "Planned"),
        (BOOKED, "Booked"),
        (IN_TRANSIT, "In transit"),
        (AT_PORT, "Arrived at port"),
        (CUSTOMS, "In customs"),
        (CLEARED, "Customs cleared"),
        (RECEIVED, "Received in warehouse"),
        (CANCELLED, "Cancelled"),
    ]
    EDITABLE_STATUSES = (PLANNED, BOOKED, IN_TRANSIT, AT_PORT, CUSTOMS, CLEARED)
    MODES = [("sea_fcl", "Sea — FCL"), ("sea_lcl", "Sea — LCL"), ("air", "Air"), ("road", "Road"),
             ("rail", "Rail"), ("courier", "Courier")]
    ALLOC_VALUE, ALLOC_QTY, ALLOC_WEIGHT, ALLOC_VOLUME = "value", "quantity", "weight", "volume"
    ALLOCATIONS = [
        (ALLOC_VALUE, "By goods value"),
        (ALLOC_QTY, "By quantity"),
        (ALLOC_WEIGHT, "By weight"),
        (ALLOC_VOLUME, "By volume (CBM)"),
    ]

    vendor = models.ForeignKey(Vendor, null=True, blank=True, on_delete=models.PROTECT, related_name="shipments",
                               help_text="Shipper / supplier")
    forwarder = models.ForeignKey(Vendor, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="forwarded_shipments")
    mode = models.CharField(max_length=10, choices=MODES, default="sea_fcl")
    incoterm = models.CharField(max_length=3, choices=INCOTERMS, blank=True)
    carrier = models.CharField(max_length=100, blank=True, help_text="Shipping line / airline")
    vessel_voyage = models.CharField("Vessel / voyage / flight", max_length=100, blank=True)
    origin_port = models.CharField(max_length=100, blank=True)
    destination_port = models.CharField(max_length=100, blank=True)
    container_numbers = models.TextField(blank=True, help_text="One per line, e.g. MSCU1234567 40HQ")
    bill_of_lading = models.CharField("B/L / AWB no.", max_length=60, blank=True)
    etd = models.DateField("ETD", null=True, blank=True)
    eta = models.DateField("ETA", null=True, blank=True)
    actual_arrival = models.DateField(null=True, blank=True)
    customs_entry_number = models.CharField(max_length=60, blank=True)
    customs_cleared_on = models.DateField(null=True, blank=True)
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, verbose_name="Main destination",
                                  help_text="Goods are received into each PO line's own warehouse")
    allocation_method = models.CharField(max_length=10, choices=ALLOCATIONS, default=ALLOC_VALUE,
                                         help_text="How landed costs are spread over the goods")
    status = models.CharField(max_length=12, choices=STATUSES, default=PLANNED)
    received_on = models.DateField(null=True, blank=True, editable=False)
    notes = models.TextField(blank=True)
    created_via_portal = models.BooleanField(default=False, editable=False)
    approved = models.BooleanField(default=False, editable=False,
                                   help_text="Buyer approved the booking (quantities / dates)")
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", editable=False)

    BOOKED_STATUSES = (PLANNED, BOOKED)
    IN_TRANSIT_STATUSES = (IN_TRANSIT, AT_PORT, CUSTOMS, CLEARED)

    class Meta:
        ordering = ["-eta", "-id"]

    def get_absolute_url(self):
        return reverse("purchasing:shipment_detail", args=[self.pk])

    @property
    def goods_value_base(self):
        return money(sum((line.value_base for line in self.lines.select_related("po_line__order")), ZERO))

    @property
    def landed_cost_total(self):
        return self.landed_costs.aggregate(t=Sum("amount"))["t"] or ZERO

    @property
    def total_landed_value(self):
        return self.goods_value_base + self.landed_cost_total

    @property
    def landed_cost_pct(self):
        gv = self.goods_value_base
        return (self.landed_cost_total / gv * 100) if gv else None

    @property
    def total_weight(self):
        return sum((line.quantity * line.po_line.product.weight_kg for line in self.lines.all()), ZERO)

    @property
    def total_volume(self):
        return sum((line.quantity * line.po_line.product.volume_cbm for line in self.lines.all()), ZERO)

    @property
    def purchase_orders(self):
        return PurchaseOrder.objects.filter(lines__shipment_lines__shipment=self).distinct()


class ShipmentLine(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="lines")
    po_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="shipment_lines")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    cartons = models.PositiveIntegerField(null=True, blank=True)
    # Set at receipt
    allocated_landed_cost = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, editable=False)
    unit_landed_cost = models.DecimalField(max_digits=14, decimal_places=4, default=ZERO, editable=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.shipment.number} / {self.po_line}"

    @property
    def product(self):
        return self.po_line.product

    @property
    def value_base(self):
        return money(self.quantity * self.po_line.unit_price * self.po_line.order.exchange_rate)

    @property
    def dutiable_value(self):
        """Customs value: First Sale cost when the PO line has one, otherwise the goods value."""
        if self.po_line.fs_cost_usd:
            return money(self.quantity * self.po_line.fs_cost_usd)
        return self.value_base


class LandedCost(models.Model):
    COST_TYPES = [
        ("freight", "Ocean / air freight"),
        ("insurance", "Cargo insurance"),
        ("duty", "Customs duty"),
        ("tariff", "Additional tariffs"),
        ("broker", "Customs brokerage"),
        ("port", "Port / terminal charges"),
        ("inland", "Inland trucking / drayage"),
        ("demurrage", "Demurrage / detention"),
        ("other", "Other"),
    ]
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="landed_costs")
    cost_type = models.CharField(max_length=12, choices=COST_TYPES)
    description = models.CharField(max_length=200, blank=True)
    vendor = models.ForeignKey(Vendor, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="landed_costs", help_text="Who will bill this cost")
    amount = models.DecimalField(max_digits=14, decimal_places=2, help_text="In base currency")
    capitalized = models.BooleanField(default=False, editable=False,
                                      help_text="Already added to inventory value")
    journal_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL,
                                      editable=False)

    class Meta:
        ordering = ["shipment", "id"]

    def __str__(self):
        return f"{self.shipment.number} {self.get_cost_type_display()} {self.amount}"

    def get_absolute_url(self):
        return self.shipment.get_absolute_url()


class ShipmentDocument(models.Model):
    DOC_TYPES = [
        ("commercial_invoice", "Commercial invoice"),
        ("packing_list", "Packing list"),
        ("bill_of_lading", "Bill of lading / AWB"),
        ("certificate_of_origin", "Certificate of origin"),
        ("customs_entry", "Customs entry"),
        ("insurance", "Insurance certificate"),
        ("inspection", "Inspection report"),
        ("other", "Other"),
    ]
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=30, choices=DOC_TYPES)
    file = models.FileField(upload_to="shipment_docs/%Y/%m/")
    description = models.CharField(max_length=200, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.get_doc_type_display()} ({self.shipment.number})"


class GoodsReceipt(NumberedMixin):
    SEQUENCE_KEY = "receipt"
    SEQUENCE_PREFIX = "GR-"
    purchase_order = models.ForeignKey(PurchaseOrder, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="receipts")
    shipment = models.ForeignKey(Shipment, null=True, blank=True, on_delete=models.PROTECT, related_name="receipts")
    warehouse = models.ForeignKey("inventory.Warehouse", null=True, blank=True, on_delete=models.PROTECT,
                                  help_text="Blank when lines went to several warehouses")
    date = models.DateField()
    notes = models.TextField(blank=True)
    journal_entry = models.ForeignKey("accounting.JournalEntry", null=True, blank=True, on_delete=models.SET_NULL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def get_absolute_url(self):
        return reverse("purchasing:goodsreceipt_detail", args=[self.pk])

    @property
    def total_value(self):
        return money(sum((line.total_cost for line in self.lines.all()), ZERO))


class GoodsReceiptLine(models.Model):
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    po_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipt_lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT)
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_goods_cost = models.DecimalField(max_digits=14, decimal_places=4)
    unit_landed_cost = models.DecimalField(max_digits=14, decimal_places=4, default=ZERO)

    @property
    def unit_cost(self):
        return self.unit_goods_cost + self.unit_landed_cost

    @property
    def total_cost(self):
        return self.quantity * self.unit_cost
