import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from core.crud import ErpCreateView, ErpDetailView, ErpListView, ErpUpdateView, run_action
from core.mixins import internal_required
from core.models import Company

from . import forms, services
from .models import Chargeback, CreditMemo, CustomerReturn, VendorCredit, VendorReturn


def _today(initial, *keys):
    for k in keys:
        initial.setdefault(k, datetime.date.today())
    return initial


def _decimal(value):
    try:
        return Decimal(str(value).replace(",", "")) if value not in (None, "") else None
    except InvalidOperation:
        return None


# ----------------------------------------------------------------------------- customer returns

class CustomerReturnList(ErpListView):
    model = CustomerReturn
    title = "Customer Returns (RMA)"
    create_url = reverse_lazy("returns:customerreturn_create")
    columns = (("RMA", "number"), ("Customer", "customer"), ("Date", "date"), ("Sales order", "sales_order"),
               ("Reason", "reason"), ("Customer RA", "customer_reference"), ("Value", "total"), ("Status", "status"))
    search_fields = ("number", "customer__name", "customer_reference", "sales_order__number")
    select_related = ("customer", "sales_order")
    filters = (("status", "Status", CustomerReturn.STATUSES),)

    def get_queryset(self):
        return super().get_queryset().prefetch_related("lines")


class CustomerReturnFormMixin:
    model = CustomerReturn
    form_class = forms.CustomerReturnForm
    formset_class = forms.CustomerReturnLineFormSet

    def get_initial(self):
        initial = super().get_initial()
        if not self.object:
            _today(initial, "date")
            from core.models import default_warehouse
            wh = default_warehouse()
            if wh:
                initial.setdefault("warehouse", wh.pk)
        return initial


class CustomerReturnCreate(CustomerReturnFormMixin, ErpCreateView):
    title = "New Customer Return (RMA)"


class CustomerReturnUpdate(CustomerReturnFormMixin, ErpUpdateView):
    pass


class CustomerReturnDetail(ErpDetailView):
    model = CustomerReturn
    template_name = "returns/customerreturn_detail.html"


@internal_required
@require_POST
def customerreturn_receive(request, pk):
    ret = get_object_or_404(CustomerReturn, pk=pk)
    run_action(request, ret, services.receive_customer_return, f"Return {ret.number} received into stock.", ret,
               user=request.user)
    return redirect(ret.get_absolute_url())


@internal_required
@require_POST
def customerreturn_credit(request, pk):
    ret = get_object_or_404(CustomerReturn, pk=pk)
    memo = run_action(request, ret, services.create_credit_memo_from_return, "Draft credit memo created.", ret)
    return redirect((memo or ret).get_absolute_url())


@internal_required
@require_POST
def customerreturn_cancel(request, pk):
    ret = get_object_or_404(CustomerReturn, pk=pk)
    if ret.status == CustomerReturn.DRAFT:
        ret.status = CustomerReturn.CANCELLED
        ret.save(update_fields=["status"])
        messages.success(request, f"Return {ret.number} cancelled.")
    else:
        messages.error(request, "Only draft returns can be cancelled.")
    return redirect(ret.get_absolute_url())


# ----------------------------------------------------------------------------- credit memos

class CreditMemoList(ErpListView):
    model = CreditMemo
    title = "Credit Memos"
    create_url = reverse_lazy("returns:creditmemo_create")
    columns = (("Credit memo", "number"), ("Customer", "customer"), ("Date", "date"), ("Invoice", "invoice"),
               ("Reason", "reason"), ("Total", "total"), ("Unapplied", "unapplied"), ("Status", "status"))
    search_fields = ("number", "customer__name", "invoice__number")
    select_related = ("customer", "invoice")
    filters = (("status", "Status", CreditMemo.STATUSES),)

    def get_queryset(self):
        return super().get_queryset().prefetch_related("lines")


class CreditMemoFormMixin:
    model = CreditMemo
    form_class = forms.CreditMemoForm
    formset_class = forms.CreditMemoLineFormSet

    def get_initial(self):
        initial = super().get_initial()
        if not self.object:
            _today(initial, "date")
        return initial


class CreditMemoCreate(CreditMemoFormMixin, ErpCreateView):
    title = "New Credit Memo"


