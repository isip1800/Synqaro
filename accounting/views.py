import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.crud import ErpCreateView, ErpDetailView, ErpListView, ErpUpdateView, run_action
from core.mixins import internal_required
from core.models import Company, ExchangeRate

from . import services
from .forms import (
    AccountForm,
    BillForm,
    BillLineFormSet,
    CustomerAllocFormSet,
    CustomerPaymentForm,
    InvoiceForm,
    InvoiceLineFormSet,
    JournalEntryForm,
    JournalLineFormSet,
    VendorAllocFormSet,
    VendorPaymentForm,
)
from .models import (
    Account,
    CustomerInvoice,
    CustomerPayment,
    JournalEntry,
    VendorBill,
    VendorPayment,
)


def _date(value, default):
    try:
        return datetime.date.fromisoformat(value) if value else default
    except ValueError:
        return default


# ----------------------------------------------------------------------------- Chart of accounts / journal

class AccountList(ErpListView):
    model = Account
    title = "Chart of Accounts"
    template_name = "accounting/account_list.html"
    search_fields = ("code", "name")
    paginate_by = 200

    def get_filters(self):
        return (("account_type", "Type", Account.TYPES),)


class AccountCreate(ErpCreateView):
    model = Account
    form_class = AccountForm


class AccountUpdate(ErpUpdateView):
    model = Account
    form_class = AccountForm


@internal_required
def account_detail(request, pk):
    account = get_object_or_404(Account, pk=pk)
    today = datetime.date.today()
    start = _date(request.GET.get("start"), today.replace(month=1, day=1))
    end = _date(request.GET.get("end"), today)
    ledger = services.account_ledger(account, start, end)
    return render(request, "accounting/account_detail.html", {
        "object": account, "ledger": ledger, "start": start, "end": end, "title": str(account),
    })


class JournalList(ErpListView):
    model = JournalEntry
    title = "Journal Entries"
    columns = (("Entry", "number"), ("Date", "date"), ("Source", "source"), ("Reference", "reference"),
               ("Memo", "memo"), ("Amount", "total_debit"))
    search_fields = ("number", "memo", "reference")

    def get_filters(self):
        return (("source", "Source", JournalEntry.SOURCES),)


class JournalCreate(ErpCreateView):
    model = JournalEntry
    form_class = JournalEntryForm
    formset_class = JournalLineFormSet
    title = "New Manual Journal Entry"

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault("date", datetime.date.today())
        return initial

    def after_save(self, form):
        services.validate_manual_entry(self.object)


class JournalDetail(ErpDetailView):
    model = JournalEntry
    template_name = "accounting/journal_detail.html"


@internal_required
@require_POST
def journal_reverse(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk)

    def reverse():
        lines = [{"account": ln.account, "debit": ln.credit, "credit": ln.debit, "customer": ln.customer,
                  "vendor": ln.vendor, "description": f"Reversal: {ln.description}"} for ln in entry.lines.all()]
        return services.post_journal(_date(request.POST.get("date"), datetime.date.today()),
                                     f"Reversal of {entry.number}: {entry.memo}", lines, "manual", entry.number,
                                     request.user)

    if entry.source != "manual":
        messages.error(request, "Only manual entries can be reversed here; reverse the source document instead.")
        return redirect(entry.get_absolute_url())
    new = run_action(request, entry, reverse, f"Entry {entry.number} reversed.")
    return redirect((new or entry).get_absolute_url())


# ----------------------------------------------------------------------------- Receivables

class InvoiceList(ErpListView):
    model = CustomerInvoice
    title = "Customer Invoices (Accounts Receivable)"
    columns = (("Invoice", "number"), ("Customer", "customer"), ("Date", "invoice_date"), ("Due", "due_date"),
               ("Total", "total"), ("Balance", "balance_due"), ("Status", "status"))
    search_fields = ("number", "customer__name", "sales_order__number")
    select_related = ("customer",)

    def get_queryset(self):
        return super().get_queryset().prefetch_related("lines")

    def get_filters(self):
        return (("status", "Status", CustomerInvoice.STATUSES),)


