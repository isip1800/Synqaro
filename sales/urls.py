from django.urls import path

from core.crud import crud_urls

from . import views
from .forms import CUSTOMER_FIELDS
from .models import Customer

app_name = "sales"

urlpatterns = [
    *crud_urls(Customer, prefix="customers/", fields=CUSTOMER_FIELDS, columns=(), list_view=views.CustomerList,
               detail_view=views.CustomerDetail),
    path("customers/<int:pk>/statement/", views.customer_statement, name="customer_statement"),
    path("orders/", views.OrderList.as_view(), name="salesorder_list"),
    path("orders/new/", views.OrderCreate.as_view(), name="salesorder_create"),
    path("orders/<int:pk>/", views.OrderDetail.as_view(), name="salesorder_detail"),
    path("orders/<int:pk>/edit/", views.OrderUpdate.as_view(), name="salesorder_update"),
    path("orders/<int:pk>/confirm/", views.order_confirm, name="order_confirm"),
    path("orders/<int:pk>/confirm-override/", views.order_confirm_override, name="order_confirm_override"),
    path("orders/<int:pk>/cancel/", views.order_cancel, name="order_cancel"),
    path("orders/<int:pk>/ship/", views.order_ship, name="order_ship"),
    path("orders/<int:pk>/invoice/", views.order_invoice, name="order_invoice"),
    path("deliveries/", views.DeliveryList.as_view(), name="delivery_list"),
    path("deliveries/<int:pk>/", views.DeliveryDetail.as_view(), name="delivery_detail"),
]