class CreditMemoUpdate(CreditMemoFormMixin, ErpUpdateView):
    pass


class CreditMemoDetail(ErpDetailView):
    model = CreditMemo
    template_name = "returns/creditmemo_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.object.status in CreditMemo.OPEN_STATUSES:
            ctx["apply_form"] = forms.ApplyCreditForm(doc=self.object)
        return ctx


@internal_required
@require_POST
def creditmemo_post(request, pk):
    memo = get_object_or_404(CreditMemo, pk=pk)
    run_action(request, memo, services.post_credit_memo, f"Credit memo {memo.number} posted.", memo,
               user=request.user)
    return redirect(memo.get_absolute_url())


@internal_required
@require_POST
def creditmemo_apply(request, pk):
    memo = get_object_or_404(CreditMemo, pk=pk)
    if request.POST.get("auto"):
        run_action(request, memo, services.auto_apply_credit_memo, "Credit applied to open invoices.", memo)
        return redirect(memo.get_absolute_url())
    form = forms.ApplyCreditForm(request.POST, doc=memo)
    if form.is_valid():
        run_action(request, memo, services.apply_credit_memo, "Credit applied.", memo, form.cleaned_data["target"],
                   form.cleaned_data["amount"])
    else:
        messages.error(request, "Pick an invoice and an amount.")
    return redirect(memo.get_absolute_url())


@internal_required
@require_POST
def creditmemo_void(request, pk):
    memo = get_object_or_404(CreditMemo, pk=pk)
    if memo.status == CreditMemo.DRAFT:
        memo.status = CreditMemo.VOID
        memo.save(update_fields=["status"])
        messages.success(request, f"Credit memo {memo.number} voided.")
    else:
        messages.error(request, "Only draft credit memos can be voided.")
    return redirect(memo.get_absolute_url())


# ----------------------------------------------------------------------------- chargebacks

class ChargebackList(ErpListView):
    model = Chargeback
    title = "Chargebacks & Deductions"
    template_name = "returns/chargeback_list.html"
    create_url = reverse_lazy("returns:chargeback_create")
    columns = (("Chargeback", "number"), ("Customer", "customer"), ("Date", "date"), ("Type", "chargeback_type"),
               ("Deduction #", "deduction_reference"), ("Invoice", "invoice"), ("Amount", "amount"),
               ("Dispute by", "dispute_deadline"), ("Status", "status"))
    search_fields = ("number", "customer__name", "deduction_reference", "invoice__number")
    select_related = ("customer", "invoice")

    def get_filters(self):
        from sales.models import Customer

        return (("status", "Status", Chargeback.STATUSES), ("chargeback_type", "Type", Chargeback.TYPES),
                ("customer", "Customer", [(c.pk, c.name) for c in Customer.objects.filter(chargebacks__isnull=False)
                                          .distinct().order_by("name")]))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = Chargeback.objects.all()
        ctx["cb_summary"] = qs.aggregate(
            open_amt=Sum("amount", filter=Q(status__in=[Chargeback.OPEN, Chargeback.DISPUTED])),
            open_n=Count("id", filter=Q(status__in=[Chargeback.OPEN, Chargeback.DISPUTED])),
            accepted=Sum("accepted_amount", filter=Q(status=Chargeback.ACCEPTED)),
            won=Sum("amount", filter=Q(status=Chargeback.WON)),
            overdue=Count("id", filter=Q(status__in=[Chargeback.OPEN, Chargeback.DISPUTED],
                                         dispute_deadline__lt=datetime.date.today())),
        )
        return ctx


class ChargebackFormMixin:
    model = Chargeback
    form_class = forms.ChargebackForm

    def get_initial(self):
        initial = super().get_initial()
        if not self.object:
            _today(initial, "date")
            inv_id = self.request.GET.get("invoice")
            if inv_id:
                from accounting.models import CustomerInvoice

                inv = CustomerInvoice.objects.filter(pk=inv_id).first()
                if inv:
                    initial.update(customer=inv.customer_id, sales_order=inv.sales_order_id)
        return initial


class ChargebackCreate(ChargebackFormMixin, ErpCreateView):
    title = "Record Chargeback / Deduction"


