from django.urls import path

from core.crud import crud_urls
from core.forms import line_formset

from . import views
from .models import PriceList, PriceListItem, Product, UnitOfMeasure, VendorProduct

app_name = "products"

urlpatterns = [
    path("items/", views.ProductList.as_view(), name="product_list"),
    path("items/export.xlsx", views.product_export, name="product_export"),
    path("items/new/", views.product_form, name="product_create"),
    path("items/<int:pk>/", views.ProductDetail.as_view(), name="product_detail"),
    path("items/<int:pk>/edit/", views.product_form, name="product_update"),
    path("items/<int:pk>/images/upload/", views.image_upload, name="image_upload"),
    path("items/<int:pk>/generate-upc/", views.generate_upc, name="generate_upc"),
    path("images/<int:pk>/", views.image, name="image"),
    path("images/<int:pk>/thumb/", views.image_thumb, name="image_thumb"),
    path("images/<int:pk>/primary/", views.image_primary, name="image_primary"),
    path("images/<int:pk>/delete/", views.image_delete, name="image_delete"),
    # Product setup
    path("setup/", views.setup_index, name="setup"),
    path("setup/<str:kind>/", views.lookup_list, name="lookup_list"),
    path("setup/<str:kind>/new/", views.lookup_form, name="lookup_create"),
    path("setup/<str:kind>/<int:pk>/edit/", views.lookup_form, name="lookup_update"),
    path("setup/<str:kind>/<int:pk>/delete/", views.lookup_delete, name="lookup_delete"),
    path("item-classes/", views.itemclass_list, name="itemclass_list"),
    path("item-classes/new/", views.itemclass_form, name="itemclass_create"),
    path("item-classes/<int:pk>/edit/", views.itemclass_form, name="itemclass_update"),
    path("hts-codes/", views.HTSList.as_view(), name="htscode_list"),
    path("hts-codes/new/", views.hts_form, name="htscode_create"),
    path("hts-codes/<int:pk>/edit/", views.hts_form, name="htscode_update"),
    path("hts-codes/<int:pk>/delete/", views.hts_delete, name="htscode_delete"),
    path("hts-codes/export.xlsx", views.hts_export, name="htscode_export"),
    path("hts-codes/import/", views.hts_import, name="htscode_import"),
    *crud_urls(UnitOfMeasure, prefix="uom/", fields=["code", "name"],
               columns=(("Code", "code"), ("Name", "name")), detail=False),
    *crud_urls(VendorProduct, prefix="vendor-prices/",
               fields=["vendor", "product", "vendor_sku", "unit_cost", "currency", "moq", "lead_time_days"],
               columns=(("Vendor", "vendor"), ("Product", "product"), ("Vendor SKU", "vendor_sku"),
                        ("Cost", "unit_cost"), ("Currency", "currency"), ("MOQ", "moq")),
               search_fields=("vendor__name", "product__sku", "vendor_sku"), detail=False,
               select_related=("vendor", "product", "currency")),
    *crud_urls(PriceList, prefix="price-lists/", fields=["name", "description", "is_active"],
               columns=(("Name", "name"), ("Description", "description"), ("Active", "is_active")),
               detail_view=views.PriceListDetail,
               formset_class=line_formset(PriceList, PriceListItem, ["product", "min_qty", "price"], extra=5)),
]

__all__ = ["Product"]
