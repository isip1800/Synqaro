from django.urls import path

from core.crud import action_view, crud_urls
from core.forms import line_formset

from . import services, views
from .models import StockAdjustment, StockAdjustmentLine, StockTransfer, StockTransferLine, Warehouse

app_name = "inventory"

urlpatterns = [
    path("availability/", views.availability_view, name="availability"),
    path("sales-availability/", views.sales_availability_view, name="sales_availability"),
    path("availability/definitions/", views.availability_definitions, name="availability_definitions"),
    path("availability/export.xlsx", views.availability_export, name="availability_export"),
    path("availability/<int:pk>/<str:bucket>/", views.availability_detail, name="availability_detail"),
    path("stock/", views.stock, name="stock"),
    path("reorder/", views.reorder, name="reorder"),
    path("moves/", views.MoveList.as_view(), name="moves"),
    *crud_urls(Warehouse, prefix="warehouses/",
               fields=["code", "name", "address", "city", "state", "postal_code", "country", "is_active"],
               columns=(("Code", "code"), ("Name", "name"), ("City", "city"), ("Country", "country"),
                        ("Active", "is_active")),
               detail_view=views.WarehouseDetail),
    *crud_urls(StockAdjustment, prefix="adjustments/", fields=["warehouse", "date", "reason", "notes"],
               columns=(("Number", "number"), ("Date", "date"), ("Warehouse", "warehouse"), ("Reason", "reason"),
                        ("Status", "status")),
               search_fields=("number", "notes"), select_related=("warehouse",),
               detail_view=views.AdjustmentDetail,
               formset_class=line_formset(StockAdjustment, StockAdjustmentLine, ["product", "quantity",
                                                                                 "unit_cost"])),
    path("adjustments/<int:pk>/post/", action_view(StockAdjustment, services.post_adjustment,
                                                   "Adjustment {obj} posted."), name="adjustment_post"),
    *crud_urls(StockTransfer, prefix="transfers/", fields=["from_warehouse", "to_warehouse", "date", "notes"],
               columns=(("Number", "number"), ("Date", "date"), ("From", "from_warehouse"),
                        ("To", "to_warehouse"), ("Status", "status")),
               search_fields=("number",), select_related=("from_warehouse", "to_warehouse"),
               detail_view=views.TransferDetail,
               formset_class=line_formset(StockTransfer, StockTransferLine, ["product", "quantity"])),
    path("transfers/<int:pk>/post/", action_view(StockTransfer, services.post_transfer, "Transfer {obj} posted."),
         name="transfer_post"),
]
