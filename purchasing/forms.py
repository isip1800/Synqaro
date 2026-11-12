from django import forms

from core.forms import LineWarehouseForm, StyledModelForm, line_formset, model_form

from .models import LandedCost, PurchaseOrder, PurchaseOrderLine, Shipment, ShipmentDocument, ShipmentLine

PO_FIELDS = ["vendor", "order_date", "expected_date", "currency", "exchange_rate", "incoterm", "warehouse",
             "payment_terms", "vendor_reference", "notes"]
PurchaseOrderForm = model_form(PurchaseOrder, PO_FIELDS)
POLineFormSet = line_formset(PurchaseOrder, PurchaseOrderLine,
                             ["product", "warehouse", "description", "quantity", "unit_price", "fs_cost_usd"],
                             extra=4,
                             form=LineWarehouseForm)

SHIPMENT_FIELDS = [
    "vendor", "forwarder", "mode", "incoterm", "status", "carrier", "vessel_voyage", "origin_port",
    "destination_port", "bill_of_lading", "container_numbers", "etd", "eta", "actual_arrival",
    "customs_entry_number", "customs_cleared_on", "warehouse", "allocation_method", "notes",
]


class ShipmentForm(StyledModelForm):
    class Meta:
        model = Shipment
        fields = SHIPMENT_FIELDS

    def clean_status(self):
        status = self.cleaned_data["status"]
        if status == Shipment.RECEIVED and (not self.instance.pk or self.instance.status != Shipment.RECEIVED):
            raise forms.ValidationError("Use the 'Receive into warehouse' button to receive a shipment.")
        return status


class ShipmentLineForm(StyledModelForm):
    class Meta:
        model = ShipmentLine
        fields = ["po_line", "quantity", "cartons"]

    def __init__(self, *args, vendor=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = PurchaseOrderLine.objects.filter(order__status__in=PurchaseOrder.OPEN_STATUSES) \
            .select_related("order", "product")
        if vendor is not None:
            qs = qs.filter(order__vendor=vendor)
        if self.instance.pk:
            qs = qs | PurchaseOrderLine.objects.filter(pk=self.instance.po_line_id)
        self.fields["po_line"].queryset = qs.distinct()
        self.fields["po_line"].label = "PO line"


ShipmentLineFormSet = line_formset(Shipment, ShipmentLine, ["po_line", "quantity", "cartons"], extra=4,
                                   form=ShipmentLineForm)


class LandedCostForm(StyledModelForm):
    class Meta:
        model = LandedCost
        fields = ["cost_type", "description", "vendor", "amount"]


class ShipmentDocumentForm(StyledModelForm):
    class Meta:
        model = ShipmentDocument
        fields = ["doc_type", "file", "description"]
