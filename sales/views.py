import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.crud import ErpCreateView, ErpDetailView, ErpListView, ErpUpdateView, run_action
from core.mixins import internal_required
from core.models import Company, default_warehouse
from core.utils import d

from . import services
from .forms import CustomerForm, SalesOrderForm, SOLineFormSet
from .models import Customer, Delivery, SalesOrder


def rep_scope(request, qs, field="sales_rep"):
    """Sales people without staff rights only see their own customers and orders."""
    user = request.user
    rep = getattr(user, "sales_rep", None)
    if rep and not (user.is_staff or user.is_superuser):
        return qs.filter(**{field: rep})
    return qs


# ----------------------------------------------------------------------------- Customers

class CustomerList(ErpListView):
    model = Customer
    template_name = "sales/customer_list.html"
    search_fields = ("code", "name", "contact_name", "email", "city")
    select_related = ("sales_rep", "price_list")

    def get_queryset(self):
        return rep_scope(self.request, super().get_queryset())

    def get_filters(self):
        from salesteam.models import SalesRep

        return (("sales_rep", "Sales person", [(r.pk, r.name) for r in SalesRep.objects.all()]),
                ("is_active", "Active", [("True", "Active"), ("False", "Inactive")]))


class CustomerDetail(ErpDetailView):
    model = Customer
    template_name = "sales/customer_detail.html"

    def get_queryset(self):
        return rep_scope(self.request, super().get_queryset())

    def get_context_data(self, **kwargs):
        from accounting.models import CustomerInvoice

        ctx = super().get_context_data(**kwargs)
        c = self.object
        ctx.update(
            orders=c.sales_orders.all()[:20],
            open_invoices=c.invoices.filter(status__in=CustomerInvoice.OPEN_STATUSES).prefetch_related("lines"),
            invoices=c.invoices.prefetch_related("lines")[:20],
            payments=c.payments.all()[:10],
            today=datetime.date.today(),
        )
        return ctx


@internal_required
def customer_statement(request, pk):
    from accounting.models import JournalLine
    from accounting.services import acct

    customer = get_object_or_404(Customer, pk=pk)
    lines = JournalLine.objects.filter(customer=customer, account=acct("AR")).select_related("entry") \
        .order_by("entry__date", "entry_id")
    running, rows = d(0), []
    for line in lines:
        running += line.debit - line.credit
        rows.append({"line": line, "balance": running})
    return render(request, "sales/customer_statement.html", {
        "customer": customer, "rows": rows, "balance": running, "title": f"Statement — {customer}",
        "today": datetime.date.today(),
    })


# ----------------------------------------------------------------------------- Sales orders

class OrderList(ErpListView):
    model = SalesOrder
    title = "Sales Orders & Quotes"
    columns = (("Number", "number"), ("Customer", "customer"), ("Date", "order_date"),
               ("Requested", "requested_date"), ("Sales person", "sales_rep"), ("Total", "total"),
               ("Status", "status"))
    available_columns = (("Number", "number"), ("Customer", "customer"), ("Buyer", "buyer"),
                         ("Customer PO #", "customer_po"), ("Date", "order_date"), ("Requested", "requested_date"),
                         ("Valid until", "valid_until"), ("Sales person", "sales_rep"),
                         ("Default warehouse", "warehouse"), ("Subtotal", "subtotal"), ("Tax", "tax"),
                         ("Total", "total"), ("Uninvoiced total", "uninvoiced_total"),
                         ("Est. margin", "estimated_margin"), ("Status", "status"),
                         ("Approval reason", "approval_reason"))
    search_fields = ("number", "customer__name", "customer_po", "buyer__name")
    select_related = ("customer", "sales_rep", "buyer", "warehouse")

    def get_queryset(self):
        return rep_scope(self.request, super().get_queryset().prefetch_related("lines"))

    def get_filters(self):
        return (("status", "Status", SalesOrder.STATUSES),)


class OrderFormMixin:
    model = SalesOrder
    form_class = SalesOrderForm
    formset_class = SOLineFormSet

    def get_initial(self):
        initial = super().get_initial()
        if not self.object:
            initial.setdefault("order_date", datetime.date.today())
            initial.setdefault("tax_rate", Company.get().default_sales_tax_rate)
            wh = default_warehouse()
            if wh:
                initial.setdefault("warehouse", wh.pk)
            rep = getattr(self.request.user, "sales_rep", None)
            if rep:
                initial.setdefault("sales_rep", rep.pk)
        return initial

    def before_save(self, form):
        super().before_save(form)
        services.copy_order_defaults(form.instance)


class OrderCreate(OrderFormMixin, ErpCreateView):
    title = "New Quotation / Sales Order"


