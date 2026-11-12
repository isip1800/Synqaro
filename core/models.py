from decimal import Decimal

from django.db import models, transaction


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AddressMixin(models.Model):
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField("State / Province", max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, blank=True)

    class Meta:
        abstract = True

    @property
    def full_address(self):
        parts = [self.address, self.city, self.state, self.postal_code, self.country]
        return ", ".join(p for p in parts if p)


class Currency(models.Model):
    code = models.CharField(max_length=3, primary_key=True)
    name = models.CharField(max_length=50)
    symbol = models.CharField(max_length=5, blank=True)

    class Meta:
        verbose_name_plural = "currencies"
        ordering = ["code"]

    def __str__(self):
        return self.code

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("core:currency_update", args=[self.pk])


class ExchangeRate(models.Model):
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name="rates")
    date = models.DateField()
    rate = models.DecimalField(
        max_digits=18, decimal_places=6, help_text="Units of base currency per 1 unit of this currency"
    )

    class Meta:
        unique_together = ("currency", "date")
        ordering = ["-date", "currency"]

    def __str__(self):
        return f"{self.currency} {self.date}: {self.rate}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("core:exchangerate_update", args=[self.pk])

    @classmethod
    def get_rate(cls, currency, date=None):
        """Rate to convert 1 unit of `currency` into the company base currency."""
        code = getattr(currency, "code", currency)
        if not code or code == Company.get().base_currency_id:
            return Decimal("1")
        qs = cls.objects.filter(currency_id=code)
        if date:
            hit = qs.filter(date__lte=date).order_by("-date").first()
            if hit:
                return hit.rate
        hit = qs.order_by("-date").first()
        return hit.rate if hit else Decimal("1")


def default_sales_no_negative():
    return ["ats", "in_transit_available", "ats_in_transit", "booked_available", "available_to_sell"]


class Company(models.Model):
    name = models.CharField(max_length=200, default="My Import Company")
    legal_name = models.CharField(max_length=200, blank=True)
    tax_id = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    base_currency = models.ForeignKey(Currency, on_delete=models.PROTECT, default="USD")
    default_sales_tax_rate = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("0"))
    default_payment_terms_days = models.PositiveIntegerField(default=30)
    default_warehouse = models.ForeignKey("inventory.Warehouse", null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name="+", help_text="Pre-selected on new orders and shipments")
    require_so_approval = models.BooleanField(
        "Sales orders need approval", default=False,
        help_text="If ticked, every confirmed sales order waits for approval. Orders over the customer's credit "
                  "limit always need approval.")
    # Availability screens: formula fields that show 0 instead of a negative number
    avail_no_negative = models.JSONField("Inventory Availability: show negatives as 0", default=list, blank=True)
    sales_avail_no_negative = models.JSONField("Sales Availability: show negatives as 0",
                                               default=default_sales_no_negative, blank=True)
    # UPC generation
    upc_company_prefix = models.CharField("UPC company prefix", max_length=10, blank=True,
                                          help_text="Your GS1 company prefix, 6–10 digits (e.g. 676191)")
    upc_next_item_ref = models.PositiveIntegerField("Next UPC item reference", default=1,
                                                    help_text="Item reference used for the next generated UPC")
    # Container capacities used for 'qty per container'
    cbm_20 = models.DecimalField("20' container CBM", max_digits=6, decimal_places=2, default=Decimal("27"))
    cbm_40 = models.DecimalField("40' container CBM", max_digits=6, decimal_places=2, default=Decimal("58"))
    cbm_40hq = models.DecimalField("40' HQ container CBM", max_digits=6, decimal_places=2, default=Decimal("67"))
    cbm_45 = models.DecimalField("45' container CBM", max_digits=6, decimal_places=2, default=Decimal("76"))

    class Meta:
        verbose_name_plural = "company"

    def __str__(self):
        return self.name

    @property
    def containers(self):
        return [("20'", self.cbm_20), ("40'", self.cbm_40), ("40' HQ", self.cbm_40hq), ("45'", self.cbm_45)]

    @property
    def next_upc(self):
        from products.upc import build

        try:
            return build(self.upc_company_prefix, self.upc_next_item_ref)
        except ValueError:
            return None

    _cache = None

    @classmethod
    def get(cls):
        """The company record, read once per request (the cache is cleared when each request starts and on save)."""
        if cls._cache is not None:
            return cls._cache
        Currency.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "symbol": "$"})
        obj, _ = cls.objects.get_or_create(pk=1)
        cls._cache = obj
        return obj

    @classmethod
    def clear_cache(cls):
        cls._cache = None

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        Company.clear_cache()


