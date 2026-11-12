from decimal import Decimal

from django.db import models
from django.db.models import F, Sum
from django.urls import reverse

from core.models import TimeStamped
from core.utils import ZERO


class LookupValue(models.Model):
    """Values for the item-detail drop-downs (Brand, Season, Material...). Each kind has its own menu."""

    KINDS = [
        ("brand", "Brands"),
        ("package_size", "Package Sizes"),
        ("season", "Seasons"),
        ("seasonal_cutoff", "Seasonal Cutoffs"),
        ("gender", "Genders"),
        ("size_range", "Size Ranges"),
        ("size_breakdown", "Size Breakdowns"),
        ("item_spec", "Item Specs"),
        ("material", "Materials"),
        ("customer_code", "Customer Codes"),
        ("label", "Labels"),
    ]
    SINGULAR = {
        "brand": "Brand", "package_size": "Package Size", "season": "Season", "seasonal_cutoff": "Seasonal Cutoff",
        "gender": "Gender", "size_range": "Size Range", "size_breakdown": "Size Breakdown", "item_spec": "Item Spec",
        "material": "Material", "customer_code": "Customer Code", "label": "Label",
    }
    kind = models.CharField(max_length=20, choices=KINDS, db_index=True)
    code = models.CharField(max_length=40)
    description = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("kind", "code")
        ordering = ["kind", "code"]
        verbose_name = "lookup value"

    def __str__(self):
        return f"{self.code} - {self.description}" if self.description else self.code

    def get_absolute_url(self):
        return reverse("products:lookup_update", args=[self.kind, self.pk])


