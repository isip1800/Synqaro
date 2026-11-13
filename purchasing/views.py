import datetime

from django.db.models import Prefetch

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse_lazy
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.crud import ErpCreateView, ErpDetailView, ErpListView, ErpUpdateView, run_action
from core.mixins import internal_required
from core.models import Company, ExchangeRate, default_warehouse
from core.utils import ZERO, d

from . import services
from .forms import (
    LandedCostForm,
    POLineFormSet,
    PurchaseOrderForm,
    ShipmentDocumentForm,
    ShipmentForm,
    ShipmentLineFormSet,
)
from products.models import ProductImage

from .models import GoodsReceipt, LandedCost, PurchaseOrder, Shipment, Vendor


# ----------------------------------------------------------------------------- Vendors

class VendorList(ErpListView):
    model = Vendor
    template_name = "purchasing/vendor_list.html"
    search_fields = ("code", "name", "contact_name", "email", "country")

    def get_filters(self):
        return (("vendor_type", "Type", Vendor.TYPES), ("is_active", "Active", [("True", "Active"),
                                                                                ("False", "Inactive")]))


class VendorDetail(ErpDetailView):
    model = Vendor
    template_name = "purchasing/vendor_detail.html"

    def get_context_data(self, **kwargs):
        from accounting.models import VendorBill

        from django.contrib.contenttypes.models import ContentType
        from django.db.models import Q

        from core.models import Attachment, Note
        from returns.models import VendorCredit

        ctx = super().get_context_data(**kwargs)
        v = self.object
        today = datetime.date.today()
        perms = getattr(self.request, "perms", ())
        open_pos = list(v.purchase_orders.filter(status__in=PurchaseOrder.OPEN_STATUSES).prefetch_related("lines")
                        .order_by("-order_date", "-id"))
        shipments = Shipment.objects.filter(Q(vendor=v) | Q(forwarder=v)).order_by("-id")
        ct = ContentType.objects.get_for_model(Vendor)
        ctx.update(
            today=today,
            orders=v.purchase_orders.prefetch_related("lines").select_related("currency")
            .order_by("-order_date", "-id")[:50],
            order_count=v.purchase_orders.count(),
            open_pos=open_pos[:8],
            open_po_count=len(open_pos),
            open_po_value=sum((po.total for po in open_pos), ZERO),
            shipments=shipments[:50],
            shipment_count=shipments.count(),
            in_transit=shipments.filter(status__in=Shipment.IN_TRANSIT_STATUSES).count(),
            contacts=list(v.portal_contacts.select_related("user")),
            note_count=Note.objects.filter(content_type=ct, object_id=v.pk).count()
            + Attachment.objects.filter(content_type=ct, object_id=v.pk).count(),
            returns=v.returns.prefetch_related("lines").order_by("-date", "-id")[:25],
        )
        if "accounting.view" in perms:
            open_bills = list(v.bills.filter(status__in=VendorBill.OPEN_STATUSES).prefetch_related("lines")
                              .order_by("due_date"))
            ctx.update(
                open_bills=open_bills,
                overdue_count=sum(1 for b in open_bills if b.due_date and b.due_date < today),
                bills=v.bills.prefetch_related("lines").order_by("-bill_date", "-id")[:50],
                bill_count=v.bills.count(),
                payments=v.payments.order_by("-date", "-id")[:50],
                payment_count=v.payments.count(),
                credits=v.credits.prefetch_related("lines").order_by("-date", "-id")[:25],
                open_credit_count=v.credits.filter(status__in=VendorCredit.OPEN_STATUSES).count(),
            )
        if "costs.view" in perms:
            ctx["vendor_products"] = list(v.vendor_products.select_related("product", "currency"))
        ctx["returns_count"] = len(ctx["returns"]) + len(ctx.get("credits", []))
        return ctx


# ----------------------------------------------------------------------------- Purchase orders