def default_warehouse():
    from inventory.models import Warehouse

    company = Company.get()
    if company.default_warehouse_id and company.default_warehouse.is_active:
        return company.default_warehouse
    return Warehouse.objects.filter(is_active=True).first()


class Sequence(models.Model):
    key = models.CharField(max_length=50, unique=True)
    prefix = models.CharField(max_length=20)
    next_value = models.PositiveIntegerField(default=1)
    padding = models.PositiveSmallIntegerField(default=5)

    def __str__(self):
        return f"{self.key} ({self.prefix})"

    @classmethod
    def next(cls, key, prefix=None):
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(
                key=key, defaults={"prefix": prefix or f"{key.upper()}-"}
            )
            value = seq.next_value
            seq.next_value = value + 1
            seq.save(update_fields=["next_value"])
        return f"{seq.prefix}{value:0{seq.padding}d}"


class NumberedMixin(models.Model):
    """Assigns a document number from a Sequence on first save."""

    SEQUENCE_KEY = None
    SEQUENCE_PREFIX = None

    number = models.CharField(max_length=30, unique=True, blank=True, editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = Sequence.next(self.SEQUENCE_KEY, self.SEQUENCE_PREFIX)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.number


class UserPreference(models.Model):
    """Per-user settings such as chosen columns for a screen."""

    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="erp_preferences")
    key = models.CharField(max_length=100)
    value = models.JSONField(default=dict)

    class Meta:
        unique_together = ("user", "key")

    def __str__(self):
        return f"{self.user} {self.key}"


class SavedFilter(models.Model):
    """A named set of filters for a screen (stored as the query string)."""

    user = models.ForeignKey("auth.User", on_delete=models.CASCADE, related_name="saved_filters")
    screen = models.CharField(max_length=100)
    name = models.CharField(max_length=80)
    query = models.TextField(blank=True)
    shared = models.BooleanField(default=False, help_text="Visible to all staff")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# ----------------------------------------------------------------------------- files, notes, email

class Attachment(models.Model):
    """A file attached to any document (stored in the database so it survives redeploys)."""

    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100, default="application/octet-stream")
    size = models.PositiveIntegerField(default=0)
    data = models.BinaryField()
    description = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return self.filename


class Note(models.Model):
    """Time-stamped internal note on any document."""

    content_type = models.ForeignKey("contenttypes.ContentType", on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    text = models.TextField()
    author = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]


class EmailTemplate(models.Model):
    KINDS = [
        ("sales_order", "Sales order confirmation"),
        ("purchase_order", "Purchase order to vendor"),
        ("invoice", "Customer invoice"),
        ("statement", "Customer statement"),
    ]
    kind = models.CharField(max_length=20, choices=KINDS, unique=True)
    subject = models.CharField(max_length=255)
    body = models.TextField(help_text="Placeholders: {number} {company} {customer} {vendor} {buyer} {total} "
                                      "{currency} {customer_po} {date} {due_date} {ship_start} {cancel_date} {user}")
    attach_pdf = models.BooleanField("Attach PDF of the document", default=True)
    cc = models.CharField("Always CC", max_length=255, blank=True, help_text="Comma-separated addresses")

    class Meta:
        ordering = ["kind"]

    def __str__(self):
        return self.get_kind_display()

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("core:emailtemplate_update", args=[self.pk])


class EmailLog(models.Model):
    SENT, FAILED, NOT_CONFIGURED = "sent", "failed", "not_configured"
    STATUSES = [(SENT, "Sent"), (FAILED, "Failed"), (NOT_CONFIGURED, "Not sent — email not configured")]
    content_type = models.ForeignKey("contenttypes.ContentType", null=True, blank=True, on_delete=models.SET_NULL)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    document = models.CharField(max_length=60, blank=True)
    to = models.TextField()
    cc = models.TextField(blank=True)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    attachments = models.TextField(blank=True, help_text="File names, one per line")
    status = models.CharField(max_length=20, choices=STATUSES)
    error = models.TextField(blank=True)
    sent_by = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.subject} -> {self.to}"
