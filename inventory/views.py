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
                     F("quantity") - F("qty_received"), "warehouse_id")
    reserved = by_wh(SalesOrderLine.objects.filter(order__status__in=SalesOrder.RESERVING_STATUSES),
                     F("quantity") - F("qty_shipped"), "warehouse_id")
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


# ----------------------------------------------------------------------------- quantity buckets / availability

def _availability_rows(request):
    from django.db.models import Q

    from . import availability

    g = request.GET
    products = Product.objects.filter(is_active=True).select_related("item_class__parent__parent", "brand", "season") \
        .prefetch_related("images")
    if g.get("q"):
        q = g["q"].strip()
        products = products.filter(Q(sku__icontains=q) | Q(name__icontains=q) | Q(barcode__icontains=q))
    if g.get("tier1"):
        products = products.filter(item_class__parent__parent_id=g["tier1"])
    if g.get("item_class"):
        products = products.filter(item_class_id=g["item_class"])
    if g.get("brand"):
        products = products.filter(brand_id=g["brand"])
    if g.get("season"):
        products = products.filter(season_id=g["season"])
    products = list(products.order_by("sku"))
    wh = g.get("warehouse") or None
    data = availability.compute([p.pk for p in products], wh)
    rows = []
    for p in products:
        b = data[p.pk]
        if g.get("show") == "short" and b["ats"] >= 0:
            continue
        if g.get("show", "active") == "active" and not any(b[k] for k in availability.BUCKET_KEYS):
            continue
        rows.append((p, b))
    return rows, wh


@internal_required
def availability_view(request):
    from itertools import groupby

    from products.models import LookupValue

    from . import availability

    rows, wh = _availability_rows(request)
    groups = [(g, len(list(items))) for g, items in groupby(availability.BUCKETS, key=lambda b: b[2])]
    cols, prev = [], None
    for key, label, group, help_text in availability.BUCKETS:
        cols.append({"key": key, "label": label, "help": help_text, "start": group != prev,
                     "formula": key in availability.FORMULAS, "drill": key in availability.DRILLABLE})
        prev = group
    table = [(p, [(c, b[c["key"]]) for c in cols]) for p, b in rows]
    totals = [(c, sum((b[c["key"]] for _, b in rows), ZERO)) for c in cols]
    return render(request, "inventory/availability.html", {
        "title": "Inventory Availability", "table": table, "cols": cols, "groups": groups,
        "totals": totals,
        "warehouses": Warehouse.objects.filter(is_active=True), "params": request.GET, "wh": wh or "",
        "tier1": ItemClass.objects.filter(level=1),
        "tier3": ItemClass.objects.filter(level=3).select_related("parent__parent"),
        "brands": LookupValue.objects.filter(kind="brand", is_active=True),
        "seasons": LookupValue.objects.filter(kind="season", is_active=True),
        "query": request.GET.urlencode(),
    })


@internal_required
def availability_detail(request, pk, bucket):
    from django.http import Http404
    from django.shortcuts import get_object_or_404

    from . import availability

    if bucket not in availability.DRILLABLE:
        raise Http404
    product = get_object_or_404(Product, pk=pk)
    wh = request.GET.get("warehouse") or None
    meta = {b[0]: b for b in availability.BUCKETS}[bucket]
    rows = availability.detail_rows(product, bucket, wh)
    return render(request, "inventory/availability_detail.html", {
        "title": f"{product.sku} — {meta[1]}", "product": product, "bucket": meta, "rows": rows,
        "total": sum((r["qty"] for r in rows), ZERO),
        "warehouse": Warehouse.objects.filter(pk=wh).first() if wh else None,
    })


@internal_required
def availability_export(request):
    import datetime
    import io

    import xlsxwriter
    from django.http import HttpResponse

    from products.services import _sanitize_xlsx

    from . import availability

    rows, wh = _availability_rows(request)
    out = io.BytesIO()
    wb = xlsxwriter.Workbook(out, {"in_memory": False})
    ws = wb.add_worksheet("Availability")
    grp = wb.add_format({"bold": True, "align": "center", "bg_color": "#D9E1F2", "border": 1})
    head = wb.add_format({"bold": True, "bg_color": "#E8ECF2", "border": 1, "text_wrap": True, "valign": "top"})
    num = wb.add_format({"num_format": "#,##0;[Red]-#,##0", "valign": "vcenter"})
    bold = wb.add_format({"num_format": "#,##0;[Red]-#,##0", "bold": True, "valign": "vcenter"})
    txt = wb.add_format({"valign": "vcenter", "text_wrap": True})
    fixed = ["Image", "SKU", "Description", "Item Class"]
    widths = [12, 14, 30, 26]
    col = len(fixed)
    from itertools import groupby

    for g, items in groupby(availability.BUCKETS, key=lambda b: b[2]):
        items = list(items)
        if len(items) > 1:
            ws.merge_range(0, col, 0, col + len(items) - 1, g, grp)
        else:
            ws.write(0, col, g, grp)
        col += len(items)
    for c, (title, w) in enumerate(zip(fixed, widths)):
        ws.write(1, c, title, head)
        ws.set_column(c, c, w)
    for i, (key, label, _g, help_text) in enumerate(availability.BUCKETS):
        c = len(fixed) + i
        ws.write(1, c, label, head)
        ws.write_comment(1, c, help_text)
        ws.set_column(c, c, 11)
    ws.set_row(1, 42)
    ws.freeze_panes(2, 2)
    for r, (p, b) in enumerate(rows, start=2):
        ws.set_row(r, 60)
        img = p.primary_image
        if img:
            ws.insert_image(r, 0, f"{p.sku}.jpg", {"image_data": io.BytesIO(bytes(img.thumbnail)),
                                                   "x_scale": 0.31, "y_scale": 0.31, "x_offset": 3, "y_offset": 3,
                                                   "object_position": 1})
        ws.write(r, 1, p.sku, txt)
        ws.write(r, 2, p.name, txt)
        ws.write(r, 3, p.item_class.full_code if p.item_class else "", txt)
        for i, key in enumerate(availability.BUCKET_KEYS):
            ws.write_number(r, len(fixed) + i, float(b[key]), bold if key in availability.FORMULAS else num)
    total_row = len(rows) + 2
    ws.write(total_row, 1, "TOTAL", head)
    for i, key in enumerate(availability.BUCKET_KEYS):
        c = len(fixed) + i
        ws.write_number(total_row, c, float(sum((b[key] for _, b in rows), ZERO)), bold)
    wb.close()
    resp = HttpResponse(_sanitize_xlsx(out.getvalue()),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    suffix = f"-{Warehouse.objects.get(pk=wh).code}" if wh else ""
    resp["Content-Disposition"] = f'attachment; filename="availability{suffix}-{datetime.date.today():%Y%m%d}.xlsx"'
    return resp
