from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import VendorBill
from core.testing import Fixture
from portal.models import VendorContact
from purchasing.models import PurchaseOrder, Shipment


class VendorPortalTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        self.po = self.f.po([(self.f.a, 100, 100)])
        self.other_po = self.f.po([(self.f.a, 5, 5)], vendor=self.f.usd_vendor, rate=D(1), currency="USD")
        user = get_user_model().objects.create_user("vend", password="pw-vendor-123")
        VendorContact.objects.create(user=user, vendor=self.f.vendor)
        self.client.login(username="vend", password="pw-vendor-123")

    def test_vendor_isolation(self):
        self.assertEqual(self.client.get(f"/portal/orders/{self.po.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/portal/orders/{self.other_po.pk}/").status_code, 404)
        # Internal ERP pages bounce vendors back to the portal
        resp = self.client.get("/purchasing/orders/")
        self.assertRedirects(resp, "/portal/")
        self.assertRedirects(self.client.get("/"), "/portal/")

    def test_confirm_asn_and_invoice(self):
        self.client.post(f"/portal/orders/{self.po.pk}/confirm/", {"ship_date": "2026-07-01",
                                                                   "vendor_reference": "PI-9"})
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.CONFIRMED)
        self.assertEqual(self.po.vendor_reference, "PI-9")

        line = self.po.lines.first()
        resp = self.client.post("/portal/shipments/new/", {
            "status": "booked", "mode": "sea_fcl", "bill_of_lading": "BL123", "etd": "2026-07-02",
            "eta": "2026-07-30", "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000",
            "lines-0-po_line": line.pk, "lines-0-quantity": "100", "lines-0-cartons": "10",
        })
        shipment = Shipment.objects.get()
        self.assertRedirects(resp, f"/portal/shipments/{shipment.pk}/")
        self.assertTrue(shipment.created_via_portal)
        self.assertEqual(shipment.vendor, self.f.vendor)
        self.assertEqual(shipment.warehouse, self.f.wh)

        # A vendor cannot book another vendor's PO line
        other_line = self.other_po.lines.first()
        resp = self.client.post("/portal/shipments/new/", {
            "status": "booked", "mode": "air", "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000",
            "lines-0-po_line": other_line.pk, "lines-0-quantity": "1",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Shipment.objects.count(), 1)

        self.client.post(f"/portal/orders/{self.po.pk}/invoice/", {"vendor_invoice_number": "V-77",
                                                                   f"qty_{line.pk}": "100"})
        bill = VendorBill.objects.get()
        self.assertEqual(bill.status, VendorBill.DRAFT)
        self.assertTrue(bill.submitted_via_portal)
        self.assertEqual(bill.total, D("10000.00"))
