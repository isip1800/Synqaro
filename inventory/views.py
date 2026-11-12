from django.db.models import F, Sum
from django.shortcuts import render

from core.crud import ErpDetailView, ErpListView
from core.mixins import internal_required
from core.utils import ZERO
from products.models import Category, Product

from .models import StockAdjustment, StockMove, StockQuant, StockTransfer, Warehouse


@internal_required
def stock(request):
    warehouses = list(Warehouse.objects.filter(is_active=True))
    wh = request.GET.get("warehouse")
    cat = request.GET.get("category")
    q = request.GET.get("q", "").strip()
    products = Product.objects.filter(is_active=True).select_related("category")
    if cat:
        products = products.filter(category_id=cat)
    if q:
        products = products.filter(sku__icontains=q) | products.filter(name__icontains=q)
    quants = StockQuant.objects.all()
    if wh:
        quants = quants.filter(warehouse_id=wh)
    by_product = {}
    for row in quants.values("product_id", "warehouse_id", "quantity"):
        by_product.setdefault(row["product_id"], {})[row["warehouse_id"]] = row["quantity"]
    rows, total_value = [], ZERO
    shown_wh = [w for w in warehouses if not wh or str(w.pk) == wh]
    for p in products:
        per_wh = by_product.get(p.pk, {})
        total = sum(per_wh.values(), ZERO)
        if total == 0 and request.GET.get("nonzero", "1") == "1":
            continue
        value = total * p.avg_cost
        total_value += value
        rows.append({"product": p, "per_wh": [per_wh.get(w.pk, ZERO) for w in shown_wh], "total": total,
                     "value": value})
    return render(request, "inventory/stock.html", {
        "title": "Stock on Hand & Valuation", "rows": rows, "warehouses": shown_wh, "all_warehouses": warehouses,
        "categories": Category.objects.all(), "total_value": total_value, "params": request.GET,
    })


@internal_required
def reorder(request):
    rows = []
    for p in Product.objects.filter(is_active=True).select_related("preferred_vendor"):
        on_hand, on_order, reserved = p.qty_on_hand, p.qty_on_order, p.qty_reserved
        projected = on_hand + on_order - reserved
        if p.reorder_point and projected <= p.reorder_point:
            suggest = max(p.reorder_qty, p.reorder_point - projected)
            rows.append({"product": p, "on_hand": on_hand, "on_order": on_order, "reserved": reserved,
                         "projected": projected, "suggest": suggest})
    rows.sort(key=lambda r: (str(r["product"].preferred_vendor or ""), r["product"].sku))
    return render(request, "inventory/reorder.html", {"title": "Reorder Report", "rows": rows})


class MoveList(ErpListView):
    model = StockMove
    title = "Stock Moves"
    columns = (("Date", "date"), ("Product", "product"), ("Warehouse", "warehouse"), ("Type", "move_type"),
               ("Reference", "reference"), ("Qty", "quantity"), ("Unit cost", "unit_cost"))
    search_fields = ("product__sku", "product__name", "reference")
    select_related = ("product", "warehouse")
    paginate_by = 100

    def get_filters(self):
        return (("move_type", "Type", StockMove.TYPES),
                ("warehouse", "Warehouse", [(w.pk, w.code) for w in Warehouse.objects.all()]))


class WarehouseDetail(ErpDetailView):
    model = Warehouse
    template_name = "inventory/warehouse_detail.html"
    fields = (("Code", "code"), ("Name", "name"), ("Address", "full_address"), ("Active", "is_active"))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        quants = self.object.quants.filter(quantity__gt=0).select_related("product")
        ctx["quants"] = quants
        ctx["value"] = quants.aggregate(v=Sum(F("quantity") * F("product__avg_cost")))["v"] or ZERO
        return ctx


class AdjustmentDetail(ErpDetailView):
    model = StockAdjustment
    template_name = "inventory/adjustment_detail.html"


class TransferDetail(ErpDetailView):
    model = StockTransfer
    template_name = "inventory/transfer_detail.html"