class InvoiceFormMixin:
    model = CustomerInvoice
    form_class = InvoiceForm
    formset_class = InvoiceLineFormSet

    def get_initial(self):
        initial = super().get_initial()
        if not self.object:
            initial.setdefault("invoice_date", datetime.date.today())
            initial.setdefault("tax_rate", Company.get().default_sales_tax_rate)
        return initial


class InvoiceCreate(InvoiceFormMixin, ErpCreateView):
    title = "New Customer Invoice"


class InvoiceUpdate(InvoiceFormMixin, ErpUpdateView):
    pass


class InvoiceDetail(ErpDetailView):
    model = CustomerInvoice
    template_name = "accounting/invoice_detail.html"


class CustomerPaymentList(ErpListView):
    model = CustomerPayment
    title = "Customer Payments"
    columns = (("Receipt", "number"), ("Customer", "customer"), ("Date", "date"), ("Method", "method"),
               ("Reference", "reference"), ("Amount", "amount"), ("Status", "status"))
    search_fields = ("number", "customer__name", "reference")
    select_related = ("customer",)


class CustomerPaymentFormMixin:
    model = CustomerPayment
    form_class = CustomerPaymentForm
    formset_class = CustomerAllocFormSet

    def get_initial(self):
        initial = super().get_initial()
        if not self.object:
            initial.setdefault("date", datetime.date.today())
            initial.setdefault("deposit_account", services.acct("CASH").pk)
        return initial


class CustomerPaymentCreate(CustomerPaymentFormMixin, ErpCreateView):
    title = "Record Customer Payment"


class CustomerPaymentUpdate(CustomerPaymentFormMixin, ErpUpdateView):
    pass


class CustomerPaymentDetail(ErpDetailView):
    model = CustomerPayment
    template_name = "accounting/customer_payment_detail.html"


# ----------------------------------------------------------------------------- Payables

class BillList(ErpListView):
    model = VendorBill
    title = "Vendor Bills (Accounts Payable)"
    columns = (("Bill", "number"), ("Vendor", "vendor"), ("Vendor inv #", "vendor_invoice_number"),
               ("Date", "bill_date"), ("Due", "due_date"), ("Currency", "currency"), ("Total", "total"),
               ("Balance", "balance_due"), ("Status", "status"))
    search_fields = ("number", "vendor__name", "vendor_invoice_number")
    select_related = ("vendor",)

    def get_queryset(self):
        return super().get_queryset().prefetch_related("lines")

    def get_filters(self):
        return (("status", "Status", VendorBill.STATUSES),
                ("submitted_via_portal", "Source", [("True", "Vendor portal"), ("False", "Internal")]))


def _fill_rate(doc, date):
    if doc.currency_id != Company.get().base_currency_id and doc.exchange_rate == 1:
        doc.exchange_rate = ExchangeRate.get_rate(doc.currency_id, date)


class BillFormMixin:
    model = VendorBill
    form_class = BillForm
    formset_class = BillLineFormSet

    def get_initial(self):
        initial = super().get_initial()
        if not self.object:
            initial.setdefault("bill_date", datetime.date.today())
            initial.setdefault("currency", Company.get().base_currency_id)
        return initial

    def before_save(self, form):
        super().before_save(form)
        _fill_rate(form.instance, form.instance.bill_date)


class BillCreate(BillFormMixin, ErpCreateView):
    title = "New Vendor Bill"


class BillUpdate(BillFormMixin, ErpUpdateView):
    pass


class BillDetail(ErpDetailView):
    model = VendorBill
    template_name = "accounting/bill_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["base"] = Company.get().base_currency_id
        return ctx


