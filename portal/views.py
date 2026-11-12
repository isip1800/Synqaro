import datetime

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounting.models import VendorBill
from core.mixins import internal_required, vendor_required
from core.models import default_warehouse
from core.utils import d
from purchasing import services as purchasing
from purchasing.forms import ShipmentDocumentForm
from purchasing.models import PurchaseOrder, Shipment

from .forms import PortalContactForm, VendorShipmentForm, vendor_line_formset
from .models import VendorContact

VENDOR_VISIBLE_PO = [PurchaseOrder.SENT, PurchaseOrder.CONFIRMED, PurchaseOrder.PARTIAL, PurchaseOrder.RECEIVED,
                     PurchaseOrder.CLOSED]
VENDOR_EDITABLE_SHIPMENT = [Shipment.PLANNED, Shipment.BOOKED, Shipment.IN_TRANSIT]


def _pos(request):
    return PurchaseOrder.objects.filter(vendor=request.vendor, status__in=VENDOR_VISIBLE_PO)


def _shipments(request):
    return Shipment.objects.filter(vendor=request.vendor)


@vendor_required
def dashboard(request):
    pos = _pos(request)
    bills = VendorBill.objects.filter(vendor=request.vendor).exclude(status=VendorBill.VOID).prefetch_related("lines")
    open_bills = [b for b in bills if b.status in VendorBill.OPEN_STATUSES]
    return render(request, "portal/dashboard.html", {
        "title": "Vendor Portal",
        "to_confirm": pos.filter(status=PurchaseOrder.SENT),
        "open_pos": pos.filter(status__in=[PurchaseOrder.CONFIRMED, PurchaseOrder.PARTIAL]),
        "shipments": _shipments(request).exclude(status__in=[Shipment.RECEIVED, Shipment.CANCELLED]),
        "open_bills": open_bills,
        "open_total": sum((b.balance_due for b in open_bills), d(0)),
        "drafts": bills.filter(status=VendorBill.DRAFT).count(),
    })


@vendor_required
def po_list(request):
    return render(request, "portal/po_list.html", {"title": "Purchase Orders",
                                                   "orders": _pos(request).prefetch_related("lines")})


@vendor_required
def po_detail(request, pk):
    po = get_object_or_404(_pos(request), pk=pk)
    return render(request, "portal/po_detail.html", {
        "title": po.number, "po": po, "lines": po.lines.select_related("product", "warehouse"),
        "vendor_skus": {vp.product_id: vp.vendor_sku for vp in request.vendor.vendor_products.all()},
        "today": datetime.date.today(),
        "can_invoice": request.user.vendor_contact.can_submit_invoices,
    })


@vendor_required
@require_POST
def po_confirm(request, pk):
    po = get_object_or_404(_pos(request), pk=pk)
    try:
        purchasing.confirm_po(po, ship_date=request.POST.get("ship_date") or None,
                              vendor_reference=request.POST.get("vendor_reference", ""),
                              notes=request.POST.get("notes", ""), by=request.user.get_username())
        messages.success(request, f"{po.number} confirmed. Thank you!")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("portal:po_detail", pk=po.pk)


@vendor_required
def shipment_list(request):
    return render(request, "portal/shipment_list.html", {"title": "Shipments", "shipments": _shipments(request)})


@vendor_required
def shipment_form(request, pk=None):
    shipment = get_object_or_404(_shipments(request), pk=pk) if pk else None
    if shipment and shipment.status not in VENDOR_EDITABLE_SHIPMENT:
        messages.error(request, "This shipment has arrived and can no longer be edited from the portal.")
        return redirect("portal:shipment_detail", pk=shipment.pk)
    FormSet = vendor_line_formset(request.vendor)
    form = VendorShipmentForm(request.POST or None, instance=shipment)
    formset = FormSet(request.POST or None, instance=shipment, prefix="lines")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        try:
            with transaction.atomic():
                obj = form.save(commit=False)
                obj.vendor = request.vendor
                if not obj.pk:
                    obj.created_via_portal = True
                    obj.warehouse = default_warehouse()
                    if obj.warehouse is None:
                        raise ValidationError("No receiving warehouse is configured. Please contact the buyer.")
                lines_changed = formset.has_changed()
                if obj.pk and obj.approved and lines_changed:
                    obj.approved, obj.approved_at, obj.approved_by = False, None, None
                obj.save()
                formset.instance = obj
                formset.save()
                first = obj.lines.select_related("po_line__order").first()
                if not first:
                    raise ValidationError("Add at least one PO line to the shipment.")
                if first.po_line.order.incoterm and not obj.incoterm:
                    obj.incoterm = first.po_line.order.incoterm
                obj.warehouse = first.po_line.warehouse
                obj.save()
                purchasing.validate_shipment_lines(obj)
            messages.success(request, f"Shipment {obj.number} saved. The buyer has been updated.")
            return redirect("portal:shipment_detail", pk=obj.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "portal/shipment_form.html", {
        "title": f"Edit {shipment.number}" if shipment else "New Advance Shipping Notice",
        "form": form, "formset": formset, "shipment": shipment,
    })


