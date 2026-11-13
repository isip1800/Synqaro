import datetime
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.urls import reverse_lazy
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.crud import ErpCreateView, ErpDetailView, ErpListView, ErpUpdateView, run_action
from core.mixins import internal_required
from core.models import Company, ExchangeRate
from core.utils import ZERO

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
    create_url = reverse_lazy("accounting:journalentry_create")
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
    create_url = reverse_lazy("accounting:customerinvoice_create")
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
    create_url = reverse_lazy("accounting:customerpayment_create")
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
    create_url = reverse_lazy("accounting:vendorbill_create")
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
    create_url = reverse_lazy("accounting:vendorpayment_create")
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


# ----------------------------------------------------------------------------- bank reconciliation

from . import banking, periods  # noqa: E402
from .models import BankReconciliation, PeriodClose  # noqa: E402


@internal_required
def bank_reconciliations(request):
    banks = []
    for acc in Account.objects.filter(is_bank=True, is_active=True).order_by("code"):
        last = banking.last_completed(acc)
        banks.append({
            "account": acc, "book_balance": acc.balance(), "last": last,
            "in_progress": BankReconciliation.objects.filter(account=acc,
                                                             status=BankReconciliation.IN_PROGRESS).first(),
            "uncleared": acc.lines.filter(reconciliation__isnull=True).count(),
        })
    history = BankReconciliation.objects.select_related("account", "completed_by")[:50]
    return render(request, "accounting/bankrec_list.html", {"banks": banks, "history": history,
                                                             "today": datetime.date.today()})


@internal_required
@require_POST
def bankrec_start(request):
    acc = get_object_or_404(Account, pk=request.POST.get("account"), is_bank=True)
    try:
        bal = Decimal(request.POST.get("statement_balance", "").replace(",", ""))
    except Exception:
        messages.error(request, "Enter the statement ending balance.")
        return redirect("accounting:bankreconciliation_list")
    rec = run_action(request, acc, banking.start_reconciliation, f"Reconciliation started for {acc}.", acc,
                     _date(request.POST.get("statement_date"), datetime.date.today()), bal, request.user)
    return redirect(rec.get_absolute_url() if rec else "accounting:bankreconciliation_list")


@internal_required
def bankrec_detail(request, pk):
    rec = get_object_or_404(BankReconciliation.objects.select_related("account"), pk=pk)
    ctx = {"object": rec, "editable": rec.status == BankReconciliation.IN_PROGRESS}
    if ctx["editable"]:
        lines = list(banking.candidate_lines(rec))
        ctx["deposits"] = [ln for ln in lines if ln.debit]
        ctx["payments"] = [ln for ln in lines if ln.credit]
        ctx["offset_accounts"] = Account.objects.filter(is_active=True, is_bank=False,
                                                        account_type__in=[Account.EXPENSE, Account.INCOME])
    else:
        ctx["cleared"] = rec.cleared_lines.select_related("entry", "customer", "vendor") \
            .order_by("entry__date", "id")
    out = list(banking.outstanding(rec)) if not ctx["editable"] else []
    ctx["out_deposits"] = [ln for ln in out if ln.debit]
    ctx["out_payments"] = [ln for ln in out if ln.credit]
    ctx["out_dep_total"] = sum((ln.debit for ln in ctx["out_deposits"]), ZERO)
    ctx["out_pay_total"] = sum((ln.credit for ln in ctx["out_payments"]), ZERO)
    ctx["book_balance"] = rec.account.balance(end=rec.statement_date)
    ctx["proof"] = rec.statement_balance + ctx["out_dep_total"] - ctx["out_pay_total"]
    dr, cr = rec.cleared_totals
    ctx.update(cleared_dr=dr, cleared_cr=cr)
    return render(request, "accounting/bankrec_detail.html", ctx)


@internal_required
@require_POST
def bankrec_save(request, pk):
    rec = get_object_or_404(BankReconciliation, pk=pk)
    ok = run_action(request, rec, banking.set_cleared, "Saved.", rec, request.POST.getlist("cleared"))
    if ok and request.POST.get("finish"):
        run_action(request, rec, banking.complete_reconciliation, f"{rec.number} completed.", rec, request.user)
    return redirect(rec.get_absolute_url())


