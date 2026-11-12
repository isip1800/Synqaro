from django.urls import path

from core.crud import crud_urls

from . import views
from .models import GoodsReceipt, Vendor

app_name = "purchasing"

VENDOR_FIELDS = ["code", "name", "vendor_type", "contact_name", "email", "phone", "website", "address", "city",
                 "state", "postal_code", "country", "tax_id", "currency", "payment_terms_days", "default_incoterm",
                 "bank_details", "notes", "is_active"]

urlpatterns = [
    *crud_urls(Vendor, prefix="vendors/", fields=VENDOR_FIELDS, columns=(), list_view=views.VendorList,
               detail_view=views.VendorDetail),
    path("orders/", views.POList.as_view(), name="purchaseorder_list"),
    path("orders/new/", views.POCreate.as_view(), name="purchaseorder_create"),
    path("orders/<int:pk>/", views.PODetail.as_view(), name="purchaseorder_detail"),
    path("orders/<int:pk>/edit/", views.POUpdate.as_view(), name="purchaseorder_update"),
    path("orders/<int:pk>/send/", views.po_send, name="po_send"),
    path("orders/<int:pk>/confirm/", views.po_confirm, name="po_confirm"),
    path("orders/<int:pk>/cancel/", views.po_cancel, name="po_cancel"),
    path("orders/<int:pk>/close/", views.po_close, name="po_close"),
    path("orders/<int:pk>/receive/", views.po_receive, name="po_receive"),
    path("orders/<int:pk>/create-shipment/", views.po_create_shipment, name="po_create_shipment"),
    path("orders/<int:pk>/create-bill/", views.po_create_bill, name="po_create_bill"),
    path("shipments/", views.ShipmentList.as_view(), name="shipment_list"),
    path("shipments/new/", views.ShipmentCreate.as_view(), name="shipment_create"),
    path("shipments/<int:pk>/", views.ShipmentDetail.as_view(), name="shipment_detail"),
    path("shipments/<int:pk>/edit/", views.ShipmentUpdate.as_view(), name="shipment_update"),
    path("shipments/<int:pk>/receive/", views.shipment_receive, name="shipment_receive"),
    path("shipments/<int:pk>/estimate-duty/", views.shipment_estimate_duty, name="shipment_estimate_duty"),
    path("shipments/<int:pk>/landed-costs/add/", views.shipment_add_landed_cost, name="shipment_add_landed_cost"),
    path("shipments/<int:pk>/documents/add/", views.shipment_upload_document, name="shipment_upload_document"),
    path("landed-costs/", views.LandedCostList.as_view(), name="landedcost_list"),
    path("landed-costs/<int:pk>/delete/", views.landed_cost_delete, name="landed_cost_delete"),
    path("landed-costs/<int:pk>/capitalize/", views.landed_cost_capitalize, name="landed_cost_capitalize"),
    path("receipts/", views.ReceiptList.as_view(), name="goodsreceipt_list"),
    path("receipts/<int:pk>/", views.ReceiptDetail.as_view(), name="goodsreceipt_detail"),
]

__all__ = ["GoodsReceipt"]
