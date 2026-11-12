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

    class Meta:
        verbose_name_plural = "company"

    def __str__(self):
        return self.name

    @classmethod
    def get(cls):
        Currency.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "symbol": "$"})
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


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
