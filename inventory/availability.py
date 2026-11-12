"""Inventory quantity buckets and availability formulas.

Supply is partitioned so nothing is counted twice: every open PO quantity sits in exactly one of
PO Open (not yet booked) / Booked pending approval / Booked & approved / In transit.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import F, Q, Sum

from core.utils import ZERO

# key, label, group, help text (shown as tooltip / legend)
BUCKETS = [
    ("on_hand", "In Stock", "Stock", "Quantity physically in the warehouse(s)"),
    ("so_quote", "Quotes", "Sales orders", "Quantity on open quotations (not deducted)"),
    ("so_pending", "SO Pending Approval", "Sales orders", "Orders waiting for approval (e.g. credit hold)"),
    ("so_open", "SO Open", "Sales orders", "Approved orders, nothing shipped yet"),
    ("so_partial", "SO Partially Shipped", "Sales orders",
     "Balance to ship on orders shipped but not yet fully invoiced"),
    ("so_backorder", "Backordered", "Sales orders",
     "Balance still open after everything shipped so far was invoiced"),
    ("so_to_invoice", "Shipped Not Invoiced", "Sales orders", "Shipped, waiting to be invoiced (info only)"),
    ("po_new", "PO New", "Purchase orders", "Draft POs not yet sent to the vendor (not counted)"),
    ("po_open", "PO Open", "Purchase orders", "Sent / confirmed PO quantity not yet booked on a shipment"),
    ("po_closed", "PO Closed", "Purchase orders", "Quantity received on received / closed POs (history)"),
    ("booked", "Booked to Ship", "Overseas", "On a planned / booked shipment, awaiting buyer approval"),
    ("booked_approved", "Booked & Approved", "Overseas", "On a planned / booked shipment approved by the buyer"),
    ("in_transit", "In Transit", "Overseas", "Departed: in transit, at port, in customs or cleared"),
    ("open_orders", "Open Orders", "Availability",
     "SO Pending Approval + SO Open + SO Partially Shipped + Backordered"),
    ("ats", "Available to Ship", "Availability", "In Stock − Open Orders"),
    ("in_transit_available", "In-Transit Available", "Availability",
     "In Transit − (Open Orders not covered by In Stock)"),
    ("ats_in_transit", "Avail. Ship + In-Transit", "Availability", "In Stock + In Transit − Open Orders"),
    ("booked_available", "Booked Available", "Availability",
     "(Booked to Ship + Booked & Approved) − (Open Orders not covered by In Stock + In Transit)"),
    ("available_to_sell", "Available to Sell", "Availability",
     "In Stock + PO Open + Booked to Ship + Booked & Approved + In Transit − Open Orders"),
]
BUCKET_KEYS = [b[0] for b in BUCKETS]
DRILLABLE = {"on_hand", "so_quote", "so_pending", "so_open", "so_partial", "so_backorder", "so_to_invoice",
             "po_new", "po_open", "po_closed", "booked", "booked_approved", "in_transit"}
FORMULAS = {"open_orders", "ats", "in_transit_available", "ats_in_transit", "booked_available",
            "available_to_sell"}
# Formula fields that can be set to show 0 instead of negative (per screen, in Settings -> Company)
FLOORABLE = [(k, label) for k, label, _g, _h in BUCKETS if k in FORMULAS and k != "open_orders"]
SCREENS = {
    "inventory": {"title": "Inventory Availability", "setting": "avail_no_negative", "url": "inventory:availability"},
    "sales": {"title": "Sales Availability", "setting": "sales_avail_no_negative",
              "url": "inventory:sales_availability"},
}


def floor_keys(screen):
    from core.models import Company

    return set(getattr(Company.get(), SCREENS[screen]["setting"]) or [])


def apply_floor(buckets, keys):
    """Bucket values with the given formula fields floored at 0 (display only; stored math is unchanged)."""
    values = {k: buckets[k] for k in BUCKET_KEYS}
    for k in keys:
        if values[k] < 0:
            values[k] = ZERO
    return values


@dataclass
class Buckets:
    values: dict = field(default_factory=lambda: defaultdict(lambda: ZERO))

    def __getitem__(self, key):
        return self.values[key]

    def compute(self):
        v = self.values
        v["open_orders"] = v["so_pending"] + v["so_open"] + v["so_partial"] + v["so_backorder"]
        v["ats"] = v["on_hand"] - v["open_orders"]
        shortfall = max(v["open_orders"] - v["on_hand"], ZERO)
        v["in_transit_available"] = v["in_transit"] - shortfall
        v["ats_in_transit"] = v["on_hand"] + v["in_transit"] - v["open_orders"]
        shortfall_after_transit = max(v["open_orders"] - v["on_hand"] - v["in_transit"], ZERO)
        v["booked_available"] = v["booked"] + v["booked_approved"] - shortfall_after_transit
        v["available_to_sell"] = (v["on_hand"] + v["in_transit"] + v["booked_approved"] + v["booked"]
                                  + v["po_open"] - v["open_orders"])
        return self

    def as_list(self):
        return [(k, self.values[k]) for k in BUCKET_KEYS]


def _add(result, rows, key, qty_field="t"):
    for row in rows:
        result[row["product_id"]].values[key] += row[qty_field] or ZERO


def compute(product_ids=None, warehouse_id=None):
    """Return {product_id: Buckets} for the given products (all if None), optionally for one warehouse."""
    from purchasing.models import PurchaseOrder, PurchaseOrderLine, Shipment, ShipmentLine
    from sales.models import SalesOrder, SalesOrderLine

    from .models import StockQuant

    result = defaultdict(Buckets)

    def scope(qs, wh_path, product_path="product_id"):
        if product_ids is not None:
            qs = qs.filter(**{f"{product_path}__in": product_ids})
        if warehouse_id:
            qs = qs.filter(**{wh_path: warehouse_id})
        return qs

    # Stock
    _add(result, scope(StockQuant.objects, "warehouse_id").values("product_id").annotate(t=Sum("quantity")),
         "on_hand")

    # Sales orders by status
    so = scope(SalesOrderLine.objects, "warehouse_id")
    _add(result, so.filter(order__status=SalesOrder.QUOTE).values("product_id").annotate(t=Sum("quantity")),
         "so_quote")
    for key, status in (("so_pending", SalesOrder.PENDING), ("so_open", SalesOrder.CONFIRMED),
                        ("so_partial", SalesOrder.PARTIAL), ("so_backorder", SalesOrder.BACKORDER)):
        _add(result, so.filter(order__status=status).values("product_id")
             .annotate(t=Sum(F("quantity") - F("qty_shipped"))), key)
    _add(result, so.filter(order__status__in=[SalesOrder.PARTIAL, SalesOrder.BACKORDER, SalesOrder.SHIPPED])
         .values("product_id").annotate(t=Sum(F("qty_shipped") - F("qty_invoiced"))), "so_to_invoice")

    # Purchase orders
    po = scope(PurchaseOrderLine.objects, "warehouse_id")
    _add(result, po.filter(order__status=PurchaseOrder.DRAFT).values("product_id").annotate(t=Sum("quantity")),
         "po_new")
    _add(result, po.filter(order__status__in=[PurchaseOrder.RECEIVED, PurchaseOrder.CLOSED])
         .values("product_id").annotate(t=Sum("qty_received")), "po_closed")

    # Shipments (active), attributed to the warehouse of the PO line they will be received into
    active = ShipmentLine.objects.exclude(shipment__status__in=[Shipment.RECEIVED, Shipment.CANCELLED])
    sl = scope(active, "po_line__warehouse_id", "po_line__product_id")
    booked_q = Q(shipment__status__in=Shipment.BOOKED_STATUSES)
    for key, cond in (("booked", booked_q & Q(shipment__approved=False)),
                      ("booked_approved", booked_q & Q(shipment__approved=True)),
                      ("in_transit", Q(shipment__status__in=Shipment.IN_TRANSIT_STATUSES))):
        _add(result, sl.filter(cond).values(product_id=F("po_line__product_id")).annotate(t=Sum("quantity")), key)

    # PO Open = open PO qty not yet on any active shipment (per PO line, so nothing double counts)
    open_lines = po.filter(order__status__in=PurchaseOrder.OPEN_STATUSES).annotate(
        on_ship=Sum("shipment_lines__quantity",
                    filter=~Q(shipment_lines__shipment__status__in=[Shipment.RECEIVED, Shipment.CANCELLED])))
    for line in open_lines.values("product_id", "quantity", "qty_received", "on_ship"):
        unbooked = line["quantity"] - line["qty_received"] - (line["on_ship"] or ZERO)
        if unbooked > 0:
            result[line["product_id"]].values["po_open"] += unbooked

    for b in result.values():
        b.compute()
    if product_ids is not None:
        for pid in product_ids:
            result[pid].compute()
    return result


def for_product(product, warehouse_id=None):
    return compute([product.pk], warehouse_id)[product.pk]


def detail_rows(product, bucket, warehouse_id=None):
    """Documents behind a bucket for one product: list of dicts (doc, number, url, party, date, status, qty)."""
    from purchasing.models import PurchaseOrder, PurchaseOrderLine, Shipment, ShipmentLine
    from sales.models import SalesOrder, SalesOrderLine

    from .models import StockQuant

    rows = []

    def wh(qs, path):
        return qs.filter(**{path: warehouse_id}) if warehouse_id else qs

    if bucket == "on_hand":
        for q in wh(StockQuant.objects.filter(product=product, quantity__gt=0), "warehouse_id") \
                .select_related("warehouse"):
            rows.append({"number": q.warehouse.code, "url": q.warehouse.get_absolute_url(), "party": q.warehouse.name,
                         "date": None, "status": "In stock", "warehouse": q.warehouse.code, "qty": q.quantity})
        return rows

    def remaining(ln):
        return ln.quantity - ln.qty_shipped

    so_map = {"so_quote": ([SalesOrder.QUOTE], lambda ln: ln.quantity),
              "so_pending": ([SalesOrder.PENDING], remaining),
              "so_open": ([SalesOrder.CONFIRMED], remaining),
              "so_partial": ([SalesOrder.PARTIAL], remaining),
              "so_backorder": ([SalesOrder.BACKORDER], remaining),
              "so_to_invoice": ([SalesOrder.PARTIAL, SalesOrder.BACKORDER, SalesOrder.SHIPPED],
                                lambda ln: ln.qty_shipped - ln.qty_invoiced)}
    if bucket in so_map:
        statuses, qty = so_map[bucket]
        for ln in wh(SalesOrderLine.objects.filter(product=product, order__status__in=statuses), "warehouse_id") \
                .select_related("order__customer", "warehouse").order_by("order__requested_date", "order__order_date"):
            if qty(ln) > 0:
                o = ln.order
                rows.append({"number": o.number, "url": o.get_absolute_url(), "party": o.customer.name,
                             "date": o.requested_date or o.order_date, "status": o.get_status_display(),
                             "warehouse": ln.warehouse.code, "qty": qty(ln)})
        return rows

    if bucket in ("po_new", "po_open", "po_closed"):
        statuses = {"po_new": [PurchaseOrder.DRAFT], "po_open": list(PurchaseOrder.OPEN_STATUSES),
                    "po_closed": [PurchaseOrder.RECEIVED, PurchaseOrder.CLOSED]}[bucket]
        for ln in wh(PurchaseOrderLine.objects.filter(product=product, order__status__in=statuses),
                     "warehouse_id").select_related("order__vendor", "warehouse").order_by("order__expected_date"):
            if bucket == "po_new":
                q = ln.quantity
            elif bucket == "po_closed":
                q = ln.qty_received
            else:
                q = ln.qty_unshipped
            if q > 0:
                o = ln.order
                rows.append({"number": o.number, "url": o.get_absolute_url(), "party": o.vendor.name,
                             "date": o.expected_date or o.order_date, "status": o.get_status_display(),
                             "warehouse": ln.warehouse.code, "qty": q})
        return rows

    if bucket in ("booked", "booked_approved", "in_transit"):
        qs = ShipmentLine.objects.filter(po_line__product=product)
        if bucket == "in_transit":
            qs = qs.filter(shipment__status__in=Shipment.IN_TRANSIT_STATUSES)
        else:
            qs = qs.filter(shipment__status__in=Shipment.BOOKED_STATUSES,
                           shipment__approved=(bucket == "booked_approved"))
        for ln in wh(qs, "po_line__warehouse_id").select_related("shipment__vendor", "po_line__order",
                                                                 "po_line__warehouse").order_by("shipment__eta"):
            s = ln.shipment
            rows.append({"number": s.number, "url": s.get_absolute_url(),
                         "party": f"{s.vendor or ''} · {ln.po_line.order.number}", "date": s.eta,
                         "status": s.get_status_display(), "warehouse": ln.po_line.warehouse.code,
                         "qty": ln.quantity})
        return rows
    return rows


__all__ = ["BUCKETS", "Buckets", "compute", "for_product", "detail_rows", "Decimal"]
