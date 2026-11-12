from django import forms

from core.forms import LineWarehouseForm, StyledModelForm, formfield_callback, line_formset, model_form

from .models import Customer, CustomerBuyer, SalesOrder, SalesOrderLine

CUSTOMER_FIELDS = ["code", "name", "contact_name", "email", "phone", "address", "city", "state", "postal_code",
                   "country", "shipping_address", "tax_id", "tax_exempt", "credit_limit", "payment_terms_days",
                   "price_list", "sales_rep", "notes", "is_active"]
CustomerForm = model_form(Customer, CUSTOMER_FIELDS)

ORDER_FIELDS = ["customer", "buyer", "order_date", "requested_date", "valid_until", "sales_rep", "warehouse",
                "customer_po", "tax_rate", "shipping_address", "notes"]

BuyerFormSet = line_formset(Customer, CustomerBuyer, ["name", "title", "email", "phone", "is_active"], extra=2)


class BuyerSelect(forms.Select):
    """Options carry data-customer so the page can show only the chosen customer's buyers."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        customer_id = getattr(getattr(value, "instance", None), "customer_id", None)
        if customer_id:
            option["attrs"]["data-customer"] = customer_id
        return option


class SalesOrderForm(StyledModelForm):
    formfield_callback = formfield_callback

    class Meta:
        model = SalesOrder
        fields = ORDER_FIELDS
        widgets = {"buyer": BuyerSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["buyer"].queryset = CustomerBuyer.objects.filter(is_active=True).select_related("customer") | \
            CustomerBuyer.objects.filter(pk=getattr(self.instance, "buyer_id", None))
        self.fields["buyer"].label_from_instance = lambda b: f"{b.name}" + (f" — {b.title}" if b.title else "")

    def clean(self):
        cleaned = super().clean()
        buyer, customer = cleaned.get("buyer"), cleaned.get("customer")
        if buyer and customer and buyer.customer_id != customer.pk:
            self.add_error("buyer", f"{buyer.name} is a buyer for {buyer.customer}, not {customer}.")
        return cleaned


class SOLineForm(LineWarehouseForm):
    class Meta:
        model = SalesOrderLine
        fields = ["product", "warehouse", "description", "quantity", "unit_price", "discount_pct"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = self.fields["product"].queryset.filter(is_active=True)


SOLineFormSet = line_formset(SalesOrder, SalesOrderLine, ["product", "warehouse", "description", "quantity",
                                                          "unit_price", "discount_pct"], extra=4, form=SOLineForm)
