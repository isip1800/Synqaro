from core.forms import LineWarehouseForm, line_formset, model_form

from .models import Customer, SalesOrder, SalesOrderLine

CUSTOMER_FIELDS = ["code", "name", "contact_name", "email", "phone", "address", "city", "state", "postal_code",
                   "country", "shipping_address", "tax_id", "tax_exempt", "credit_limit", "payment_terms_days",
                   "price_list", "sales_rep", "notes", "is_active"]
CustomerForm = model_form(Customer, CUSTOMER_FIELDS)

ORDER_FIELDS = ["customer", "order_date", "requested_date", "valid_until", "sales_rep", "warehouse", "customer_po",
                "tax_rate", "shipping_address", "notes"]
SalesOrderForm = model_form(SalesOrder, ORDER_FIELDS)


class SOLineForm(LineWarehouseForm):
    class Meta:
        model = SalesOrderLine
        fields = ["product", "warehouse", "description", "quantity", "unit_price", "discount_pct"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = self.fields["product"].queryset.filter(is_active=True)


SOLineFormSet = line_formset(SalesOrder, SalesOrderLine, ["product", "warehouse", "description", "quantity",
                                                          "unit_price", "discount_pct"], extra=4, form=SOLineForm)
