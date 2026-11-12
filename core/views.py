import datetime

from django.contrib import messages
from django.db.models import F, Sum
from django.shortcuts import redirect, render

from core.utils import ZERO

from .crud import ErpListView
from .forms import model_form
from .mixins import internal_required, is_vendor_user
from .models import Company, Currency, ExchangeRate


def dashboard(request):
    if not request.user.is_authenticated:
        return redirect("login")
    if is_vendor_user(request.user):
        return redirect("portal:dashboard")
    return _dashboard(request)


@internal_required
def _dashboard(request):
    from accounting.models import CustomerInvoice, VendorBill
    from inventory.models import StockQuant
    from products.models import Product
    from purchasing.models import PurchaseOrder, Shipment
    from sales.models import SalesOrder

    today = datetime.date.today()
    month_start = today.replace(day=1)
    posted = CustomerInvoice.objects.filter(status__in=CustomerInvoice.POSTED_STATUSES)
    month_invoices = posted.filter(invoice_date__gte=month_start).prefetch_related("lines")
    sales_mtd = sum((i.subtotal for i in month_invoices), ZERO)
    margin_mtd = sum((i.gross_margin for i in month_invoices), ZERO)

    from django.db.models import Count, ExpressionWrapper, DecimalField

    open_ar = CustomerInvoice.objects.filter(status__in=CustomerInvoice.OPEN_STATUSES)
    bal = F("posted_total") - F("amount_paid")
    ar_total = open_ar.aggregate(t=Sum(bal))["t"] or ZERO
    overdue_agg = open_ar.filter(due_date__lt=today).aggregate(t=Sum(bal), n=Count("id"))
    open_ap = VendorBill.objects.filter(status__in=VendorBill.OPEN_STATUSES)
    bal_base = ExpressionWrapper((F("posted_total") - F("amount_paid")) * F("exchange_rate"),
                                 output_field=DecimalField(max_digits=20, decimal_places=4))
    ap_total = open_ap.aggregate(t=Sum(bal_base))["t"] or ZERO
    ap_due_week = open_ap.filter(due_date__lte=today + datetime.timedelta(days=7)).aggregate(
        t=Sum(bal_base))["t"] or ZERO

    inv_value = StockQuant.objects.aggregate(v=Sum(F("quantity") * F("product__avg_cost")))["v"] or ZERO
    shipments = (Shipment.objects.exclude(status__in=[Shipment.RECEIVED, Shipment.CANCELLED])
                 .select_related("vendor").order_by("eta")[:8])
    from inventory import availability

    candidates = list(Product.objects.filter(is_active=True, reorder_point__gt=0).select_related("preferred_vendor"))
    data = availability.compute([p.pk for p in candidates]) if candidates else {}
    low_stock = []
    for p in candidates:
        b = data[p.pk]
        on_order = b["po_open"] + b["booked"] + b["booked_approved"] + b["in_transit"] + b["po_new"]
        if b["on_hand"] + on_order <= p.reorder_point:
            p.dash_on_hand, p.dash_on_order = b["on_hand"], on_order
            low_stock.append(p)

    ctx = {
        "sales_mtd": sales_mtd,
        "margin_mtd": margin_mtd,
        "margin_pct": (margin_mtd / sales_mtd * 100) if sales_mtd else None,
        "ar_total": ar_total,
        "overdue_count": overdue_agg["n"],
        "overdue_total": overdue_agg["t"] or ZERO,
        "ap_total": ap_total,
        "ap_due_week": ap_due_week,
        "inv_value": inv_value,
        "open_pos": PurchaseOrder.objects.filter(status__in=PurchaseOrder.OPEN_STATUSES).count(),
        "shipments": shipments,
        "low_stock": low_stock[:10],
        "low_stock_count": len(low_stock),
        "orders_to_ship": SalesOrder.objects.filter(status__in=SalesOrder.SHIPPABLE_STATUSES + (SalesOrder.PENDING,))
        .select_related("customer")[:8],
        "quotes": SalesOrder.objects.filter(status=SalesOrder.QUOTE).count(),
        "today": today,
    }
    return render(request, "core/dashboard.html", ctx)


