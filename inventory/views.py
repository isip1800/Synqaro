from django.db.models import F, Sum
from django.shortcuts import render

from core.crud import ErpDetailView, ErpListView
from core.mixins import internal_required
from core.utils import ZERO
from products.models import ItemClass, Product, ProductWarehouse

from .models import StockAdjustment, StockMove, StockQuant, StockTransfer, Warehouse


@internal_required
def stock(request):
    warehouses = list(Warehouse.objects.filter(is_active=True))
    wh = request.GET.get("warehouse")
    cat = request.GET.get("tier1")
    q = request.GET.get("q", "").strip()
    products = Product.objects.filter(is_active=True).select_related("item_class__parent__parent")
    if cat:
        products = products.filter(item_class__parent__parent_id=cat)
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
        "categories": ItemClass.objects.filter(level=1), "total_value": total_value, "params": request.GET,
    })


@internal_required
def reorder(request):
    """Projected stock vs reorder point: per warehouse where the item has warehouse settings, else company-wide."""
    from django.db.models import F, Sum

    from purchasing.models import PurchaseOrder, PurchaseOrderLine
    from sales.models import SalesOrder, SalesOrderLine

    def by_wh(qs, qty_expr, wh_field):
        out = {}
        for row in qs.values("product_id", wh_field).annotate(t=Sum(qty_expr)):
            out[(row["product_id"], row[wh_field])] = row["t"] or ZERO
        return out

    on_hand = {(q.product_id, q.warehouse_id): q.quantity for q in StockQuant.objects.all()}
    on_order = by_wh(PurchaseOrderLine.objects.filter(order__status__in=PurchaseOrder.OPEN_STATUSES + ("draft",)),
                     F("quantity") - F("qty_received"), "order__warehouse_id")
    reserved = by_wh(SalesOrderLine.objects.filter(order__status__in=[SalesOrder.CONFIRMED, SalesOrder.PARTIAL]),
                     F("quantity") - F("qty_shipped"), "order__warehouse_id")
    wh_filter = request.GET.get("warehouse")
    rows = []
    settings = ProductWarehouse.objects.select_related("product__preferred_vendor", "warehouse") \
        .filter(product__is_active=True)
    with_settings = set()
    for s in settings:
        with_settings.add(s.product_id)
        if wh_filter and str(s.warehouse_id) != wh_filter:
            continue
        key = (s.product_id, s.warehouse_id)
        oh, oo, rs = on_hand.get(key, ZERO), on_order.get(key, ZERO), reserved.get(key, ZERO)
        projected = oh + oo - rs
        if s.reorder_point and projected <= s.reorder_point:
            suggest = max(s.reorder_qty, s.reorder_point - projected)
            if s.max_qty:
                suggest = min(suggest, max(s.max_qty - projected, ZERO))
            rows.append({"product": s.product, "warehouse": s.warehouse, "on_hand": oh, "on_order": oo,
                         "reserved": rs, "projected": projected, "reorder_point": s.reorder_point,
                         "suggest": suggest})
    if not wh_filter:
        for p in Product.objects.filter(is_active=True, reorder_point__gt=0).exclude(pk__in=with_settings) \
                .select_related("preferred_vendor"):
            oh, oo, rs = p.qty_on_hand, p.qty_on_order, p.qty_reserved
            projected = oh + oo - rs
            if projected <= p.reorder_point:
                rows.append({"product": p, "warehouse": None, "on_hand": oh, "on_order": oo, "reserved": rs,
                             "projected": projected, "reorder_point": p.reorder_point,
                             "suggest": max(p.reorder_qty, p.reorder_point - projected)})
    rows.sort(key=lambda r: (str(r["product"].preferred_vendor or ""), r["product"].sku))
    return render(request, "inventory/reorder.html", {"title": "Reorder Report", "rows": rows,
                                                      "warehouses": Warehouse.objects.filter(is_active=True),
                                                      "wh_filter": wh_filter or ""})


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