class POList(ErpListView):
    create_url = reverse_lazy("purchasing:purchaseorder_create")
    model = PurchaseOrder
    title = "Purchase Orders"
    columns = (("PO", "number"), ("Vendor", "vendor"), ("Date", "order_date"), ("Expected", "expected_date"),
               ("Currency", "currency"), ("Total", "total"), ("Status", "status"))
    available_columns = (("PO", "number"), ("Vendor", "vendor"), ("Vendor ref.", "vendor_reference"),
                         ("Date", "order_date"), ("Expected", "expected_date"),
                         ("Vendor ready date", "vendor_confirmed_ship_date"), ("Incoterm", "incoterm"),
                         ("Default warehouse", "warehouse"), ("Currency", "currency"), ("Rate", "exchange_rate"),
                         ("Total", "total"), ("Total (base)", "total_base"), ("Ext FS cost USD", "fs_totals.usd"),
                         ("Ext FS cost RMB", "fs_totals.rmb"), ("Payment terms", "payment_terms"),
                         ("Status", "status"))
    search_fields = ("number", "vendor__name", "vendor_reference")
    select_related = ("vendor", "currency", "warehouse")
    cost_columns = ("total", "total_base", "fs_totals.usd", "fs_totals.rmb", "exchange_rate")

    def get_queryset(self):
        return super().get_queryset().prefetch_related("lines")

    def get_filters(self):
        return (("status", "Status", PurchaseOrder.STATUSES),
                ("vendor", "Vendor", [(v.pk, v.name) for v in Vendor.objects.filter(vendor_type="supplier")]))


class POFormMixin:
    model = PurchaseOrder
    form_class = PurchaseOrderForm
    formset_class = POLineFormSet

    def get_initial(self):
        initial = super().get_initial()
        if not self.object:
            initial.setdefault("order_date", datetime.date.today())
            vendor_id = self.request.GET.get("vendor")
            vendor = Vendor.objects.filter(pk=vendor_id).first() if vendor_id else None
            if vendor:
                initial.update(currency=vendor.currency_id, incoterm=vendor.default_incoterm,
                               exchange_rate=ExchangeRate.get_rate(vendor.currency_id))
            else:
                initial.setdefault("currency", Company.get().base_currency_id)
            wh = default_warehouse()
            if wh:
                initial.setdefault("warehouse", wh.pk)
        return initial

    def before_save(self, form):
        super().before_save(form)
        po = form.instance
        if po.currency_id != Company.get().base_currency_id and po.exchange_rate == 1:
            po.exchange_rate = ExchangeRate.get_rate(po.currency_id, po.order_date)


class POCreate(POFormMixin, ErpCreateView):
    title = "New Purchase Order"


class POUpdate(POFormMixin, ErpUpdateView):
    pass


class PODetail(ErpDetailView):
    model = PurchaseOrder
    template_name = "purchasing/po_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        po = self.object
        ctx.update(
            lines=po.lines.select_related("product", "warehouse").prefetch_related(
                Prefetch("product__images", queryset=ProductImage.objects.only("id", "is_primary", "product_id"))),
            receipts=po.receipts.all(),
            bills=po.bills.prefetch_related("lines"),
            shipments=Shipment.objects.filter(lines__po_line__order=po).distinct(),
            today=datetime.date.today(),
            base=Company.get().base_currency_id,
            fs=po.fs_totals if "costs.view" in getattr(self.request, "perms", ()) else {"any": False},
        )
        return ctx


