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
    columns = (("Number", "number"), ("Customer", "customer"), ("Ship to", "ship_to"), ("Customer PO #", "customer_po"),
               ("Date", "order_date"), ("Start ship", "ship_start_date"), ("Cancel", "cancel_date"),
               ("Total", "total"), ("Status", "status"))
    available_columns = (("Number", "number"), ("Customer", "customer"), ("Ship to", "ship_to"), ("Buyer", "buyer"),
                         ("Customer PO #", "customer_po"), ("Master PO #", "master_customer_po"),
                         ("Date", "order_date"), ("Start ship", "ship_start_date"), ("Cancel", "cancel_date"),
                         ("Requested", "requested_date"), ("On hold", "on_hold"),
                         ("Released to WH", "released_at"),
                         ("Valid until", "valid_until"), ("Sales person", "sales_rep"),
                         ("Default warehouse", "warehouse"), ("Subtotal", "subtotal"), ("Tax", "tax"),
                         ("Total", "total"), ("Uninvoiced total", "uninvoiced_total"),
                         ("Est. margin", "estimated_margin"), ("Status", "status"),
                         ("Approval reason", "approval_reason"))
    search_fields = ("number", "customer__name", "customer_po", "master_customer_po", "buyer__name",
                     "ship_to__location_id", "ship_to__name")
    select_related = ("customer", "sales_rep", "buyer", "warehouse", "ship_to")

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
    locked_message = "This order can't be edited: it has been released to the warehouse (recall it first), " \
                     "or it is shipped / closed (use Reopen)."

    def is_locked(self, obj):
        return not obj.is_editable

    def after_save(self, form):
        services.after_order_edit(self.object)


class OrderDetail(ErpDetailView):
    model = SalesOrder
    template_name = "sales/order_detail.html"

    def get_queryset(self):
        return rep_scope(self.request, super().get_queryset())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        o = self.object
        from django.db.models import Prefetch

        from products.models import ProductImage

        lines = list(o.lines.select_related("product", "warehouse").prefetch_related(
            Prefetch("product__images", queryset=ProductImage.objects.only("id", "is_primary", "product_id"))))
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
order_release = _order_action(lambda o, r: services.release_to_warehouse(o, r.user),
                              "Released to the warehouse — the order is locked for editing.")
order_recall = _order_action(lambda o, r: services.recall_from_warehouse(o, r.user),
                             "Recalled from the warehouse — the order can be edited again.")
order_hold = _order_action(lambda o, r: services.hold_order(o, r.POST.get("reason", ""), r.user), "Order on hold.")
order_unhold = _order_action(lambda o, r: services.release_hold(o, r.user), "Hold removed.")
order_reopen = _order_action(lambda o, r: services.reopen_order(o, r.user),
                             "Order reopened — you can now edit it.")
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


# ----------------------------------------------------------------------------- customer locations

LOCATION_FIELDS = ["location_id", "name", "address", "address2", "city", "state", "postal_code", "country", "phone",
                   "is_default", "is_active"]


@internal_required
def location_form(request, customer_pk=None, pk=None):
    from core.forms import model_form

    from .models import CustomerLocation

    loc = get_object_or_404(CustomerLocation, pk=pk) if pk else None
    customer = loc.customer if loc else get_object_or_404(Customer, pk=customer_pk)
    Form = model_form(CustomerLocation, LOCATION_FIELDS)
    form = Form(request.POST or None, instance=loc or CustomerLocation(customer=customer))
    if request.method == "POST" and form.is_valid():
        if CustomerLocation.objects.filter(customer=customer, location_id__iexact=form.cleaned_data["location_id"]) \
                .exclude(pk=getattr(loc, "pk", None)).exists():
            form.add_error("location_id", "This customer already has that location ID.")
        else:
            saved = form.save()
            messages.success(request, f"Location {saved} saved.")
            if request.POST.get("another"):
                return redirect("sales:location_create", customer_pk=customer.pk)
            return redirect(customer.get_absolute_url() + "#locations")
    return render(request, "core/form.html", {"form": form, "title": f"{'Edit' if loc else 'New'} location — "
                                                                     f"{customer}",
                                              "cancel_url": customer.get_absolute_url() + "#locations"})


