from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import VendorBill, VendorBillLine, VendorPayment, VendorPaymentAllocation
from accounting.services import acct, post_vendor_bill, post_vendor_payment
from core.testing import TODAY, Fixture
from inventory.services import issue_stock
from purchasing import services
from purchasing.models import LandedCost, PurchaseOrder, Shipment


class ImportFlowTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        # A: 100 @ 100 CNY (=14.00 USD), B: 50 @ 200 CNY (=28.00 USD); goods value 2,800 USD
        self.po = self.f.po([(self.f.a, 100, 100), (self.f.b, 50, 200)])
        self.ship = services.create_shipment_for_po(self.po, forwarder=self.f.forwarder)

    def add_cost(self, amount, cost_type="freight", vendor=None):
        return LandedCost.objects.create(shipment=self.ship, cost_type=cost_type, amount=D(amount),
                                         vendor=vendor or self.f.forwarder)

    def test_allocation_methods_are_exact(self):
        lines = list(self.ship.lines.all())
        expected = {
            Shipment.ALLOC_VALUE: [D("200.00"), D("200.00")],
            Shipment.ALLOC_QTY: [D("266.67"), D("133.33")],
            Shipment.ALLOC_WEIGHT: [D("160.00"), D("240.00")],
        }
        for method, amounts in expected.items():
            self.ship.allocation_method = method
            alloc = services.allocate(self.ship, D("400"), lines)
            self.assertEqual([alloc[ln.pk] for ln in lines], amounts, method)
            self.assertEqual(sum(alloc.values()), D("400.00"))

    def test_estimate_duty_uses_product_rates(self):
        lc = services.estimate_duty(self.ship)
        # 1,400 * 5% + 1,400 * 10%
        self.assertEqual(lc.amount, D("210.00"))
        services.estimate_duty(self.ship)  # idempotent refresh
        self.assertEqual(self.ship.landed_costs.filter(cost_type="duty").count(), 1)

    def test_receive_shipment_capitalizes_landed_cost_and_full_cycle_clears_clearing_accounts(self):
        self.add_cost("400")
        receipt = services.receive_shipment(self.ship, TODAY, self.f.user)
        self.f.a.refresh_from_db()
        self.f.b.refresh_from_db()
        self.assertEqual(self.f.a.avg_cost, D("16.0000"))  # 14 goods + 2 landed
        self.assertEqual(self.f.b.avg_cost, D("32.0000"))  # 28 goods + 4 landed
        self.assertEqual(receipt.total_value, D("3200.00"))
        self.assertEqual(self.f.balance("INVENTORY"), D("3200.00"))
        self.assertEqual(self.f.balance("GRNI"), D("2800.00"))
        self.assertEqual(self.f.balance("ACCRUED_LANDED"), D("400.00"))
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.RECEIVED)
        self.ship.refresh_from_db()
        self.assertEqual(self.ship.status, Shipment.RECEIVED)
        with self.assertRaises(ValidationError):
            services.receive_shipment(self.ship, TODAY)

        # Supplier bill at a different FX rate -> price/FX variance
        bill = services.create_bill_from_po(self.po, "INV-1", TODAY)
        bill.exchange_rate = D("0.15")
        bill.save()
        post_vendor_bill(bill, self.f.user)
        self.assertEqual(self.f.balance("GRNI"), D("0.00"))
        self.assertEqual(self.f.balance("PPV"), D("200.00"))
        self.assertEqual(self.f.balance("AP"), D("3000.00"))

        # Forwarder bill clears accrued landed costs
        fb = VendorBill.objects.create(vendor=self.f.forwarder, vendor_invoice_number="F-1", bill_date=TODAY,
                                       currency_id="USD")
        VendorBillLine.objects.create(bill=fb, description="Freight", account=acct("ACCRUED_LANDED"),
                                      unit_price=D("400"))
        post_vendor_bill(fb)
        self.assertEqual(self.f.balance("ACCRUED_LANDED"), D("0.00"))

        # Pay the supplier at yet another rate -> realized FX loss
        pay = VendorPayment.objects.create(vendor=self.f.vendor, date=TODAY, currency_id="CNY",
                                           exchange_rate=D("0.16"), amount=D("20000"), paid_from=acct("CASH"))
        VendorPaymentAllocation.objects.create(payment=pay, bill=bill, amount=D("20000"))
        post_vendor_payment(pay)
        bill.refresh_from_db()
        self.assertEqual(bill.status, VendorBill.PAID)
        self.assertEqual(self.f.balance("FX"), D("200.00"))
        self.assertEqual(self.f.vendor.balance, D("0.00"))

    def test_duplicate_vendor_invoice_rejected(self):
        services.receive_shipment(self.ship, TODAY)
        b1 = services.create_bill_from_po(self.po, "DUP", TODAY, {self.po.lines.first().pk: D(10)})
        post_vendor_bill(b1)
        b2 = services.create_bill_from_po(self.po, "DUP", TODAY, {self.po.lines.first().pk: D(10)})
        with self.assertRaisesMessage(ValidationError, "already posted"):
            post_vendor_bill(b2)

    def test_over_billing_rejected(self):
        line = self.po.lines.first()
        bill = VendorBill.objects.create(vendor=self.f.vendor, vendor_invoice_number="X", bill_date=TODAY,
                                         currency_id="CNY", exchange_rate=D("0.14"))
        VendorBillLine.objects.create(bill=bill, po_line=line, description="x", quantity=D(101), unit_price=D(100))
        with self.assertRaises(ValidationError):
            post_vendor_bill(bill)

    def test_late_landed_cost_split_between_stock_and_cogs(self):
        services.receive_shipment(self.ship, TODAY)
        issue_stock(self.f.a, self.f.wh, D(50), TODAY)  # half of A already sold
        lc = self.add_cost("100")  # by value: 50 to A, 50 to B
        services.capitalize_late_landed_cost(lc, TODAY)
        self.f.a.refresh_from_db()
        self.f.b.refresh_from_db()
        self.assertEqual(self.f.a.avg_cost, D("14.5000"))  # 14 + 25/50
        self.assertEqual(self.f.b.avg_cost, D("29.0000"))  # 28 + 50/50
        self.assertEqual(self.f.balance("COGS"), D("25.00"))
        self.assertEqual(self.f.balance("ACCRUED_LANDED"), D("100.00"))

    def test_shipment_cannot_exceed_open_po_qty(self):
        line = self.ship.lines.first()
        line.quantity = D(1000)
        line.save()
        with self.assertRaises(ValidationError):
            services.receive_shipment(self.ship, TODAY)

    def test_direct_receipt_and_partial_status(self):
        po = self.f.po([(self.f.a, 10, 10)], vendor=self.f.usd_vendor, rate=D(1), currency="USD")
        services.receive_po(po, {po.lines.first().pk: D(4)}, TODAY)
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.PARTIAL)
        with self.assertRaises(ValidationError):
            services.receive_po(po, {po.lines.first().pk: D(7)}, TODAY)
        with self.assertRaises(ValidationError):
            services.cancel_po(po)
