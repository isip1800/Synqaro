from django import forms

from core.forms import StyledModelForm, line_formset

from .models import (
    HTSCode,
    ItemClass,
    LookupValue,
    Product,
    ProductCrossReference,
    ProductWarehouse,
    VendorProduct,
)

REQUIRED_DETAILS = ["item_class", "brand", "package_size", "season", "gender", "size_range", "material",
                    "customer_code"]

PRODUCT_SECTIONS = [
    ("General", ["sku", "name", "description", "item_class", "uom", "barcode", "is_active"]),
    ("Item details", ["brand", "package_size", "season", "seasonal_cutoff", "gender", "size_range",
                      "size_breakdown", "item_spec", "material", "customer_code", "label1", "label2",
                      "multi_pc_set"]),
    ("Customs / HTS", ["hs_code", "duty_rate", "tariff_rate", "hts_override", "country_of_origin"]),
    ("Packaging & container", ["prepack_qty", "carton_qty", "dimension_unit", "carton_length", "carton_width",
                               "carton_height", "carton_cbm", "carton_weight_kg", "weight_kg", "volume_cbm"]),
    ("Price / cost", ["list_price", "standard_cost", "monthly_carrying_cost"]),
    ("Replenishment (all warehouses)", ["preferred_vendor", "reorder_point", "reorder_qty", "lead_time_days"]),
]
PRODUCT_FIELDS = [f for _, fields in PRODUCT_SECTIONS for f in fields]


class ProductForm(StyledModelForm):
    class Meta:
        model = Product
        fields = PRODUCT_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in REQUIRED_DETAILS:
            self.fields[name].required = True
        for name, field in self.fields.items():
            if isinstance(field, forms.ModelChoiceField) and field.queryset.model is LookupValue:
                field.queryset = field.queryset.filter(is_active=True) | LookupValue.objects.filter(
                    pk=getattr(self.instance, f"{name}_id", None))
        self.fields["item_class"].queryset = ItemClass.objects.filter(level=3, is_active=True) \
            .select_related("parent__parent").order_by("parent__parent__code", "parent__code", "code")
        self.fields["hs_code"].help_text = "Filled from the HTS table (Item class + Material + Gender) unless " \
                                           "'Override HTS table' is ticked"
        self.fields["barcode"].label = "UPC / barcode"
        self.fields["barcode"].help_text = "Enter 11 digits to have the check digit added, or use Generate UPC"
        self.fields["volume_cbm"].widget.attrs["readonly"] = True

    def clean_barcode(self):
        from .upc import complete, is_valid

        code = complete(self.cleaned_data.get("barcode", ""))
        if code and not is_valid(code):
            raise forms.ValidationError("Invalid UPC/EAN check digit.")
        if code and Product.objects.filter(barcode=code).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This barcode is already used by another product.")
        return code


WarehouseFormSet = line_formset(Product, ProductWarehouse, ["warehouse", "bin_location", "reorder_point",
                                                            "reorder_qty", "max_qty", "is_default"], extra=1)
CrossRefFormSet = line_formset(Product, ProductCrossReference, ["alt_type", "vendor", "customer", "alternate_id",
                                                                "uom", "description"], extra=1)
VendorPriceFormSet = line_formset(Product, VendorProduct, ["vendor", "vendor_sku", "unit_cost", "currency", "moq",
                                                           "lead_time_days"], extra=1)


class LookupForm(StyledModelForm):
    class Meta:
        model = LookupValue
        fields = ["code", "description", "is_active"]


class ItemClassForm(StyledModelForm):
    class Meta:
        model = ItemClass
        fields = ["parent", "code", "description", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = ItemClass.objects.filter(level__lt=3).select_related("parent").order_by("level", "code")
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = qs
        self.fields["parent"].label_from_instance = lambda o: f"{o.full_code}  ({o.get_level_display()}: " \
                                                              f"{o.full_description})"
        self.fields["code"].help_text = "Short code, e.g. INT, UNDERWEAR, 10PKPANTIE"

    def clean(self):
        cleaned = super().clean()
        parent = cleaned.get("parent")
        if parent and parent.level >= 3:
            raise forms.ValidationError("A product type (Tier 3) cannot have children.")
        if self.instance.pk and parent and self.instance.children.exists() and parent.level >= 2:
            raise forms.ValidationError("This class has children; it cannot move below Tier 2.")
        return cleaned


class HTSCodeForm(StyledModelForm):
    class Meta:
        model = HTSCode
        fields = ["item_class", "material", "gender", "hts_code", "duty_rate", "tariff_rate", "notes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item_class"].queryset = ItemClass.objects.filter(level=3).select_related("parent__parent") \
            .order_by("parent__parent__code", "parent__code", "code")


class HTSImportForm(forms.Form):
    file = forms.FileField(help_text="An .xlsx with columns: Item Class, Material, Gender, HTS Code, Duty Rate, "
                                     "Tariff (same as the export)")