class VendorPaymentList(ErpListView):
    model = VendorPayment
    title = "Vendor Payments"
    columns = (("Payment", "number"), ("Vendor", "vendor"), ("Date", "date"), ("Method", "method"),
               ("Currency", "currency"), ("Amount", "amount"), ("Status", "status"))
    search_fields = ("number", "vendor__name", "reference")
    select_related = ("vendor",)


class VendorPaymentFormMixin:
    model = VendorPayment
    form_class = VendorPaymentForm
    formset_class = VendorAllocFormSet

    def get_initial(self):
        initial = super().get_initial()
        if not self.object:
            initial.setdefault("date", datetime.date.today())
            initial.setdefault("paid_from", services.acct("CASH").pk)
            initial.setdefault("currency", Company.get().base_currency_id)
        return initial

    def before_save(self, form):
        super().before_save(form)
        _fill_rate(form.instance, form.instance.date)


class VendorPaymentCreate(VendorPaymentFormMixin, ErpCreateView):
    title = "Record Vendor Payment"


class VendorPaymentUpdate(VendorPaymentFormMixin, ErpUpdateView):
    pass


class VendorPaymentDetail(ErpDetailView):
    model = VendorPayment
    template_name = "accounting/vendor_payment_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["base"] = Company.get().base_currency_id
        return ctx


def _simple_action(model, func, success):
    @internal_required
    @require_POST
    def view(request, pk):
        obj = get_object_or_404(model, pk=pk)
        run_action(request, obj, func, success, obj)
        return redirect(obj.get_absolute_url())

    return view


auto_allocate_customer = _simple_action(CustomerPayment, services.auto_allocate_customer_payment,
                                        "Allocated to oldest open invoices.")
auto_allocate_vendor = _simple_action(VendorPayment, services.auto_allocate_vendor_payment,
                                      "Allocated to oldest open bills.")


@internal_required
@require_POST
def void_draft(request, kind, pk):
    model = {"invoice": CustomerInvoice, "bill": VendorBill}[kind]
    obj = get_object_or_404(model, pk=pk)
    if obj.status != model.DRAFT:
        messages.error(request, "Only drafts can be voided. Issue a credit note / reversing entry instead.")
    else:
        obj.status = model.VOID
        obj.save(update_fields=["status"])
        messages.success(request, f"{obj.number} voided.")
    return redirect(obj.get_absolute_url())


# ----------------------------------------------------------------------------- Reports

@internal_required
def reports(request):
    return render(request, "accounting/reports.html", {"title": "Financial Reports"})


@internal_required
def trial_balance(request):
    as_of = _date(request.GET.get("as_of"), datetime.date.today())
    return render(request, "accounting/trial_balance.html", {"tb": services.trial_balance(as_of), "as_of": as_of,
                                                             "title": "Trial Balance"})


@internal_required
def income_statement(request):
    today = datetime.date.today()
    start = _date(request.GET.get("start"), today.replace(month=1, day=1))
    end = _date(request.GET.get("end"), today)
    return render(request, "accounting/income_statement.html", {
        "r": services.income_statement(start, end), "start": start, "end": end, "title": "Profit & Loss"})


@internal_required
def balance_sheet(request):
    as_of = _date(request.GET.get("as_of"), datetime.date.today())
    return render(request, "accounting/balance_sheet.html", {"r": services.balance_sheet(as_of), "as_of": as_of,
                                                             "title": "Balance Sheet"})


@internal_required
def ar_aging(request):
    as_of = _date(request.GET.get("as_of"), datetime.date.today())
    return render(request, "accounting/aging.html", {"r": services.ar_aging(as_of), "as_of": as_of,
                                                     "title": "Accounts Receivable Aging", "kind": "ar"})


@internal_required
def ap_aging(request):
    as_of = _date(request.GET.get("as_of"), datetime.date.today())
    return render(request, "accounting/aging.html", {"r": services.ap_aging(as_of), "as_of": as_of,
                                                     "title": "Accounts Payable Aging (base currency)", "kind": "ap"})


__all__ = ["ValidationError"]
