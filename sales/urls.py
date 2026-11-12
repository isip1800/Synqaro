from django.urls import path

from core.crud import crud_urls

from . import views
from .forms import CUSTOMER_FIELDS, BuyerFormSet
from .models import Customer

app_name = "sales"

urlpatterns = [
    *crud_urls(Customer, prefix="customers/", fields=CUSTOMER_FIELDS, columns=(), list_view=views.CustomerList,
               detail_view=views.CustomerDetail, formset_class=BuyerFormSet),
    path("customers/<int:pk>/statement/", views.customer_statement, name="customer_statement"),
    path("orders/", views.OrderList.as_view(), name="salesorder_list"),
    path("orders/new/", views.OrderCreate.as_view(), name="salesorder_create"),
    path("orders/<int:pk>/", views.OrderDetail.as_view(), name="salesorder_detail"),
    path("orders/<int:pk>/edit/", views.OrderUpdate.as_view(), name="salesorder_update"),
    path("orders/<int:pk>/confirm/", views.order_confirm, name="order_confirm"),
    path("orders/<int:pk>/approve/", views.order_approve, name="order_approve"),
    path("orders/<int:pk>/reject/", views.order_reject, name="order_reject"),
    path("orders/<int:pk>/cancel/", views.order_cancel, name="order_cancel"),
    path("orders/<int:pk>/release/", views.order_release, name="order_release"),
    path("orders/<int:pk>/recall/", views.order_recall, name="order_recall"),
    path("orders/<int:pk>/hold/", views.order_hold, name="order_hold"),
    path("orders/<int:pk>/unhold/", views.order_unhold, name="order_unhold"),
    path("orders/<int:pk>/reopen/", views.order_reopen, name="order_reopen"),
    path("customers/<int:customer_pk>/locations/new/", views.location_form, name="location_create"),
    path("customers/<int:customer_pk>/locations/export.xlsx", views.locations_export, name="locations_export"),
    path("customers/<int:customer_pk>/locations/import/", views.locations_import, name="locations_import"),
    path("locations/<int:pk>/edit/", views.location_form, name="location_update"),
    path("locations/<int:pk>/delete/", views.location_delete, name="location_delete"),
    path("orders/<int:pk>/ship/", views.order_ship, name="order_ship"),
    path("orders/<int:pk>/invoice/", views.order_invoice, name="order_invoice"),
    path("deliveries/", views.DeliveryList.as_view(), name="delivery_list"),
    path("deliveries/<int:pk>/", views.DeliveryDetail.as_view(), name="delivery_detail"),
]
