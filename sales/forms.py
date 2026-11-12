from django import forms

from core.forms import LineWarehouseForm, StyledModelForm, formfield_callback, line_formset, model_form

from .models import Customer, CustomerBuyer, CustomerLocation, SalesOrder, SalesOrderLine

CUSTOMER_FIELDS = ["code", "name", "contact_name", "email", "phone", "address", "city", "state", "postal_code",
                   "country", "shipping_address", "tax_id", "tax_exempt", "credit_limit", "payment_terms_days",
                   "price_list", "sales_rep", "notes", "is_active"]
CustomerForm = model_form(Customer, CUSTOMER_FIELDS)

ORDER_FIELDS = ["customer", "ship_to", "buyer", "customer_po", "master_customer_po", "order_date", "ship_start_date",
                "cancel_date", "requested_date", "valid_until", "sales_rep", "warehouse", "tax_rate", "notes"]

BuyerFormSet = line_formset(Customer, CustomerBuyer, ["name", "title", "email", "phone", "is_active"], extra=2)


class BuyerSelect(forms.Select):
    """Options carry data-customer (and data-default) so the page shows only the chosen customer's entries."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, "instance", None)
        customer_id = getattr(instance, "customer_id", None)
        if customer_id:
            option["attrs"]["data-customer"] = customer_id
        if getattr(instance, "is_default", False):
            option["attrs"]["data-default"] = "1"
        return option


class SalesOrderForm(StyledModelForm):
    formfield_callback = formfield_callback

    class Meta:
        model = SalesOrder
        fields = ORDER_FIELDS
        widgets = {"buyer": BuyerSelect, "ship_to": BuyerSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ship_to"].required = True
        self.fields["ship_to"].queryset = CustomerLocation.objects.filter(is_active=True).select_related("customer") \
            | CustomerLocation.objects.filter(pk=getattr(self.instance, "ship_to_id", None))
        self.fields["ship_to"].help_text = "Locations are set up on the customer (Customers → Locations)"
        self.fields["buyer"].queryset = CustomerBuyer.objects.filter(is_active=True).select_related("customer") | \
            CustomerBuyer.objects.filter(pk=getattr(self.instance, "buyer_id", None))
        self.fields["buyer"].label_from_instance = lambda b: f"{b.name}" + (f" — {b.title}" if b.title else "")

    def clean(self):
        cleaned = super().clean()
        buyer, customer = cleaned.get("buyer"), cleaned.get("customer")
        if buyer and customer and buyer.customer_id != customer.pk:
            self.add_error("buyer", f"{buyer.name} is a buyer for {buyer.customer}, not {customer}.")
        ship_to = cleaned.get("ship_to")
        if ship_to and customer and ship_to.customer_id != customer.pk:
            self.add_error("ship_to", f"{ship_to} is a location of {ship_to.customer}, not {customer}.")
        start, cancel = cleaned.get("ship_start_date"), cleaned.get("cancel_date")
        if start and cancel and cancel < start:
            self.add_error("cancel_date", "Cancel date is before the start ship date.")
        if self.instance.pk and self.instance.lines.filter(qty_shipped__gt=0).exists() and customer and \
                customer.pk != self.instance.customer_id:
            self.add_error("customer", "Goods have shipped on this order; the customer cannot change.")
        return cleaned


class SOLineForm(LineWarehouseForm):
    class Meta:
        model = SalesOrderLine
        fields = ["product", "warehouse", "description", "quantity", "unit_price", "discount_pct"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = self.fields["product"].queryset.filter(is_active=True)


class ProtectShippedLines(forms.BaseInlineFormSet):
    """Lines with shipped / invoiced quantity cannot be removed, re-pointed or reduced below what shipped."""

    def clean(self):
        super().clean()
        for form in self.forms:
            line = form.instance
            if not line.pk or not getattr(form, "cleaned_data", None):
                continue
            if line.qty_shipped <= 0 and line.qty_invoiced <= 0:
                continue
            cd = form.cleaned_data
            if cd.get("DELETE"):
                raise forms.ValidationError(f"{line.product.sku}: {line.qty_shipped.normalize():f} already shipped — the line "
                                            f"cannot be deleted.")
            if cd.get("product") and cd["product"].pk != line.product_id:
                form.add_error("product", "Already shipped — the item cannot change.")
            if cd.get("warehouse") and cd["warehouse"].pk != line.warehouse_id:
                form.add_error("warehouse", "Already shipped — the warehouse cannot change.")
            floor = max(line.qty_shipped, line.qty_invoiced)
            if cd.get("quantity") is not None and cd["quantity"] < floor:
                form.add_error("quantity", f"Cannot be less than the {floor.normalize():f} already shipped / invoiced.")


SOLineFormSet = forms.inlineformset_factory(
    SalesOrder, SalesOrderLine, form=SOLineForm, formset=ProtectShippedLines,
    fields=["product", "warehouse", "description", "quantity", "unit_price", "discount_pct"], extra=4,
    can_delete=True, formfield_callback=formfield_callback)
