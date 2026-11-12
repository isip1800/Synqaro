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

    open_ar = list(CustomerInvoice.objects.filter(status__in=CustomerInvoice.OPEN_STATUSES)
                   .select_related("customer").prefetch_related("lines"))
    ar_total = sum((i.balance_due for i in open_ar), ZERO)
    overdue = [i for i in open_ar if i.due_date and i.due_date < today]
    open_ap = list(VendorBill.objects.filter(status__in=VendorBill.OPEN_STATUSES).prefetch_related("lines"))
    ap_total = sum((b.balance_due_base for b in open_ap), ZERO)
    ap_due_week = sum((b.balance_due_base for b in open_ap
                       if b.due_date and b.due_date <= today + datetime.timedelta(days=7)), ZERO)

    inv_value = StockQuant.objects.aggregate(v=Sum(F("quantity") * F("product__avg_cost")))["v"] or ZERO
    shipments = (Shipment.objects.exclude(status__in=[Shipment.RECEIVED, Shipment.CANCELLED])
                 .select_related("vendor").order_by("eta")[:8])
    low_stock = []
    for p in Product.objects.filter(is_active=True, reorder_point__gt=0):
        if p.qty_on_hand + p.qty_on_order <= p.reorder_point:
            low_stock.append(p)

    ctx = {
        "sales_mtd": sales_mtd,
        "margin_mtd": margin_mtd,
        "margin_pct": (margin_mtd / sales_mtd * 100) if sales_mtd else None,
        "ar_total": ar_total,
        "overdue_count": len(overdue),
        "overdue_total": sum((i.balance_due for i in overdue), ZERO),
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
