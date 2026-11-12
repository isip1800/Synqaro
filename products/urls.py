from core.crud import crud_urls
from core.forms import line_formset

from . import views
from .models import Category, PriceList, PriceListItem, Product, UnitOfMeasure, VendorProduct

app_name = "products"

PRODUCT_FIELDS = [
    "sku", "name", "description", "category", "uom", "barcode", "hs_code", "country_of_origin", "duty_rate",
    "weight_kg", "volume_cbm", "units_per_carton", "list_price", "standard_cost", "preferred_vendor",
    "reorder_point", "reorder_qty", "lead_time_days", "is_active",
]

urlpatterns = [
    *crud_urls(Product, prefix="items/", fields=PRODUCT_FIELDS, columns=(), list_view=views.ProductList,
               detail_view=views.ProductDetail),
    *crud_urls(Category, prefix="categories/", fields=["name", "parent"],
               columns=(("Category", "__str__"), ("Parent", "parent")), search_fields=("name",), detail=False),
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