@internal_required
@require_POST
def bankrec_import(request, pk):
    rec = get_object_or_404(BankReconciliation, pk=pk)
    f = request.FILES.get("statement")
    if not f:
        messages.error(request, "Choose a CSV file exported from your bank.")
        return redirect(rec.get_absolute_url())
    try:
        matched, unmatched = banking.import_statement(rec, f.read())
        messages.success(request, f"Matched and cleared {matched} item{'s' if matched != 1 else ''}; "
                                  f"{len(unmatched)} statement line{'s' if len(unmatched) != 1 else ''} not found "
                                  "in the books.")
    except ValidationError as exc:
        for m in exc.messages:
            messages.error(request, m)
    return redirect(rec.get_absolute_url())


@internal_required
@require_POST
def bankrec_item(request, pk):
    rec = get_object_or_404(BankReconciliation, pk=pk)
    acc = Account.objects.filter(pk=request.POST.get("account")).first()
    try:
        amount = Decimal(request.POST.get("amount", "").replace(",", ""))
    except Exception:
        amount = None
    if not acc or amount is None:
        messages.error(request, "Pick an account and enter an amount.")
        return redirect(rec.get_absolute_url())
    if request.POST.get("direction") == "out":
        amount = -abs(amount)
    else:
        amount = abs(amount)
    run_action(request, rec, banking.record_bank_item, "Bank item recorded and cleared.", rec, acc, amount,
               request.POST.get("memo", "").strip(), request.user)
    return redirect(rec.get_absolute_url())


@internal_required
@require_POST
def bankrec_undo(request, pk):
    rec = get_object_or_404(BankReconciliation, pk=pk)
    run_action(request, rec, banking.undo_reconciliation, f"{rec.number} reopened.", rec, request.user)
    return redirect(rec.get_absolute_url())


@internal_required
@require_POST
def bankrec_delete(request, pk):
    rec = get_object_or_404(BankReconciliation, pk=pk)
    if run_action(request, rec, lambda: banking.delete_reconciliation(rec) or True, f"{rec.number} deleted."):
        return redirect("accounting:bankreconciliation_list")
    return redirect(rec.get_absolute_url())


# ----------------------------------------------------------------------------- month-end close

@internal_required
def period_close(request):
    today = datetime.date.today()
    locked = periods.lock_date()
    default_end = periods.month_end(locked + datetime.timedelta(days=1)) if locked else periods.prev_month_end(today)
    if default_end >= today:
        default_end = periods.prev_month_end(today)
    period_end = periods.month_end(_date(request.GET.get("period"), default_end))
    months = []
    d = periods.prev_month_end(today)
    for _ in range(12):
        months.append({"end": d, "closed": bool(locked and d <= locked)})
        d = periods.prev_month_end(d)
    items = periods.checklist(period_end)
    return render(request, "accounting/period_close.html", {
        "locked": locked, "period_end": period_end, "months": months, "items": items,
        "open_count": sum(1 for i in items if i["severity"] == "warn"),
        "has_error": any(i["severity"] == "error" for i in items),
        "can_close": not (locked and period_end <= locked) and period_end < today,
        "log": PeriodClose.objects.select_related("user")[:20],
    })


@internal_required
@require_POST
def period_close_action(request):
    end = _date(request.POST.get("period"), None)
    if not end:
        messages.error(request, "Pick a month.")
        return redirect("accounting:period_close")
    run_action(request, None, periods.close_period, f"Books closed through {periods.month_end(end):%Y-%m-%d}.", end,
               request.user, request.POST.get("notes", ""), bool(request.POST.get("force")))
    return redirect("accounting:period_close")


@internal_required
@require_POST
def period_reopen(request):
    run_action(request, None, periods.reopen_period, "Period reopened.", request.user,
               request.POST.get("notes", ""))
    return redirect("accounting:period_close")
