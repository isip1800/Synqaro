from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.testing import TODAY, Fixture
from inventory import availability
from inventory.models import StockAdjustment, StockAdjustmentLine, Warehouse
from inventory.services import post_adjustment
from portal.models import VendorContact
from purchasing import services as pur
from purchasing.models import PurchaseOrder, PurchaseOrderLine, Shipment, ShipmentLine
from sales import services as sal
from sales.models import SalesOrder, SalesOrderLine


class AvailabilityTests(TestCase):
    """Scenario for product A (all quantities in units):

    stock 100 opening + 10 received on a closed PO - 20 shipped           = 90 in stock
    SO: quote 5, confirmed 30, partial 50 ordered / 20 shipped (30 left)  = 60 open orders
    PO: draft 40; open PO 380 (WH1) + 120 (WH2 line) of which booked 100 (pending), booked 150 (approved),
        in transit 120 (the WH2 line), unbooked 130
    """

    def setUp(self):
        self.f = Fixture()
        f = self.f
        self.wh2 = Warehouse.objects.create(code="WH2", name="Second")
        adj = StockAdjustment.objects.create(warehouse=f.wh, date=TODAY, reason="opening")
        StockAdjustmentLine.objects.create(adjustment=adj, product=f.a, quantity=D(100), unit_cost=D(10))
        post_adjustment(adj)
        closed = f.po([(f.a, 10, 50)])
        pur.receive_po(closed, {closed.lines.first().pk: D(10)}, TODAY)  # -> status received
        f.po([(f.a, 40, 50)], send=False)  # draft
        self.open_po = f.po([(f.a, 380, 50)], send=False)
        line = self.open_po.lines.first()
        wh2_line = PurchaseOrderLine.objects.create(order=self.open_po, product=f.a, warehouse=self.wh2,
                                                    quantity=D(120), unit_price=D(50))
        pur.send_po(self.open_po)
        self.s_pending = self.ship(line, 100, Shipment.BOOKED, approved=False)
        self.s_approved = self.ship(line, 150, Shipment.PLANNED, approved=True)
        self.s_transit = self.ship(wh2_line, 120, Shipment.IN_TRANSIT, approved=True)
        self.order(5)  # quote
        sal.confirm_order(self.order(30))
        partial = self.order(50)
        sal.confirm_order(partial)
        sal.create_delivery(partial, {partial.lines.first().pk: D(20)}, TODAY)

    def ship(self, po_line, qty, status, approved, warehouse=None):
        s = Shipment.objects.create(vendor=self.f.vendor, warehouse=warehouse or self.f.wh, status=status,
                                    approved=approved)
        ShipmentLine.objects.create(shipment=s, po_line=po_line, quantity=D(qty))
        return s

    def order(self, qty):
        so = SalesOrder.objects.create(customer=self.f.customer, order_date=TODAY, warehouse=self.f.wh)
        SalesOrderLine.objects.create(order=so, product=self.f.a, quantity=D(qty), unit_price=D(20))
        return so

    def test_buckets_and_formulas(self):
        b = availability.for_product(self.f.a)
        expected = {
            "on_hand": 90, "so_quote": 5, "so_pending": 0, "so_open": 30, "so_partial": 30, "so_backorder": 0,
            "so_to_invoice": 20, "po_new": 40, "po_open": 130, "po_closed": 10, "booked": 100,
            "booked_approved": 150, "in_transit": 120, "open_orders": 60, "ats": 30, "in_transit_available": 120,
            "ats_in_transit": 150, "booked_available": 250, "available_to_sell": 90 + 130 + 100 + 150 + 120 - 60,
        }
        self.assertEqual({k: b[k] for k in expected}, {k: D(v) for k, v in expected.items()})

    def test_in_transit_available_deducts_shortfall(self):
        sal.confirm_order(self.order(100))  # open orders 160 vs 90 in stock -> short 70
        b = availability.for_product(self.f.a)
        self.assertEqual(b["ats"], D(-70))
        self.assertEqual(b["in_transit_available"], D(120 - 70))
        self.assertEqual(b["ats_in_transit"], D(90 + 120 - 160))

    def test_backorder_pending_and_booked_available(self):
        from accounting.services import post_customer_invoice

        partial = SalesOrder.objects.get(status=SalesOrder.PARTIAL)
        post_customer_invoice(sal.create_invoice_from_order(partial, TODAY))
        self.f.customer.credit_limit = D("1")  # next order goes to Pending approval
        self.f.customer.save()
        pending = self.order(300)
        sal.confirm_order(pending)
        self.assertEqual(SalesOrder.objects.get(pk=pending.pk).status, SalesOrder.PENDING)
        b = availability.for_product(self.f.a)
        self.assertEqual((b["so_partial"], b["so_backorder"], b["so_pending"]), (D(0), D(30), D(300)))
        self.assertEqual(b["open_orders"], D(360))
        # 360 ordered vs 90 stock + 120 in transit -> 150 still uncovered, taken from 250 booked
        self.assertEqual(b["booked_available"], D(100))
        self.assertEqual(b["in_transit_available"], D(120 - 270))
        self.assertEqual(b["available_to_sell"], D(90 + 130 + 100 + 150 + 120 - 360))
        self.assertEqual(sum(r["qty"] for r in availability.detail_rows(self.f.a, "so_backorder")), D(30))

    def test_partition_has_no_double_counting(self):
        b = availability.for_product(self.f.a)
        self.assertEqual(b["po_open"] + b["booked"] + b["booked_approved"] + b["in_transit"],
                         sum(ln.qty_open for ln in self.open_po.lines.all()))
        # Receiving the in-transit shipment moves 120 from In Transit to In Stock
        pur.receive_shipment(self.s_transit, TODAY)
        b2 = availability.for_product(self.f.a)
        self.assertEqual((b2["in_transit"], b2["on_hand"]), (D(0), D(210)))
        self.assertEqual(availability.for_product(self.f.a, self.wh2.pk)["on_hand"], D(120))  # into the line's WH
        self.assertEqual(b2["available_to_sell"], b["available_to_sell"])

    def test_warehouse_filter(self):
        wh1 = availability.for_product(self.f.a, self.f.wh.pk)
        wh2 = availability.for_product(self.f.a, self.wh2.pk)
        self.assertEqual((wh1["in_transit"], wh2["in_transit"]), (D(0), D(120)))
        self.assertEqual((wh1["on_hand"], wh2["on_hand"]), (D(90), D(0)))
        self.assertEqual(wh2["so_open"], D(0))

    def test_approval_moves_between_buckets(self):
        pur.approve_shipment(self.s_pending, self.f.user)
        b = availability.for_product(self.f.a)
        self.assertEqual((b["booked"], b["booked_approved"]), (D(0), D(250)))
        pur.unapprove_shipment(self.s_pending)
        self.assertEqual(availability.for_product(self.f.a)["booked"], D(100))

    def test_drilldown_and_screens(self):
        rows = availability.detail_rows(self.f.a, "so_partial")
        self.assertEqual([r["qty"] for r in rows], [D(30)])
        self.assertEqual(sum(r["qty"] for r in availability.detail_rows(self.f.a, "po_open")), D(130))
        self.assertEqual(availability.detail_rows(self.f.a, "booked")[0]["number"], self.s_pending.number)
        self.client.login(username="admin", password="pw-admin-123")
        resp = self.client.get("/inventory/availability/")
        self.assertContains(resp, "Available to Sell")
        self.assertContains(resp, f"/inventory/availability/{self.f.a.pk}/booked/")
        for url in [f"/inventory/availability/{self.f.a.pk}/in_transit/", f"/inventory/availability/?warehouse="
                    f"{self.wh2.pk}", "/inventory/availability/?show=short", f"/products/items/{self.f.a.pk}/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get(f"/inventory/availability/{self.f.a.pk}/ats/").status_code, 404)
        resp = self.client.get("/inventory/availability/export.xlsx")
        self.assertEqual(resp.status_code, 200)
        import io
        import zipfile

        self.assertFalse([n for n in zipfile.ZipFile(io.BytesIO(resp.content)).namelist() if n.startswith("/")])


class AvailabilityScreensTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        so = SalesOrder.objects.create(customer=self.f.customer, order_date=TODAY, warehouse=self.f.wh)
        SalesOrderLine.objects.create(order=so, product=self.f.a, quantity=D(25), unit_price=D(1))
        sal.confirm_order(so)  # no stock -> Available to Ship = -25
        self.client.login(username="admin", password="pw-admin-123")

    def cell(self, url, key):
        resp = self.client.get(url)
        return [v for c, v in resp.context["table"][0][1] if c["key"] == key][0]

    def test_each_screen_has_its_own_negative_setting(self):
        from core.models import Company

        self.assertEqual(self.cell("/inventory/availability/", "ats"), D(-25))  # default: real negatives
        self.assertEqual(self.cell("/inventory/sales-availability/", "ats"), D(0))  # default: floored
        c = Company.get()
        c.avail_no_negative, c.sales_avail_no_negative = ["ats"], []
        c.save()
        self.assertEqual(self.cell("/inventory/availability/", "ats"), D(0))
        self.assertEqual(self.cell("/inventory/availability/", "available_to_sell"), D(-25))
        self.assertEqual(self.cell("/inventory/sales-availability/", "ats"), D(-25))
        self.assertEqual(self.cell("/inventory/availability/", "open_orders"), D(25))

    def test_settings_form_and_pages(self):
        from core.models import Company

        resp = self.client.get("/settings/company/")
        self.assertContains(resp, 'name="sales_avail_no_negative"')
        data = {k: v for k, v in resp.context["form"].initial.items() if v is not None}
        data.update({"name": "Co", "base_currency": "USD", "default_sales_tax_rate": "0",
                     "default_payment_terms_days": "30", "upc_next_item_ref": "1", "cbm_20": "27", "cbm_40": "58",
                     "cbm_40hq": "67", "cbm_45": "76", "avail_no_negative": ["ats", "booked_available"],
                     "sales_avail_no_negative": []})
        data.pop("require_so_approval", None)
        self.client.post("/settings/company/", data)
        c = Company.get()
        self.assertEqual((sorted(c.avail_no_negative), c.sales_avail_no_negative), (["ats", "booked_available"], []))
        page = self.client.get("/inventory/availability/definitions/")
        self.assertContains(page, "Booked Available")
        self.assertContains(page, "Available to Ship, Booked Available show 0 when negative")
        screen = self.client.get("/inventory/availability/").content.decode()
        self.assertNotIn("How each bucket is calculated", screen)
        self.assertIn("pin-img", screen)
        resp = self.client.get("/inventory/availability/export.xlsx?screen=sales")
        self.assertIn("sales-availability", resp["Content-Disposition"])


class MultiWarehouseTests(TestCase):
    """Same item stocked, bought and sold in two warehouses."""

    def setUp(self):
        self.f = Fixture()
        self.wh2 = Warehouse.objects.create(code="WH2", name="Dallas")
        for wh, qty in ((self.f.wh, 30), (self.wh2, 50)):
            adj = StockAdjustment.objects.create(warehouse=wh, date=TODAY, reason="opening")
            StockAdjustmentLine.objects.create(adjustment=adj, product=self.f.a, quantity=D(qty), unit_cost=D(10))
            post_adjustment(adj)

    def qty(self, wh):
        from inventory.models import StockQuant

        q = StockQuant.objects.filter(product=self.f.a, warehouse=wh).first()
        return q.quantity if q else D(0)

    def test_sales_order_lines_ship_from_their_own_warehouse(self):
        so = SalesOrder.objects.create(customer=self.f.customer, order_date=TODAY, warehouse=self.f.wh)
        l1 = SalesOrderLine.objects.create(order=so, product=self.f.a, quantity=D(20))  # defaults to WH1
        l2 = SalesOrderLine.objects.create(order=so, product=self.f.a, quantity=D(40), warehouse=self.wh2)
        self.assertEqual(l1.warehouse, self.f.wh)
        sal.confirm_order(so)
        b1, b2 = availability.for_product(self.f.a, self.f.wh.pk), availability.for_product(self.f.a, self.wh2.pk)
        self.assertEqual((b1["so_open"], b1["ats"]), (D(20), D(10)))
        self.assertEqual((b2["so_open"], b2["ats"]), (D(40), D(10)))
        deliveries = sal.ship_order(so, {l1.pk: D(20), l2.pk: D(40)}, TODAY)
        self.assertEqual(sorted(d.warehouse.code for d in deliveries), ["WH1", "WH2"])
        self.assertEqual((self.qty(self.f.wh), self.qty(self.wh2)), (D(10), D(10)))
        # WH1 cannot cover more than it holds even though WH2 has stock
        so2 = SalesOrder.objects.create(customer=self.f.customer, order_date=TODAY, warehouse=self.f.wh)
        l3 = SalesOrderLine.objects.create(order=so2, product=self.f.a, quantity=D(15))
        sal.confirm_order(so2)
        from inventory.services import InsufficientStock

        with self.assertRaises(InsufficientStock):
            sal.ship_order(so2, {l3.pk: D(15)}, TODAY)

    def test_po_lines_received_into_their_own_warehouse(self):
        po = self.f.po([(self.f.a, 10, 70)], send=False)  # CNY 70 @ 0.14 = 9.80
        l2 = PurchaseOrderLine.objects.create(order=po, product=self.f.a, warehouse=self.wh2, quantity=D(25),
                                              unit_price=D(70))
        pur.send_po(po)
        b2 = availability.for_product(self.f.a, self.wh2.pk)
        self.assertEqual(b2["po_open"], D(25))
        receipt = pur.receive_po(po, {ln.pk: ln.quantity for ln in po.lines.all()}, TODAY)
        self.assertIsNone(receipt.warehouse)  # spans two warehouses
        self.assertEqual(sorted(ln.warehouse.code for ln in receipt.lines.all()), ["WH1", "WH2"])
        self.assertEqual((self.qty(self.f.wh), self.qty(self.wh2)), (D(40), D(75)))
        self.assertEqual(l2.receipt_lines.get().warehouse, self.wh2)

    def test_import_shipment_splits_into_line_warehouses(self):
        po = self.f.po([(self.f.a, 10, 70)], send=False)
        PurchaseOrderLine.objects.create(order=po, product=self.f.a, warehouse=self.wh2, quantity=D(5),
                                         unit_price=D(70))
        pur.send_po(po)
        shipment = pur.create_shipment_for_po(po)
        self.assertEqual(availability.for_product(self.f.a, self.wh2.pk)["booked_approved"], D(5))
        pur.receive_shipment(shipment, TODAY)
        self.assertEqual((self.qty(self.f.wh), self.qty(self.wh2)), (D(40), D(55)))

    def test_order_form_requires_line_warehouse(self):
        from products.models import Product  # noqa: F401

        self.client.login(username="admin", password="pw-admin-123")
        base = {"customer": self.f.customer.pk, "order_date": "2026-06-15", "warehouse": self.f.wh.pk,
                "ship_to": self.f.customer.default_location.pk,
                "tax_rate": "0", "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000", "lines-0-product": self.f.a.pk, "lines-0-quantity": "5",
                "lines-0-discount_pct": "0"}
        resp = self.client.post("/sales/orders/new/", dict(base, **{"lines-0-warehouse": ""}))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(SalesOrder.objects.exists())
        self.client.post("/sales/orders/new/", dict(base, **{"lines-0-warehouse": self.wh2.pk}))
        self.assertEqual(SalesOrder.objects.get().lines.get().warehouse, self.wh2)
        page = self.client.get("/sales/orders/new/").content.decode()
        self.assertIn('name="lines-0-warehouse"', page)


class ShipmentApprovalTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        self.po = self.f.po([(self.f.a, 100, 10)])
        user = get_user_model().objects.create_user("vend", password="pw-vendor-123")
        VendorContact.objects.create(user=user, vendor=self.f.vendor)

    def asn(self, qty, shipment=None):
        line = self.po.lines.first()
        data = {"status": "booked", "mode": "sea_fcl", "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "1" if shipment else "0", "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000", "lines-0-po_line": line.pk, "lines-0-quantity": str(qty)}
        if shipment:
            data["lines-0-id"] = shipment.lines.first().pk
        url = f"/portal/shipments/{shipment.pk}/edit/" if shipment else "/portal/shipments/new/"
        return self.client.post(url, data)

    def test_staff_bookings_are_approved_portal_asn_is_pending(self):
        s = pur.create_shipment_for_po(self.po, approved_by=self.f.user)
        self.assertTrue(s.approved)
        s.status = Shipment.CANCELLED
        s.save()
        self.client.login(username="vend", password="pw-vendor-123")
        self.asn(60)
        asn = Shipment.objects.get(created_via_portal=True)
        self.assertFalse(asn.approved)
        self.assertEqual(availability.for_product(self.f.a)["booked"], D(60))
        pur.approve_shipment(asn, self.f.user)
        # Vendor changes the quantity -> approval is reset
        self.asn(80, asn)
        asn.refresh_from_db()
        self.assertFalse(asn.approved)
        self.assertEqual(availability.for_product(self.f.a)["booked"], D(80))

    def test_staff_approve_button(self):
        s = Shipment.objects.create(vendor=self.f.vendor, warehouse=self.f.wh, created_via_portal=True)
        ShipmentLine.objects.create(shipment=s, po_line=self.po.lines.first(), quantity=D(10))
        self.client.login(username="admin", password="pw-admin-123")
        self.client.post(f"/purchasing/shipments/{s.pk}/approve/")
        s.refresh_from_db()
        self.assertTrue(s.approved)
        self.assertEqual(s.approved_by, self.f.user)


__all__ = ["PurchaseOrder", "PurchaseOrderLine"]
