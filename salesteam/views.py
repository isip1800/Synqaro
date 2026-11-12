import calendar
import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounting.models import Account
from core.crud import ErpDetailView, ErpListView
from core.mixins import internal_required
from core.utils import ZERO

from . import services
from .models import Commission, SalesRep


def month_bounds(day):
    start = day.replace(day=1)
    end = day.replace(day=calendar.monthrange(day.year, day.month)[1])
    return start, end


class RepList(ErpListView):
    model = SalesRep
    title = "Sales People"
    columns = (("Code", "code"), ("Name", "name"), ("Territory", "territory"), ("Manager", "manager"),
               ("Commission %", "commission_rate"), ("Basis", "commission_basis"),
               ("Monthly target", "monthly_target"), ("Active", "is_active"))
    search_fields = ("code", "name", "territory")
    select_related = ("manager",)


class RepDetail(ErpDetailView):
    model = SalesRep
    template_name = "salesteam/rep_detail.html"

    def get_context_data(self, **kwargs):
        from accounting.models import CustomerInvoice
        from sales.models import SalesOrder

        ctx = super().get_context_data(**kwargs)
        rep = self.object
        today = datetime.date.today()
        m_start, m_end = month_bounds(today)
        y_start = today.replace(month=1, day=1)
        mtd = rep.sales_between(m_start, m_end)
        ytd = rep.sales_between(y_start, today)
        history = []
        for i in range(5, -1, -1):
            y, m = today.year, today.month - i
            while m <= 0:
                m += 12
                y -= 1
            s, e = month_bounds(datetime.date(y, m, 1))
            rev, margin, n = rep.sales_between(s, e)
            pct = (rev / rep.monthly_target * 100) if rep.monthly_target else None
            history.append({"label": s.strftime("%b %Y"), "revenue": rev, "margin": margin, "count": n, "pct": pct,
                            "bar": min(pct or 0, 100)})
        ctx.update(
            mtd_revenue=mtd[0], mtd_margin=mtd[1], ytd_revenue=ytd[0], ytd_margin=ytd[1],
            attainment=(mtd[0] / rep.monthly_target * 100) if rep.monthly_target else None,
            history=history,
            customers=rep.customers.all(),
            open_orders=rep.sales_orders.filter(status__in=SalesOrder.OPEN_STATUSES + (SalesOrder.QUOTE,))
            .select_related("customer").prefetch_related("lines"),
            open_invoices=CustomerInvoice.objects.filter(sales_rep=rep, status__in=CustomerInvoice.OPEN_STATUSES)
            .select_related("customer").prefetch_related("lines"),
            commissions=rep.commissions.select_related("invoice")[:50],
            unpaid_commissions=rep.commissions.exclude(status=Commission.PAID).select_related("invoice"),
            payouts=rep.payouts.all()[:10],
            bank_accounts=Account.objects.filter(is_bank=True, is_active=True),
            today=today,
        )
        return ctx


@internal_required
def performance(request):
    today = datetime.date.today()
    try:
        month = datetime.date.fromisoformat(request.GET["month"] + "-01") if request.GET.get("month") else today
    except ValueError:
        month = today
    start, end = month_bounds(month)
    rows = []
    totals = {"revenue": ZERO, "margin": ZERO, "target": ZERO, "commission": ZERO}
    for rep in SalesRep.objects.filter(is_active=True):
        revenue, margin, count = rep.sales_between(start, end)
        commission = rep.commissions.filter(date__gte=start, date__lte=end).aggregate(t=Sum("amount"))["t"] or ZERO
        pct = (revenue / rep.monthly_target * 100) if rep.monthly_target else None
        rows.append({"rep": rep, "revenue": revenue, "margin": margin, "count": count, "pct": pct,
                     "bar": min(pct or 0, 100), "commission": commission,
                     "margin_pct": (margin / revenue * 100) if revenue else None})
        totals["revenue"] += revenue
        totals["margin"] += margin
        totals["target"] += rep.monthly_target
        totals["commission"] += commission
    rows.sort(key=lambda r: -r["revenue"])
    return render(request, "salesteam/performance.html", {
        "title": "Sales Performance", "rows": rows, "totals": totals, "month": start,
    })


class CommissionList(ErpListView):
    model = Commission
    title = "Commissions"
    columns = (("Date", "date"), ("Sales person", "rep"), ("Invoice", "invoice"), ("Basis", "basis_amount"),
               ("Rate %", "rate"), ("Commission", "amount"), ("Status", "status"))
    search_fields = ("rep__name", "invoice__number")
    select_related = ("rep", "invoice")

    def get_filters(self):
        return (("status", "Status", Commission.STATUSES),
                ("rep", "Sales person", [(r.pk, r.name) for r in SalesRep.objects.all()]))


@internal_required
@require_POST
def pay_commissions(request, pk):
    rep = get_object_or_404(SalesRep, pk=pk)
    ids = request.POST.getlist("commission")
    account = get_object_or_404(Account, pk=request.POST.get("paid_from"), is_bank=True)
    date = request.POST.get("date") or datetime.date.today()
    try:
        payout = services.pay_commissions(rep, list(Commission.objects.filter(pk__in=ids, rep=rep)), account, date,
                                          request.POST.get("reference", ""), request.user)
        messages.success(request, f"Payout {payout.number} of {payout.amount:,.2f} posted.")
    except ValidationError as exc:
        for m in exc.messages:
            messages.error(request, m)
    return redirect(rep.get_absolute_url())


@internal_required
@require_POST
def approve_commissions(request, pk):
    rep = get_object_or_404(SalesRep, pk=pk)
    n = Commission.objects.filter(rep=rep, status=Commission.ACCRUED, pk__in=request.POST.getlist("commission")) \
        .update(status=Commission.APPROVED)
    messages.success(request, f"{n} commission(s) approved.")
    return redirect(rep.get_absolute_url())