_CompanyBase = model_form(Company, ["name", "legal_name", "tax_id", "address", "phone", "email", "base_currency",
                                    "default_sales_tax_rate", "default_payment_terms_days",
                                    "default_warehouse", "require_so_approval", "upc_company_prefix",
                                    "upc_next_item_ref", "cbm_20", "cbm_40", "cbm_40hq", "cbm_45",
                                    "avail_no_negative", "sales_avail_no_negative"])


class CompanyForm(_CompanyBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django import forms

        from inventory.availability import FLOORABLE

        for name in ("avail_no_negative", "sales_avail_no_negative"):
            self.fields[name] = forms.MultipleChoiceField(
                choices=FLOORABLE, required=False, widget=forms.CheckboxSelectMultiple,
                label=Company._meta.get_field(name).verbose_name.capitalize(),
                initial=getattr(self.instance, name),
                help_text="Ticked fields show 0 instead of a negative number on this screen")


@internal_required
def company(request):
    obj = Company.get()
    form = CompanyForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Company settings saved.")
        return redirect("core:company")
    return render(request, "core/form.html", {"form": form, "title": "Company Settings"})


class ExchangeRateList(ErpListView):
    model = ExchangeRate
    title = "Currencies & Exchange Rates"
    template_name = "core/exchange_rates.html"
    columns = (("Currency", "currency"), ("Date", "date"), ("Rate to base", "rate"))
    select_related = ("currency",)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["currencies"] = Currency.objects.all()
        return ctx


# ----------------------------------------------------------------------------- column chooser / saved filters

def _safe_next(request, default="/"):
    from django.utils.http import url_has_allowed_host_and_scheme

    nxt = request.POST.get("next") or request.GET.get("next") or default
    return nxt if url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}) else default


@internal_required
def columns_save(request):
    from django.views.decorators.http import require_POST  # noqa: F401

    from .columns import reset_columns, save_columns

    if request.method == "POST":
        screen = request.POST.get("screen", "")[:90]
        if request.POST.get("reset"):
            reset_columns(request.user, screen)
            messages.success(request, "Columns reset to the default.")
        else:
            keys = request.POST.getlist("col")
            if not keys:
                messages.error(request, "Pick at least one column.")
            else:
                save_columns(request.user, screen, keys)
                messages.success(request, "Columns saved.")
    return redirect(_safe_next(request))


@internal_required
def filter_save(request):
    from .models import SavedFilter

    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:80]
        screen = request.POST.get("screen", "")[:90]
        if not name:
            messages.error(request, "Give the filter a name.")
        else:
            SavedFilter.objects.update_or_create(
                user=request.user, screen=screen, name=name,
                defaults={"query": request.POST.get("query", ""), "shared": bool(request.POST.get("shared"))})
            messages.success(request, f"Filter “{name}” saved.")
    return redirect(_safe_next(request))


@internal_required
def filter_delete(request, pk):
    from .models import SavedFilter

    if request.method == "POST":
        f = SavedFilter.objects.filter(pk=pk).first()
        if f and (f.user_id == request.user.id or request.user.is_superuser):
            f.delete()
            messages.success(request, f"Filter “{f.name}” deleted.")
    return redirect(_safe_next(request))


# ----------------------------------------------------------------------------- email settings / log

@internal_required
def email_settings(request):
    from django.conf import settings as dj

    from . import documents
    from .models import EmailLog

    templates = [documents.get_template(k) for k, _ in documents.DEFAULT_TEMPLATES.items()]
    return render(request, "core/email_settings.html", {
        "title": "Email", "configured": dj.EMAIL_CONFIGURED, "host": dj.EMAIL_HOST, "port": dj.EMAIL_PORT,
        "from_email": dj.DEFAULT_FROM_EMAIL, "user_name": dj.EMAIL_HOST_USER, "templates": templates,
        "recent": EmailLog.objects.all()[:10],
    })


class EmailLogList(ErpListView):
    from .models import EmailLog as model  # noqa: N813

    title = "Email Log"
    columns = (("Sent", "created_at"), ("Document", "document"), ("To", "to"), ("Subject", "subject"),
               ("Status", "status"), ("By", "sent_by"), ("Error", "error"))
    search_fields = ("document", "to", "subject")
    create_url = None