@internal_required
@require_POST
def location_delete(request, pk):
    from django.db.models import ProtectedError

    from .models import CustomerLocation

    loc = get_object_or_404(CustomerLocation, pk=pk)
    try:
        loc.delete()
        messages.success(request, f"Location {loc} deleted.")
    except ProtectedError:
        messages.error(request, f"{loc} is used on sales orders — mark it inactive instead.")
    return redirect(loc.customer.get_absolute_url() + "#locations")


LOCATION_COLUMNS = ["Location ID", "Location Name", "Address", "Address 2", "City", "State", "Postal Code",
                    "Country", "Phone", "Default", "Active"]


@internal_required
def locations_export(request, customer_pk):
    import io

    import xlsxwriter
    from django.http import HttpResponse

    customer = get_object_or_404(Customer, pk=customer_pk)
    out = io.BytesIO()
    wb = xlsxwriter.Workbook(out, {"in_memory": False})
    ws = wb.add_worksheet("Locations")
    head = wb.add_format({"bold": True, "bg_color": "#E8ECF2", "border": 1})
    for c, h in enumerate(LOCATION_COLUMNS):
        ws.write(0, c, h, head)
        ws.set_column(c, c, 30 if h in ("Location Name", "Address") else 14)
    for r, loc in enumerate(customer.locations.all(), start=1):
        ws.write_row(r, 0, [loc.location_id, loc.name, loc.address, loc.address2, loc.city, loc.state,
                            loc.postal_code, loc.country, loc.phone, "Y" if loc.is_default else "",
                            "Y" if loc.is_active else "N"])
    wb.close()
    from products.services import _sanitize_xlsx

    resp = HttpResponse(_sanitize_xlsx(out.getvalue()),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="locations-{customer.code}.xlsx"'
    return resp


@internal_required
@require_POST
def locations_import(request, customer_pk):
    """Create / update ship-to locations from an .xlsx (same columns as the export)."""
    from openpyxl import load_workbook

    from .models import CustomerLocation

    customer = get_object_or_404(Customer, pk=customer_pk)
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Choose an .xlsx file.")
        return redirect(customer.get_absolute_url() + "#locations")
    try:
        ws = load_workbook(f, read_only=True, data_only=True).active
    except Exception:
        messages.error(request, "Could not read the file — use the Excel template from Export.")
        return redirect(customer.get_absolute_url() + "#locations")
    rows = ws.iter_rows(values_only=True)
    header = [str(h or "").strip().lower() for h in next(rows, [])]
    idx = {h.lower(): header.index(h.lower()) for h in LOCATION_COLUMNS if h.lower() in header}
    if "location id" not in idx or "location name" not in idx:
        messages.error(request, "The first row needs at least 'Location ID' and 'Location Name' columns.")
        return redirect(customer.get_absolute_url() + "#locations")

    def val(row, col):
        i = idx.get(col.lower())
        v = row[i] if i is not None and i < len(row) else None
        return "" if v is None else str(v).strip()

    created = updated = 0
    with transaction.atomic():
        for row in rows:
            if not row or not val(row, "Location ID"):
                continue
            loc_id = val(row, "Location ID").removesuffix(".0")
            loc, is_new = CustomerLocation.objects.get_or_create(customer=customer, location_id=loc_id,
                                                                defaults={"name": val(row, "Location Name")[:150]})
            loc.name = val(row, "Location Name")[:150] or loc.name
            for field, col in (("address", "Address"), ("address2", "Address 2"), ("city", "City"),
                               ("state", "State"), ("postal_code", "Postal Code"), ("country", "Country"),
                               ("phone", "Phone")):
                if col.lower() in idx:
                    setattr(loc, field, val(row, col).removesuffix(".0") if field == "postal_code" else val(row, col))
            if "default" in idx:
                loc.is_default = val(row, "Default").upper() in ("Y", "YES", "TRUE", "1", "X")
            if "active" in idx:
                loc.is_active = val(row, "Active").upper() not in ("N", "NO", "FALSE", "0")
            loc.save()
            created, updated = created + is_new, updated + (not is_new)
    messages.success(request, f"Locations imported: {created} new, {updated} updated.")
    return redirect(customer.get_absolute_url() + "#locations")