class ChargebackUpdate(ChargebackFormMixin, ErpUpdateView):
    pass


class ChargebackDetail(ErpDetailView):
    model = Chargeback
    template_name = "returns/chargeback_detail.html"


@internal_required
@require_POST
def chargeback_action(request, pk, action):
    cb = get_object_or_404(Chargeback, pk=pk)
    notes = request.POST.get("notes", "").strip()
    if action == "accept":
        run_action(request, cb, services.accept_chargeback, f"Chargeback {cb.number} written off.", cb,
                   _decimal(request.POST.get("amount")), notes, user=request.user)
    elif action == "dispute":
        run_action(request, cb, services.dispute_chargeback, f"Chargeback {cb.number} marked disputed.", cb, notes)
    elif action == "won":
        run_action(request, cb, services.win_chargeback, f"Chargeback {cb.number} recovered.", cb, notes)
    elif action == "void":
        run_action(request, cb, services.void_chargeback, f"Chargeback {cb.number} voided.", cb)
    return redirect(cb.get_absolute_url())


@internal_required
def chargeback_report(request):
    """Deductions by customer and type for a period."""
    today = datetime.date.today()
    try:
        start = datetime.date.fromisoformat(request.GET.get("start", ""))
    except ValueError:
        start = today.replace(month=1, day=1)
    try:
        end = datetime.date.fromisoformat(request.GET.get("end", ""))
    except ValueError:
        end = today
    qs = Chargeback.objects.filter(date__range=(start, end)).exclude(status=Chargeback.VOID)
    open_q = Q(status__in=[Chargeback.OPEN, Chargeback.DISPUTED])
    agg = dict(n=Count("id"), total=Sum("amount"), open=Sum("amount", filter=open_q),
               accepted=Sum("accepted_amount", filter=Q(status=Chargeback.ACCEPTED)),
               won=Sum("amount", filter=Q(status=Chargeback.WON)))
    by_customer = qs.values("customer__name").annotate(**agg).order_by("-total")
    types = dict(Chargeback.TYPES)
    by_type = [dict(r, label=types.get(r["chargeback_type"], r["chargeback_type"]))
               for r in qs.values("chargeback_type").annotate(**agg).order_by("-total")]
    totals = qs.aggregate(**agg)
    return render(request, "returns/chargeback_report.html", {
        "start": start, "end": end, "by_customer": by_customer, "by_type": by_type, "totals": totals})


# ----------------------------------------------------------------------------- vendor returns

class VendorReturnList(ErpListView):
    model = VendorReturn
    title = "Returns to Vendor"
    create_url = reverse_lazy("returns:vendorreturn_create")
    columns = (("Return", "number"), ("Vendor", "vendor"), ("Date", "date"), ("PO", "purchase_order"),
               ("Reason", "reason"), ("Vendor RA", "vendor_rma"), ("Value", "total"), ("Status", "status"))
    cost_columns = ("total",)
    search_fields = ("number", "vendor__name", "vendor_rma", "purchase_order__number")
    select_related = ("vendor", "purchase_order")
    filters = (("status", "Status", VendorReturn.STATUSES),)

    def get_queryset(self):
        return super().get_queryset().prefetch_related("lines")


class VendorReturnFormMixin:
    model = VendorReturn
    form_class = forms.VendorReturnForm
    formset_class = forms.VendorReturnLineFormSet

    def get_initial(self):
        initial = super().get_initial()
        if not self.object:
            _today(initial, "date")
            from core.models import default_warehouse
            wh = default_warehouse()
            if wh:
                initial.setdefault("warehouse", wh.pk)
        return initial


class VendorReturnCreate(VendorReturnFormMixin, ErpCreateView):
    title = "New Return to Vendor"


class VendorReturnUpdate(VendorReturnFormMixin, ErpUpdateView):
    pass


class VendorReturnDetail(ErpDetailView):
    model = VendorReturn
    template_name = "returns/vendorreturn_detail.html"


@internal_required
@require_POST
def vendorreturn_ship(request, pk):
    vr = get_object_or_404(VendorReturn, pk=pk)
    run_action(request, vr, services.ship_vendor_return, f"Return {vr.number} shipped; stock relieved.", vr,
               user=request.user)
    return redirect(vr.get_absolute_url())