class ItemClass(models.Model):
    """Three-tier item classification: Tier 1 Class -> Tier 2 Subclass -> Tier 3 Product Type."""

    TIERS = [(1, "Tier 1 — Class"), (2, "Tier 2 — Subclass"), (3, "Tier 3 — Product Type")]
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children",
                               limit_choices_to={"level__lt": 3},
                               help_text="Leave blank for a Tier 1 class; pick a Tier 1 for a subclass, "
                                         "a Tier 2 for a product type")
    code = models.CharField(max_length=30)
    description = models.CharField(max_length=100)
    level = models.PositiveSmallIntegerField(choices=TIERS, editable=False, default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("parent", "code")
        ordering = ["level", "code"]
        verbose_name = "item class"
        verbose_name_plural = "item classes"

    def __str__(self):
        return self.full_code

    def get_absolute_url(self):
        return reverse("products:itemclass_update", args=[self.pk])

    def save(self, *args, **kwargs):
        self.level = (self.parent.level + 1) if self.parent_id else 1
        if self.level > 3:
            from django.core.exceptions import ValidationError

            raise ValidationError("Item classes have at most three tiers.")
        super().save(*args, **kwargs)

    @property
    def chain(self):
        node, out = self, []
        while node is not None:
            out.insert(0, node)
            node = node.parent
        return out

    @property
    def full_code(self):
        return " -".join(n.code for n in self.chain)

    @property
    def full_description(self):
        return " / ".join(n.description for n in self.chain)

    def tier(self, n):
        chain = self.chain
        return chain[n - 1] if len(chain) >= n else None


class HTSCode(models.Model):
    """HTS code, duty and tariff for an item class + material (+ gender) combination."""

    item_class = models.ForeignKey(ItemClass, on_delete=models.CASCADE, related_name="hts_codes",
                                   limit_choices_to={"level": 3}, verbose_name="Item class")
    material = models.ForeignKey(LookupValue, on_delete=models.PROTECT, related_name="+",
                                 limit_choices_to={"kind": "material"})
    gender = models.ForeignKey(LookupValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                               limit_choices_to={"kind": "gender"}, help_text="Blank = applies to any gender")
    hts_code = models.CharField("HTS code", max_length=20, blank=True, help_text="Blank = waiting on info")
    duty_rate = models.DecimalField("Duty rate %", max_digits=6, decimal_places=3, default=ZERO)
    tariff_rate = models.DecimalField("Tariff rate %", max_digits=6, decimal_places=3, default=ZERO,
                                      help_text="Additional tariffs (e.g. Section 301)")
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("item_class", "material", "gender")
        ordering = ["item_class__parent__parent__code", "item_class__parent__code", "item_class__code",
                    "material__code"]
        verbose_name = "HTS code"
        verbose_name_plural = "HTS codes"

    def __str__(self):
        return f"{self.item_class} / {self.material.code} / {self.gender.code if self.gender else 'any'}"

    def get_absolute_url(self):
        return reverse("products:htscode_update", args=[self.pk])

    @classmethod
    def match(cls, item_class_id, material_id, gender_id):
        if not item_class_id or not material_id:
            return None
        qs = cls.objects.filter(item_class_id=item_class_id, material_id=material_id)
        exact = qs.filter(gender_id=gender_id).first() if gender_id else None
        return exact or qs.filter(gender__isnull=True).first()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Push the classification to every product that follows this row (and isn't manually overridden)
        products = Product.objects.filter(item_class_id=self.item_class_id, material_id=self.material_id,
                                          hts_override=False)
        if self.gender_id:
            products = products.filter(gender_id=self.gender_id)
        for p in products:
            if HTSCode.match(p.item_class_id, p.material_id, p.gender_id) == self:
                Product.objects.filter(pk=p.pk).update(hs_code=self.hts_code, duty_rate=self.duty_rate,
                                                       tariff_rate=self.tariff_rate)


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
    item_class = models.ForeignKey(ItemClass, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="products", limit_choices_to={"level": 3},
                                   help_text="Tier 3 product type (its Tier 1 class and Tier 2 subclass follow)")
    uom = models.ForeignKey(UnitOfMeasure, null=True, blank=True, on_delete=models.PROTECT, verbose_name="UoM")
    barcode = models.CharField(max_length=50, blank=True)
    # Item details
    brand = models.ForeignKey(LookupValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                              limit_choices_to={"kind": "brand"})
    package_size = models.ForeignKey(LookupValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                     limit_choices_to={"kind": "package_size"})
    season = models.ForeignKey(LookupValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                               limit_choices_to={"kind": "season"})
    seasonal_cutoff = models.ForeignKey(LookupValue, null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+", limit_choices_to={"kind": "seasonal_cutoff"})
    gender = models.ForeignKey(LookupValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                               limit_choices_to={"kind": "gender"})
    size_range = models.ForeignKey(LookupValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                   limit_choices_to={"kind": "size_range"})
    size_breakdown = models.ForeignKey(LookupValue, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="+", limit_choices_to={"kind": "size_breakdown"})
    item_spec = models.ForeignKey(LookupValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                  limit_choices_to={"kind": "item_spec"})
    material = models.ForeignKey(LookupValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                 limit_choices_to={"kind": "material"})
    customer_code = models.ForeignKey(LookupValue, null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+", limit_choices_to={"kind": "customer_code"})
    label1 = models.ForeignKey(LookupValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                               limit_choices_to={"kind": "label"}, verbose_name="Label 1")
    label2 = models.ForeignKey(LookupValue, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                               limit_choices_to={"kind": "label"}, verbose_name="Label 2")
    multi_pc_set = models.BooleanField("Multi PC set", default=False)
    monthly_carrying_cost = models.DecimalField(max_digits=10, decimal_places=4, default=ZERO)
    # Import / customs attributes
    hs_code = models.CharField("HTS code", max_length=20, blank=True, help_text="Harmonized tariff code")
    hts_override = models.BooleanField("Override HTS table", default=False,
                                       help_text="Tick to keep your own HTS / duty / tariff instead of the HTS table")
    country_of_origin = models.CharField(max_length=60, blank=True)
    duty_rate = models.DecimalField("Duty rate %", max_digits=6, decimal_places=3, default=ZERO)
    tariff_rate = models.DecimalField("Tariff rate %", max_digits=6, decimal_places=3, default=ZERO)
    weight_kg = models.DecimalField("Unit weight (kg)", max_digits=10, decimal_places=3, default=ZERO)
    volume_cbm = models.DecimalField("Unit volume (m³)", max_digits=10, decimal_places=6, default=ZERO,
                                     help_text="Calculated from the carton when carton size is entered")
    # Packaging
    prepack_qty = models.PositiveIntegerField("Prepack qty", default=1, help_text="Units per inner pack")
    carton_qty = models.PositiveIntegerField("Carton qty", default=1, help_text="Units per master carton")
    DIM_UNITS = [("in", "inches"), ("cm", "cm")]
    carton_length = models.DecimalField(max_digits=8, decimal_places=2, default=ZERO)
    carton_width = models.DecimalField(max_digits=8, decimal_places=2, default=ZERO)
    carton_height = models.DecimalField(max_digits=8, decimal_places=2, default=ZERO)
    dimension_unit = models.CharField(max_length=2, choices=DIM_UNITS, default="in")
    carton_cbm = models.DecimalField("Carton CBM", max_digits=10, decimal_places=4, default=ZERO,
                                     help_text="Calculated from L × W × H; enter directly if dimensions unknown")
    carton_weight_kg = models.DecimalField("Carton gross weight (kg)", max_digits=10, decimal_places=3,
                                           default=ZERO)
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

    CBM_PER_CUBIC_INCH = Decimal("0.000016387064")
    CUFT_PER_CBM = Decimal("35.3146667")

    def save(self, *args, **kwargs):
        from core.utils import cost

        # Carton volume from dimensions
        if self.carton_length and self.carton_width and self.carton_height:
            vol = self.carton_length * self.carton_width * self.carton_height
            factor = self.CBM_PER_CUBIC_INCH if self.dimension_unit == "in" else Decimal("0.000001")
            self.carton_cbm = (vol * factor).quantize(Decimal("0.0001"))
        # Per-unit volume / weight follow the carton (keeps landed-cost allocation accurate)
        if self.carton_cbm and self.carton_qty:
            self.volume_cbm = (self.carton_cbm / self.carton_qty).quantize(Decimal("0.000001"))
        if self.carton_weight_kg and self.carton_qty:
            self.weight_kg = cost(self.carton_weight_kg / self.carton_qty).quantize(Decimal("0.001"))
        # HTS / duty / tariff from the HTS table unless overridden
        if not self.hts_override:
            row = HTSCode.match(self.item_class_id, self.material_id, self.gender_id)
            if row:
                self.hs_code, self.duty_rate, self.tariff_rate = row.hts_code, row.duty_rate, row.tariff_rate
        if self.barcode:
            from .upc import complete

            self.barcode = complete(self.barcode)
        super().save(*args, **kwargs)

    @property
    def hts_match(self):
        return HTSCode.match(self.item_class_id, self.material_id, self.gender_id)

    @property
    def carton_cuft(self):
        return (self.carton_cbm * self.CUFT_PER_CBM).quantize(Decimal("0.0001")) if self.carton_cbm else ZERO

    def cartons_per(self, container_cbm):
        if not self.carton_cbm:
            return None
        return int(container_cbm / self.carton_cbm)

    def qty_per(self, container_cbm):
        cartons = self.cartons_per(container_cbm)
        return None if cartons is None else cartons * self.carton_qty

    @property
    def qty_per_40hq(self):
        from core.models import Company

        return self.qty_per(Company.get().cbm_40hq)

    @property
    def container_capacity(self):
        """[(label, container CBM, cartons, units)] using the company's container sizes."""
        from core.models import Company

        return [(label, cbm, self.cartons_per(cbm), self.qty_per(cbm)) for label, cbm in Company.get().containers]

    @property
    def total_duty_rate(self):
        return (self.duty_rate or ZERO) + (self.tariff_rate or ZERO)

    @property
    def primary_image(self):
        images = list(self.images.all())
        for img in images:
            if img.is_primary:
                return img
        return images[0] if images else None

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

        agg = self.so_lines.filter(order__status__in=SalesOrder.RESERVING_STATUSES).aggregate(
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


class ProductImage(models.Model):
    """Item photo. Stored in the database so it survives redeploys (no persistent disk needed)."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    data = models.BinaryField()
    thumbnail = models.BinaryField()
    content_type = models.CharField(max_length=50, default="image/jpeg")
    filename = models.CharField(max_length=200, blank=True)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    is_primary = models.BooleanField(default=False)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_primary", "id"]

    def __str__(self):
        return f"{self.product.sku} image {self.pk}"

    def get_absolute_url(self):
        return reverse("products:image", args=[self.pk])

    @property
    def thumb_url(self):
        return reverse("products:image_thumb", args=[self.pk])


class ProductCrossReference(models.Model):
    """Alternate identifiers: barcodes / UPCs, vendor part numbers, customer part numbers."""

    BARCODE, VENDOR, CUSTOMER, GLOBAL = "barcode", "vendor", "customer", "global"
    TYPES = [(BARCODE, "Barcode / UPC"), (VENDOR, "Vendor part number"), (CUSTOMER, "Customer part number"),
             (GLOBAL, "Global / other")]
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="cross_references")
    alt_type = models.CharField("Alternate type", max_length=10, choices=TYPES, default=BARCODE)
    vendor = models.ForeignKey("purchasing.Vendor", null=True, blank=True, on_delete=models.CASCADE,
                               related_name="+")
    customer = models.ForeignKey("sales.Customer", null=True, blank=True, on_delete=models.CASCADE,
                                 related_name="+")
    alternate_id = models.CharField("Alternate ID", max_length=60)
    uom = models.ForeignKey(UnitOfMeasure, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="UoM")
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["alt_type", "alternate_id"]
        verbose_name = "cross-reference"

    def __str__(self):
        return f"{self.get_alt_type_display()} {self.alternate_id}"

    def clean(self):
        from django.core.exceptions import ValidationError

        from .upc import complete, is_valid

        if self.alt_type == self.BARCODE:
            self.alternate_id = complete(self.alternate_id)
            if not is_valid(self.alternate_id):
                raise ValidationError({"alternate_id": "Invalid UPC/EAN check digit."})
            dup = ProductCrossReference.objects.filter(alt_type=self.BARCODE, alternate_id=self.alternate_id) \
                .exclude(pk=self.pk).exclude(product_id=self.product_id).first()
            if dup:
                raise ValidationError({"alternate_id": f"Barcode already used on {dup.product.sku}."})


class ProductWarehouse(models.Model):
    """Per-warehouse stocking settings for an item."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="warehouse_settings")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.CASCADE, related_name="product_settings")
    bin_location = models.CharField(max_length=40, blank=True)
    reorder_point = models.DecimalField(max_digits=12, decimal_places=3, default=ZERO)
    reorder_qty = models.DecimalField(max_digits=12, decimal_places=3, default=ZERO)
    max_qty = models.DecimalField("Max qty", max_digits=12, decimal_places=3, default=ZERO)
    is_default = models.BooleanField("Default warehouse", default=False)

    class Meta:
        unique_together = ("product", "warehouse")
        ordering = ["warehouse__code"]
        verbose_name = "warehouse setting"

    def __str__(self):
        return f"{self.product.sku} @ {self.warehouse.code}"

    @property
    def qty_on_hand(self):
        q = self.product.quants.filter(warehouse=self.warehouse).first()
        return q.quantity if q else ZERO


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
