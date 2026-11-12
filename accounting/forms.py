from core.forms import StyledModelForm, line_formset, model_form

from .models import (
    Account,
    CustomerInvoice,
    CustomerInvoiceLine,
    CustomerPayment,
    CustomerPaymentAllocation,
    JournalEntry,
    JournalLine,
    VendorBill,
    VendorBillLine,
    VendorPayment,
    VendorPaymentAllocation,
)

AccountForm = model_form(Account, ["code", "name", "account_type", "is_bank", "description", "is_active"])
JournalEntryForm = model_form(JournalEntry, ["date", "memo", "reference"])
JournalLineFormSet = line_formset(JournalEntry, JournalLine, ["account", "description", "debit", "credit",
                                                              "customer", "vendor"], extra=4)


class InvoiceLineForm(StyledModelForm):
    class Meta:
        model = CustomerInvoiceLine
        fields = ["description", "account", "quantity", "unit_price", "discount_pct"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(account_type=Account.INCOME, is_active=True)


InvoiceForm = model_form(CustomerInvoice, ["customer", "invoice_date", "due_date", "sales_rep", "tax_rate", "notes"])
InvoiceLineFormSet = line_formset(CustomerInvoice, CustomerInvoiceLine,
                                  ["description", "account", "quantity", "unit_price", "discount_pct"], extra=3,
                                  form=InvoiceLineForm)


class OpenInvoiceAllocForm(StyledModelForm):
    class Meta:
        model = CustomerPaymentAllocation
        fields = ["invoice", "amount"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = CustomerInvoice.objects.filter(status__in=CustomerInvoice.OPEN_STATUSES).select_related("customer")
        if self.instance.pk:
            qs = qs | CustomerInvoice.objects.filter(pk=self.instance.invoice_id)
        self.fields["invoice"].queryset = qs
        self.fields["invoice"].label_from_instance = lambda i: f"{i.number} — {i.customer} (due {i.balance_due:,.2f})"


CustomerPaymentForm = model_form(CustomerPayment, ["customer", "date", "amount", "method", "reference",
                                                   "deposit_account", "notes"])
CustomerAllocFormSet = line_formset(CustomerPayment, CustomerPaymentAllocation, ["invoice", "amount"], extra=3,
                                    form=OpenInvoiceAllocForm)

BillForm = model_form(VendorBill, ["vendor", "vendor_invoice_number", "bill_date", "due_date", "currency",
                                   "exchange_rate", "purchase_order", "shipment", "notes"])


class BillLineForm(StyledModelForm):
    class Meta:
        model = VendorBillLine
        fields = ["po_line", "description", "account", "quantity", "unit_price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from purchasing.models import PurchaseOrder, PurchaseOrderLine

        qs = PurchaseOrderLine.objects.filter(
            order__status__in=[PurchaseOrder.SENT, PurchaseOrder.CONFIRMED, PurchaseOrder.PARTIAL,
                               PurchaseOrder.RECEIVED]
        ).select_related("order", "product")
        if self.instance.pk and self.instance.po_line_id:
            qs = qs | PurchaseOrderLine.objects.filter(pk=self.instance.po_line_id)
        self.fields["po_line"].queryset = qs.distinct()
        self.fields["po_line"].required = False
        self.fields["account"].queryset = Account.objects.filter(is_active=True)


BillLineFormSet = line_formset(VendorBill, VendorBillLine, ["po_line", "description", "account", "quantity",
                                                            "unit_price"], extra=3, form=BillLineForm)


class OpenBillAllocForm(StyledModelForm):
    class Meta:
        model = VendorPaymentAllocation
        fields = ["bill", "amount"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = VendorBill.objects.filter(status__in=VendorBill.OPEN_STATUSES).select_related("vendor", "currency")
        if self.instance.pk:
            qs = qs | VendorBill.objects.filter(pk=self.instance.bill_id)
        self.fields["bill"].queryset = qs
        self.fields["bill"].label_from_instance = (
            lambda b: f"{b.number} — {b.vendor} #{b.vendor_invoice_number} ({b.balance_due:,.2f} {b.currency_id})"
        )


VendorPaymentForm = model_form(VendorPayment, ["vendor", "date", "currency", "exchange_rate", "amount", "method",
                                               "reference", "paid_from", "notes"])
VendorAllocFormSet = line_formset(VendorPayment, VendorPaymentAllocation, ["bill", "amount"], extra=3,
                                  form=OpenBillAllocForm)
