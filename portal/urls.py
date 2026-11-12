from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("orders/", views.po_list, name="po_list"),
    path("orders/<int:pk>/", views.po_detail, name="po_detail"),
    path("orders/<int:pk>/confirm/", views.po_confirm, name="po_confirm"),
    path("orders/<int:po_pk>/invoice/", views.invoice_submit, name="invoice_submit"),
    path("shipments/", views.shipment_list, name="shipment_list"),
    path("shipments/new/", views.shipment_form, name="shipment_create"),
    path("shipments/<int:pk>/", views.shipment_detail, name="shipment_detail"),
    path("shipments/<int:pk>/edit/", views.shipment_form, name="shipment_update"),
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("users/", views.contact_list, name="contact_list"),
    path("users/new/", views.contact_create, name="contact_create"),
    path("users/<int:pk>/toggle/", views.contact_toggle, name="contact_toggle"),
]