@vendor_required
def shipment_detail(request, pk):
    shipment = get_object_or_404(_shipments(request), pk=pk)
    if request.method == "POST":
        doc_form = ShipmentDocumentForm(request.POST, request.FILES)
        if doc_form.is_valid():
            doc = doc_form.save(commit=False)
            doc.shipment = shipment
            doc.uploaded_by = request.user
            doc.save()
            messages.success(request, "Document uploaded.")
            return redirect("portal:shipment_detail", pk=pk)
    else:
        doc_form = ShipmentDocumentForm()
    return render(request, "portal/shipment_detail.html", {
        "title": shipment.number, "s": shipment, "doc_form": doc_form,
        "lines": shipment.lines.select_related("po_line__product", "po_line__order"),
        "editable": shipment.status in VENDOR_EDITABLE_SHIPMENT,
    })


@vendor_required
def invoice_list(request):
    bills = VendorBill.objects.filter(vendor=request.vendor).exclude(status=VendorBill.VOID) \
        .prefetch_related("lines").select_related("purchase_order")
    return render(request, "portal/invoice_list.html", {"title": "Invoices & Payments", "bills": bills,
                                                        "payments": request.vendor.payments.filter(status="posted")})


@vendor_required
def invoice_submit(request, po_pk):
    po = get_object_or_404(_pos(request), pk=po_pk)
    if not request.user.vendor_contact.can_submit_invoices:
        messages.error(request, "Your account is not allowed to submit invoices.")
        return redirect("portal:po_detail", pk=po.pk)
    lines = list(po.lines.select_related("product"))
    for line in lines:
        line.remaining = max(line.quantity - line.qty_billed, d(0))
    if request.method == "POST":
        number = request.POST.get("vendor_invoice_number", "").strip()
        quantities = {line.pk: d(request.POST.get(f"qty_{line.pk}")) for line in lines}
        try:
            if not number:
                raise ValidationError("Enter your invoice number.")
            with transaction.atomic():
                bill = purchasing.create_bill_from_po(po, number, request.POST.get("date") or datetime.date.today(),
                                                      quantities, portal=True)
                bill.notes = request.POST.get("notes", "")
                bill.save(update_fields=["notes"])
            messages.success(request, f"Invoice {number} submitted for approval.")
            return redirect("portal:invoice_list")
        except ValidationError as exc:
            for m in exc.messages:
                messages.error(request, m)
    return render(request, "portal/invoice_submit.html", {
        "title": f"Submit invoice for {po.number}", "po": po, "lines": lines, "today": datetime.date.today(),
    })


# ----------------------------------------------------------------------------- internal: manage portal users

@internal_required
def contact_list(request):
    return render(request, "portal/contact_list.html", {
        "title": "Vendor Portal Users",
        "contacts": VendorContact.objects.select_related("user", "vendor"),
    })


@internal_required
def contact_create(request):
    form = PortalContactForm(request.POST or None, initial={"vendor": request.GET.get("vendor")})
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        with transaction.atomic():
            user = get_user_model().objects.create_user(
                username=cd["username"], password=cd["password"], email=cd["email"],
                first_name=cd["first_name"], last_name=cd["last_name"],
            )
            VendorContact.objects.create(user=user, vendor=cd["vendor"], title=cd["title"],
                                         can_submit_invoices=cd["can_submit_invoices"])
        messages.success(request, f"Portal user {user.username} created for {cd['vendor']}. "
                                  f"They sign in at the normal login page.")
        return redirect("portal:contact_list")
    return render(request, "core/form.html", {"form": form, "title": "New Vendor Portal User"})


@internal_required
@require_POST
def contact_toggle(request, pk):
    contact = get_object_or_404(VendorContact, pk=pk)
    contact.user.is_active = not contact.user.is_active
    contact.user.save(update_fields=["is_active"])
    messages.success(request, f"{contact.user.username} {'enabled' if contact.user.is_active else 'disabled'}.")
    return redirect("portal:contact_list")
