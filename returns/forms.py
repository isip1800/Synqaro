from django import forms

from accounting.models import Account, CustomerInvoice, VendorBill
from core.forms import StyledModelForm, line_formset, model_form

from .models import (
    Chargeback, CreditMemo, CreditMemoLine, CustomerReturn, CustomerReturnLine, VendorCredit, VendorCreditLine,
    VendorReturn, VendorReturnLine,
)

CustomerReturnForm = model_form(CustomerReturn, ["customer", "sales_order", "invoice", "warehouse", "date", "reason",
                                                 "customer_reference", "notes"])
CustomerReturnLineFormSet = line_formset(CustomerReturn, CustomerReturnLine,
                                         ["product", "quantity", "unit_price", "disposition"], extra=3)


class CreditMemoLineForm(StyledModelForm):
    class Meta:
        model = CreditMemoLine
        fields = ["product", "description", "account", "quantity", "unit_price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(is_active=True,
                                                                 account_type__in=[Account.INCOME, Account.EXPENSE])


CreditMemoForm = model_form(CreditMemo, ["customer", "invoice", "sales_rep", "date", "reason", "tax_rate", "notes"])
CreditMemoLineFormSet = line_formset(CreditMemo, CreditMemoLine,
                                     ["product", "description", "account", "quantity", "unit_price"], extra=3,
                                     form=CreditMemoLineForm)

ChargebackForm = model_form(Chargeback, ["customer", "invoice", "sales_order", "date", "deduction_reference",
                                         "chargeback_type", "amount", "dispute_deadline", "description"])

VendorReturnForm = model_form(VendorReturn, ["vendor", "purchase_order", "warehouse", "date", "reason", "vendor_rma",
                                             "notes"])
VendorReturnLineFormSet = line_formset(VendorReturn, VendorReturnLine, ["product", "quantity", "unit_cost"], extra=3)

VendorCreditForm = model_form(VendorCredit, ["vendor", "vendor_credit_number", "date", "currency", "exchange_rate",
                                             "notes"])
VendorCreditLineFormSet = line_formset(VendorCredit, VendorCreditLine,
                                       ["description", "account", "quantity", "unit_price"], extra=3)


class ApplyCreditForm(forms.Form):
    target = forms.ModelChoiceField(queryset=CustomerInvoice.objects.none(), label="Invoice")
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0.01)

    def __init__(self, *args, doc=None, **kwargs):
        super().__init__(*args, **kwargs)
        if isinstance(doc, CreditMemo):
            self.fields["target"].queryset = CustomerInvoice.objects.filter(
                customer=doc.customer, status__in=CustomerInvoice.OPEN_STATUSES).order_by("due_date")
        else:
            self.fields["target"].label = "Bill"
            self.fields["target"].queryset = VendorBill.objects.filter(
                vendor=doc.vendor, currency=doc.currency, status__in=VendorBill.OPEN_STATUSES).order_by("due_date")
        self.fields["target"].label_from_instance = lambda o: f"{o.number} — balance {o.balance_due:,.2f}"