def _po_action(func, success):
    @internal_required
    @require_POST
    def view(request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        result = run_action(request, po, func, success, po, request)
        if hasattr(result, "get_absolute_url"):
            return redirect(result.get_absolute_url())
        return redirect(po.get_absolute_url())

    return view


po_send = _po_action(lambda po, r: services.send_po(po), "PO marked as sent to vendor.")
po_cancel = _po_action(lambda po, r: services.cancel_po(po), "PO cancelled.")
po_close = _po_action(lambda po, r: services.close_po(po), "PO closed.")
po_confirm = _po_action(
    lambda po, r: services.confirm_po(po, ship_date=r.POST.get("ship_date") or None,
                                      vendor_reference=r.POST.get("vendor_reference", ""),
                                      notes=r.POST.get("notes", ""), by=r.user.get_username()),
    "PO confirmed.",
)
po_create_shipment = _po_action(lambda po, r: services.create_shipment_for_po(po, approved_by=r.user),
                                "Shipment created from PO.")
po_create_bill = _po_action(
    lambda po, r: services.create_bill_from_po(po, r.POST.get("vendor_invoice_number") or "TBD",
                                               r.POST.get("bill_date") or datetime.date.today()),
    "Draft bill created — review and post it.",
)


@internal_required
def po_receive(request, pk):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    lines = po.lines.select_related("product", "warehouse")
    if request.method == "POST":
        quantities = {line.pk: d(request.POST.get(f"qty_{line.pk}")) for line in lines}
        date = request.POST.get("date") or datetime.date.today()
        try:
            with transaction.atomic():
                receipt = services.receive_po(po, quantities, date, user=request.user)
            messages.success(request, f"Goods receipt {receipt.number} posted.")
            return redirect(receipt.get_absolute_url())
        except ValidationError as exc:
            for m in exc.messages:
                messages.error(request, m)
    return render(request, "purchasing/po_receive.html", {"po": po, "lines": lines, "title": f"Receive {po.number}",
                                                          "today": datetime.date.today()})


# ----------------------------------------------------------------------------- Shipments / imports

class ShipmentList(ErpListView):
    model = Shipment
    title = "Import Shipments"
    template_name = "purchasing/shipment_list.html"
    search_fields = ("number", "bill_of_lading", "container_numbers", "vendor__name", "customs_entry_number")
    select_related = ("vendor", "forwarder")

    def get_filters(self):
        return (("status", "Status", Shipment.STATUSES), ("mode", "Mode", Shipment.MODES),
                ("approved", "Approval", [("True", "Approved"), ("False", "Pending approval")]))


class ShipmentFormMixin:
    model = Shipment
    form_class = ShipmentForm
    formset_class = ShipmentLineFormSet

    def get_initial(self):
        initial = super().get_initial()
        if not self.object:
            wh = default_warehouse()
            if wh:
                initial.setdefault("warehouse", wh.pk)
        return initial

    def after_save(self, form):
        services.validate_shipment_lines(self.object)
        if not self.object.approved and not self.object.created_via_portal:
            services.approve_shipment(self.object, self.request.user)  # booked by staff = approved


class ShipmentCreate(ShipmentFormMixin, ErpCreateView):
    title = "New Import Shipment"


class ShipmentUpdate(ShipmentFormMixin, ErpUpdateView):
    pass


class ShipmentDetail(ErpDetailView):
    model = Shipment
    template_name = "purchasing/shipment_detail.html"
    TIMELINE = [Shipment.PLANNED, Shipment.BOOKED, Shipment.IN_TRANSIT, Shipment.AT_PORT, Shipment.CUSTOMS,
                Shipment.CLEARED, Shipment.RECEIVED]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        s = self.object
        lines = list(s.lines.select_related("po_line__product", "po_line__order", "po_line__warehouse"))
        uncapitalized = sum((lc.amount for lc in s.landed_costs.filter(capitalized=False)), d(0))
        preview = services.allocate(s, uncapitalized, lines) if s.status != Shipment.RECEIVED else {}
        rows = []
        for line in lines:
            pending = preview.get(line.pk, d(0))
            landed = line.allocated_landed_cost if s.status == Shipment.RECEIVED else pending
            unit_goods = line.po_line.unit_cost_base
            rows.append({
                "line": line,
                "value": line.value_base,
                "landed": landed,
                "unit_goods": unit_goods,
                "unit_landed": (landed / line.quantity) if line.quantity else d(0),
                "unit_total": unit_goods + ((landed / line.quantity) if line.quantity else d(0)),
            })
        statuses = dict(Shipment.STATUSES)
        idx = self.TIMELINE.index(s.status) if s.status in self.TIMELINE else -1
        ctx.update(
            rows=rows,
            pending_landed=uncapitalized,
            timeline=[{"key": k, "label": statuses[k], "done": i <= idx, "current": i == idx}
                      for i, k in enumerate(self.TIMELINE)],
            landed_form=LandedCostForm(),
            doc_form=ShipmentDocumentForm(),
            today=datetime.date.today(),
            containers=[c for c in s.container_numbers.splitlines() if c.strip()],
        )
        return ctx


def _shipment_action(func, success):
    @internal_required
    @require_POST
    def view(request, pk):
        s = get_object_or_404(Shipment, pk=pk)
        result = run_action(request, s, func, success, s, request)
        if hasattr(result, "get_absolute_url") and not isinstance(result, (Shipment, LandedCost)):
            return redirect(result.get_absolute_url())
        return redirect(s.get_absolute_url())

    return view


shipment_receive = _shipment_action(
    lambda s, r: services.receive_shipment(s, r.POST.get("date") or datetime.date.today(), r.user),
    "Shipment received into inventory with landed costs capitalized.",
)
shipment_approve = _shipment_action(lambda s, r: services.approve_shipment(s, r.user),
                                    "Booking approved — quantities now count as Booked & Approved.")
shipment_unapprove = _shipment_action(lambda s, r: services.unapprove_shipment(s, r.user),
                                      "Approval removed — booking is pending again.")
shipment_estimate_duty = _shipment_action(lambda s, r: services.estimate_duty(s),
                                          "Duty estimated from product duty rates.")


@internal_required
@require_POST
def shipment_add_landed_cost(request, pk):
    s = get_object_or_404(Shipment, pk=pk)
    form = LandedCostForm(request.POST)
    if form.is_valid():
        lc = form.save(commit=False)
        lc.shipment = s
        lc.save()
        if s.status == Shipment.RECEIVED:
            messages.warning(request, "Shipment already received: click 'Capitalize' on the cost to post it "
                                      "into inventory value.")
        else:
            messages.success(request, "Landed cost added.")
    else:
        messages.error(request, f"Could not add landed cost: {form.errors.as_text()}")
    return redirect(s.get_absolute_url())


@internal_required
@require_POST
def landed_cost_delete(request, pk):
    lc = get_object_or_404(LandedCost, pk=pk)
    if lc.capitalized:
        messages.error(request, "Capitalized landed costs cannot be deleted.")
    else:
        lc.delete()
        messages.success(request, "Landed cost removed.")
    return redirect(lc.shipment.get_absolute_url())


@internal_required
@require_POST
def landed_cost_capitalize(request, pk):
    lc = get_object_or_404(LandedCost, pk=pk)
    run_action(request, lc, services.capitalize_late_landed_cost, "Landed cost capitalized.", lc,
               request.POST.get("date") or datetime.date.today(), request.user)
    return redirect(lc.shipment.get_absolute_url())


@internal_required
@require_POST
def shipment_upload_document(request, pk):
    s = get_object_or_404(Shipment, pk=pk)
    form = ShipmentDocumentForm(request.POST, request.FILES)
    if form.is_valid():
        doc = form.save(commit=False)
        doc.shipment = s
        doc.uploaded_by = request.user
        doc.save()
        messages.success(request, "Document uploaded.")
    else:
        messages.error(request, f"Upload failed: {form.errors.as_text()}")
    return redirect(s.get_absolute_url())


class LandedCostList(ErpListView):
    model = LandedCost
    title = "Landed Costs"
    columns = (("Shipment", "shipment"), ("Type", "cost_type"), ("Description", "description"),
               ("Billed by", "vendor"), ("Amount", "amount"), ("Capitalized", "capitalized"))
    search_fields = ("shipment__number", "description", "vendor__name")
    select_related = ("shipment", "vendor")

    def get_filters(self):
        return (("cost_type", "Type", LandedCost.COST_TYPES),
                ("capitalized", "Capitalized", [("True", "Yes"), ("False", "No")]))


# ----------------------------------------------------------------------------- Receipts

class ReceiptList(ErpListView):
    model = GoodsReceipt
    title = "Goods Receipts"
    columns = (("Receipt", "number"), ("Date", "date"), ("PO", "purchase_order"), ("Shipment", "shipment"),
               ("Warehouse", "warehouse"), ("Value", "total_value"))
    cost_columns = ("total_value",)
    search_fields = ("number", "purchase_order__number", "shipment__number")
    select_related = ("purchase_order", "shipment", "warehouse")


class ReceiptDetail(ErpDetailView):
    model = GoodsReceipt
    template_name = "purchasing/receipt_detail.html"
