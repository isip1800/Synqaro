from django.db.models import Sum

from core.crud import ErpDetailView, ErpListView
from core.utils import ZERO

from .models import Category, PriceList, Product


class ProductList(ErpListView):
    model = Product
    template_name = "products/product_list.html"
    search_fields = ("sku", "name", "hs_code", "barcode")
    ordering = ["sku"]
    select_related = ("category", "preferred_vendor")

    def get_queryset(self):
        return super().get_queryset().annotate(on_hand=Sum("quants__quantity"))

    def get_filters(self):
        return (
            ("category", "Category", [(c.pk, str(c)) for c in Category.objects.all()]),
            ("is_active", "Active", [("True", "Active"), ("False", "Inactive")]),
        )


class ProductDetail(ErpDetailView):
    model = Product
    template_name = "products/product_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        p = self.object
        ctx.update(
            quants=p.quants.select_related("warehouse"),
            moves=p.moves.select_related("warehouse")[:25],
            vendor_prices=p.vendor_products.select_related("vendor", "currency"),
            price_tiers=p.price_list_items.select_related("price_list"),
            open_po_lines=p.po_lines.filter(order__status__in=["sent", "confirmed", "partial"])
            .select_related("order", "order__vendor"),
        )
        return ctx


class PriceListDetail(ErpDetailView):
    model = PriceList
    template_name = "products/pricelist_detail.html"
    fields = (("Name", "name"), ("Description", "description"), ("Active", "is_active"))


__all__ = ["ProductList", "ProductDetail", "PriceListDetail", "ZERO"]