class OrderUpdate(OrderFormMixin, ErpUpdateView):
    pass


class OrderDetail(ErpDetailView):
    model = SalesOrder
    template_name = "sales/order_detail.html"

    def get_queryset(self):
        return rep_scope(self.request, super().get_queryset())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        o = self.object
        lines = list(o.lines.select_related("product", "warehouse"))
        stock = {}
        if o.status in (SalesOrder.QUOTE,) + SalesOrder.RESERVING_STATUSES:
            from inventory.models import StockQuant

            for q in StockQuant.objects.filter(product__in=[ln.product for ln in lines]):
                stock[(q.product_id, q.warehouse_id)] = q.quantity
        ctx.update(
            lines=[{"line": ln, "stock": stock.get((ln.product_id, ln.warehouse_id), d(0))} for ln in lines],
            multi_wh=len({ln.warehouse_id for ln in lines}) > 1,
            deliveries=o.deliveries.all(),
            invoices=o.invoices.prefetch_related("lines"),
            credit_warning=services.credit_check(o) if o.status == SalesOrder.QUOTE else None,
            today=datetime.date.today(),
        )
        return ctx


def _order_action(func, success):
    @internal_required
    @require_POST
    def view(request, pk):
        order = get_object_or_404(rep_scope(request, SalesOrder.objects.all()), pk=pk)
        result = run_action(request, order, func, success, order, request)
        if hasattr(result, "get_absolute_url"):
            return redirect(result.get_absolute_url())
        return redirect(order.get_absolute_url())

    return view


def _confirm(o, r):
    services.confirm_order(o, user=r.user)
    if o.status == SalesOrder.PENDING:
        from django.contrib import messages as m

        m.warning(r, f"Order sent for approval: {o.approval_reason}")
    return o


order_confirm = _order_action(_confirm, "Order submitted.")
order_approve = _order_action(lambda o, r: services.approve_order(o, r.user), "Order approved — now open.")
order_reject = _order_action(lambda o, r: services.reject_order(o, r.user), "Order sent back to quotation.")
order_cancel = _order_action(lambda o, r: services.cancel_order(o), "Order cancelled.")
order_invoice = _order_action(
    lambda o, r: services.create_invoice_from_order(o, r.POST.get("date") or datetime.date.today()),
    "Draft invoice created — review and post it.",
)


@internal_required
def order_ship(request, pk):
    order = get_object_or_404(rep_scope(request, SalesOrder.objects.all()), pk=pk)
    lines = order.lines.select_related("product", "warehouse").order_by("warehouse__code", "id")
    if request.method == "POST":
        quantities = {line.pk: d(request.POST.get(f"qty_{line.pk}")) for line in lines}
        try:
            with transaction.atomic():
                deliveries = services.ship_order(
                    order, quantities, request.POST.get("date") or datetime.date.today(),
                    request.POST.get("carrier", ""), request.POST.get("tracking_number", ""), request.user,
                )
            if len(deliveries) == 1:
                messages.success(request, f"Delivery {deliveries[0].number} posted ({deliveries[0].warehouse.code}).")
                return redirect(deliveries[0].get_absolute_url())
            messages.success(request, "Posted one delivery per warehouse: " + ", ".join(
                f"{dl.number} ({dl.warehouse.code})" for dl in deliveries))
            return redirect(order.get_absolute_url())
        except ValidationError as exc:
            for m in exc.messages:
                messages.error(request, m)
    from inventory.models import StockQuant

    stock = {(q.product_id, q.warehouse_id): q.quantity
             for q in StockQuant.objects.filter(product__in=[ln.product_id for ln in lines])}
    rows = []
    for ln in lines:
        on_hand = stock.get((ln.product_id, ln.warehouse_id), d(0))
        rows.append({"line": ln, "stock": on_hand, "default": min(ln.qty_to_ship, max(on_hand, d(0)))})
    return render(request, "sales/order_ship.html", {"order": order, "rows": rows, "title": f"Ship {order.number}",
                                                     "today": datetime.date.today()})


class DeliveryList(ErpListView):
    model = Delivery
    title = "Deliveries"
    columns = (("Delivery", "number"), ("Date", "date"), ("Order", "order"), ("Customer", "order.customer"),
               ("Carrier", "carrier"), ("Tracking", "tracking_number"))
    search_fields = ("number", "order__number", "order__customer__name", "tracking_number")
    select_related = ("order", "order__customer")

    def get_queryset(self):
        return rep_scope(self.request, super().get_queryset(), "order__sales_rep")


class DeliveryDetail(ErpDetailView):
    model = Delivery
    template_name = "sales/delivery_detail.html"