@internal_required
@require_POST
def vendorreturn_credit(request, pk):
    vr = get_object_or_404(VendorReturn, pk=pk)
    if "accounting.post" not in request.perms:
        messages.error(request, "Recording vendor credits needs the Post to ledger permission.")
        return redirect(vr.get_absolute_url())
    vc = run_action(request, vr, services.create_vendor_credit_from_return, "Draft vendor credit created.", vr)
    return redirect((vc or vr).get_absolute_url())


@internal_required
@require_POST
def vendorreturn_cancel(request, pk):
    vr = get_object_or_404(VendorReturn, pk=pk)
    if vr.status == VendorReturn.DRAFT:
        vr.status = VendorReturn.CANCELLED
        vr.save(update_fields=["status"])
        messages.success(request, f"Return {vr.number} cancelled.")
    else:
        messages.error(request, "Only draft returns can be cancelled.")
    return redirect(vr.get_absolute_url())


# ----------------------------------------------------------------------------- vendor credits

class VendorCreditList(ErpListView):
    model = VendorCredit
    title = "Vendor Credits"
    create_url = reverse_lazy("returns:vendorcredit_create")
    columns = (("Credit", "number"), ("Vendor", "vendor"), ("Vendor credit #", "vendor_credit_number"),
               ("Date", "date"), ("Currency", "currency"), ("Total", "total"), ("Unapplied", "unapplied"),
               ("Status", "status"))
    search_fields = ("number", "vendor__name", "vendor_credit_number")
    select_related = ("vendor", "currency")
    filters = (("status", "Status", VendorCredit.STATUSES),)

    def get_queryset(self):
        return super().get_queryset().prefetch_related("lines")


class VendorCreditFormMixin:
    model = VendorCredit
    form_class = forms.VendorCreditForm
    formset_class = forms.VendorCreditLineFormSet

    def get_initial(self):
        initial = super().get_initial()
        if not self.object:
            _today(initial, "date")
            initial.setdefault("currency", Company.get().base_currency_id)
        return initial


class VendorCreditCreate(VendorCreditFormMixin, ErpCreateView):
    title = "New Vendor Credit"


class VendorCreditUpdate(VendorCreditFormMixin, ErpUpdateView):
    pass


class VendorCreditDetail(ErpDetailView):
    model = VendorCredit
    template_name = "returns/vendorcredit_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.object.status in VendorCredit.OPEN_STATUSES:
            ctx["apply_form"] = forms.ApplyCreditForm(doc=self.object)
        ctx["base"] = Company.get().base_currency_id
        return ctx


@internal_required
@require_POST
def vendorcredit_post(request, pk):
    vc = get_object_or_404(VendorCredit, pk=pk)
    run_action(request, vc, services.post_vendor_credit, f"Vendor credit {vc.number} posted.", vc, user=request.user)
    return redirect(vc.get_absolute_url())


@internal_required
@require_POST
def vendorcredit_apply(request, pk):
    vc = get_object_or_404(VendorCredit, pk=pk)
    if request.POST.get("auto"):
        run_action(request, vc, services.auto_apply_vendor_credit, "Credit applied to open bills.", vc,
                   user=request.user)
        return redirect(vc.get_absolute_url())
    form = forms.ApplyCreditForm(request.POST, doc=vc)
    if form.is_valid():
        run_action(request, vc, services.apply_vendor_credit, "Credit applied.", vc, form.cleaned_data["target"],
                   form.cleaned_data["amount"], user=request.user)
    else:
        messages.error(request, "Pick a bill and an amount.")
    return redirect(vc.get_absolute_url())


@internal_required
@require_POST
def vendorcredit_void(request, pk):
    vc = get_object_or_404(VendorCredit, pk=pk)
    if vc.status == VendorCredit.DRAFT:
        vc.status = VendorCredit.VOID
        vc.save(update_fields=["status"])
        messages.success(request, f"Vendor credit {vc.number} voided.")
    else:
        messages.error(request, "Only draft vendor credits can be voided.")
    return redirect(vc.get_absolute_url())

