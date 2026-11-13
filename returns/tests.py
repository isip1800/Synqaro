from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounting.models import CustomerInvoice, VendorBill, VendorBillLine
from accounting.services import acct, ap_aging, ar_aging, post_vendor_bill
from core.testing import TODAY, Fixture
from inventory.models import StockAdjustment, StockAdjustmentLine, StockMove, StockQuant
from inventory.services import post_adjustment
from sales import services as sales_services
from sales.models import SalesOrder, SalesOrderLine
from salesteam.models import Commission, SalesRep

from . import services
from .models import (
    Chargeback, CreditMemo, CreditMemoLine, CustomerReturn, CustomerReturnLine, VendorCredit, VendorCreditLine,
    VendorReturn, VendorReturnLine,
)


def on_hand(product, wh):
    q = StockQuant.objects.filter(product=product, warehouse=wh).first()
    return q.quantity if q else D(0)


class ReturnsTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        adj = StockAdjustment.objects.create(warehouse=self.f.wh, date=TODAY, reason="opening")
        StockAdjustmentLine.objects.create(adjustment=adj, product=self.f.a, quantity=D(100), unit_cost=D(10))
        post_adjustment(adj)

    def invoice(self, qty=10, rep=None, tax=D("0")):
        so = SalesOrder.objects.create(customer=self.f.customer, order_date=TODAY, warehouse=self.f.wh,
                                       tax_rate=tax, sales_rep=rep)
        SalesOrderLine.objects.create(order=so, product=self.f.a, quantity=D(qty))  # list price 20
        sales_services.confirm_order(so)
        sales_services.create_delivery(so, {ln.pk: ln.quantity for ln in so.lines.all()}, TODAY)
        from accounting.services import post_customer_invoice

        return so, post_customer_invoice(sales_services.create_invoice_from_order(so, TODAY))

    def ar_ties(self):
        self.assertEqual(ar_aging(TODAY)["totals"]["total"], self.f.balance("AR"))

    # ------------------------------------------------------------------ customer side

    def test_customer_return_restock_and_credit_memo(self):
        so, inv = self.invoice(qty=10, tax=D("8"))  # 200 + 16 tax
        self.assertEqual(on_hand(self.f.a, self.f.wh), D(90))
        ret = CustomerReturn.objects.create(customer=self.f.customer, sales_order=so, invoice=inv,
                                            warehouse=self.f.wh, date=TODAY)
        CustomerReturnLine.objects.create(customer_return=ret, product=self.f.a, quantity=D(3), unit_price=D(20))
        CustomerReturnLine.objects.create(customer_return=ret, product=self.f.a, quantity=D(1), unit_price=D(20),
                                          disposition=CustomerReturnLine.SCRAP)
        services.receive_customer_return(ret)
        ret.refresh_from_db()
        self.assertEqual(ret.status, CustomerReturn.RECEIVED)
        self.assertEqual(on_hand(self.f.a, self.f.wh), D(93))  # scrap line not restocked
        self.assertTrue(StockMove.objects.filter(move_type="customer_return", reference=ret.number).exists())
        self.assertEqual(self.f.balance("COGS"), D("70.00"))  # 100 shipped cost - 30 restocked

        memo = services.create_credit_memo_from_return(ret)
        self.assertEqual(memo.invoice, inv)
        self.assertEqual(memo.tax_rate, D("8"))
        with self.assertRaises(ValidationError):
            services.create_credit_memo_from_return(ret)
        memo.date = TODAY
        memo.save()
        services.post_credit_memo(memo)
        memo.refresh_from_db()
        ret.refresh_from_db()
        self.assertEqual(memo.total, D("86.40"))  # 4 x 20 + 8% tax
        self.assertEqual(ret.status, CustomerReturn.CREDITED)
        self.assertEqual(self.f.balance("SALES_RETURNS"), D("-80.00"))  # contra income: debit balance
        self.assertEqual(self.f.balance("AR"), D("129.60"))
        self.ar_ties()  # unapplied credit shows in aging

        services.auto_apply_credit_memo(memo)
        memo.refresh_from_db()
        inv.refresh_from_db()
        self.assertEqual(memo.status, CreditMemo.APPLIED)
        self.assertEqual(inv.balance_due, D("129.60"))
        self.assertEqual(inv.status, CustomerInvoice.PARTIAL)
        self.ar_ties()

    def test_credit_memo_claws_back_commission(self):
        rep = SalesRep.objects.create(code="R1", name="Rep", commission_rate=D("5"))
        so, inv = self.invoice(qty=10, rep=rep)
        self.assertEqual(self.f.balance("COMMISSIONS_PAYABLE"), D("10.00"))
        memo = CreditMemo.objects.create(customer=self.f.customer, invoice=inv, date=TODAY)
        CreditMemoLine.objects.create(credit_memo=memo, description="Allowance", unit_price=D("50"))
        services.post_credit_memo(memo)
        self.assertEqual(self.f.balance("COMMISSIONS_PAYABLE"), D("7.50"))
        self.assertEqual(Commission.objects.filter(amount__lt=0).count(), 1)

    def test_apply_credit_memo_validation(self):
        so, inv = self.invoice(qty=1)  # 20
        memo = CreditMemo.objects.create(customer=self.f.customer, date=TODAY)
        CreditMemoLine.objects.create(credit_memo=memo, description="Allowance", unit_price=D("50"))
        with self.assertRaises(ValidationError):
            services.apply_credit_memo(memo, inv, D(10))  # not posted
        services.post_credit_memo(memo)
        with self.assertRaises(ValidationError):
            services.apply_credit_memo(memo, inv, D(30))  # more than invoice balance
        services.apply_credit_memo(memo, inv, D(20))
        inv.refresh_from_db()
        memo.refresh_from_db()
        self.assertEqual(inv.status, CustomerInvoice.PAID)
        self.assertEqual(memo.status, CreditMemo.PARTIAL)
        self.assertEqual(memo.unapplied, D(30))
        self.ar_ties()

    def test_chargeback_lifecycle(self):
        so, inv = self.invoice(qty=10)  # 200
        cb = Chargeback.objects.create(customer=self.f.customer, invoice=inv, date=TODAY, amount=D("25"),
                                       chargeback_type="labeling", deduction_reference="DM-1")
        services.dispute_chargeback(cb, "Sent proof")
        cb.refresh_from_db()
        self.assertEqual(cb.status, Chargeback.DISPUTED)
        self.assertIn("Sent proof", cb.resolution_notes)
        self.assertEqual(self.f.balance("CHARGEBACKS"), D(0))
        with self.assertRaises(ValidationError):
            services.accept_chargeback(cb, D("30"))  # more than deducted
        services.accept_chargeback(cb, D("20"))
        cb.refresh_from_db()
        inv.refresh_from_db()
        self.assertEqual(cb.status, Chargeback.ACCEPTED)
        self.assertEqual(self.f.balance("CHARGEBACKS"), D("20.00"))
        self.assertEqual(inv.balance_due, D("180.00"))
        self.ar_ties()
        with self.assertRaises(ValidationError):
            services.win_chargeback(cb)

        cb2 = Chargeback.objects.create(customer=self.f.customer, date=TODAY, amount=D("10"))
        services.win_chargeback(cb2)
        cb2.refresh_from_db()
        self.assertEqual(cb2.status, Chargeback.WON)
        self.assertEqual(self.f.balance("CHARGEBACKS"), D("20.00"))

    def test_chargeback_without_invoice_clears_oldest_invoice(self):
        so, inv = self.invoice(qty=10)
        cb = Chargeback.objects.create(customer=self.f.customer, date=TODAY, amount=D("15"))
        services.accept_chargeback(cb)
        inv.refresh_from_db()
        self.assertEqual(inv.balance_due, D("185.00"))
        self.ar_ties()

    # ------------------------------------------------------------------ vendor side

    def test_vendor_return_and_credit_applied_with_fx(self):
        bill = VendorBill.objects.create(vendor=self.f.vendor, vendor_invoice_number="INV-9", bill_date=TODAY,
                                         currency_id="CNY", exchange_rate=D("0.14"))
        VendorBillLine.objects.create(bill=bill, description="Goods", account=acct("INVENTORY"),
                                      quantity=D(1), unit_price=D("1000"))
        post_vendor_bill(bill)  # 1000 CNY = 140 USD

        vr = VendorReturn.objects.create(vendor=self.f.vendor, warehouse=self.f.wh, date=TODAY)
        VendorReturnLine.objects.create(vendor_return=vr, product=self.f.a, quantity=D(5), unit_cost=D("9"))
        services.ship_vendor_return(vr)
        vr.refresh_from_db()
        self.assertEqual(vr.status, VendorReturn.SHIPPED)
        self.assertEqual(on_hand(self.f.a, self.f.wh), D(95))
        self.assertEqual(self.f.balance("VENDOR_RETURNS"), D("45.00"))
        self.assertEqual(self.f.balance("PPV"), D("5.00"))  # 50 at avg cost vs 45 expected back

        # Vendor issues a CNY credit note for the return at a different rate
        vc = VendorCredit.objects.create(vendor=self.f.vendor, vendor_return=vr, date=TODAY, currency_id="CNY",
                                         exchange_rate=D("0.15"))
        VendorCreditLine.objects.create(vendor_credit=vc, description="Credit for VR", quantity=D(1),
                                        unit_price=D("300"))  # 300 CNY = 45 USD
        services.post_vendor_credit(vc)
        vr.refresh_from_db()
        self.assertEqual(vr.status, VendorReturn.CREDITED)
        self.assertEqual(self.f.balance("VENDOR_RETURNS"), D("0.00"))
        self.assertEqual(ap_aging(TODAY)["totals"]["total"], D("95.00"))  # 140 bill - 45 credit

        services.apply_vendor_credit(vc, bill, D("300"))
        bill.refresh_from_db()
        vc.refresh_from_db()
        self.assertEqual(bill.balance_due, D("700"))
        self.assertEqual(vc.status, VendorCredit.APPLIED)
        # AP for the bill relieved at 0.14 (42) while the credit was booked at 0.15 (45): 3 FX difference
        self.assertEqual(self.f.balance("AP"), D("98.00"))  # 700 CNY @ 0.14
        self.assertEqual(ap_aging(TODAY)["totals"]["total"], self.f.balance("AP"))

    def test_vendor_credit_currency_mismatch(self):
        bill = VendorBill.objects.create(vendor=self.f.vendor, vendor_invoice_number="INV-1", bill_date=TODAY,
                                         currency_id="CNY", exchange_rate=D("0.14"))
        VendorBillLine.objects.create(bill=bill, description="x", account=acct("INVENTORY"), unit_price=D(100))
        post_vendor_bill(bill)
        vc = VendorCredit.objects.create(vendor=self.f.vendor, date=TODAY, currency_id="USD")
        VendorCreditLine.objects.create(vendor_credit=vc, description="x", unit_price=D(5))
        services.post_vendor_credit(vc)
        with self.assertRaises(ValidationError):
            services.apply_vendor_credit(vc, bill, D(5))

    # ------------------------------------------------------------------ screens & permissions

    def test_screens_render(self):
        so, inv = self.invoice(qty=2)
        ret = CustomerReturn.objects.create(customer=self.f.customer, invoice=inv, warehouse=self.f.wh, date=TODAY)
        CustomerReturnLine.objects.create(customer_return=ret, product=self.f.a, quantity=D(1), unit_price=D(20))
        memo = CreditMemo.objects.create(customer=self.f.customer, date=TODAY)
        CreditMemoLine.objects.create(credit_memo=memo, description="x", unit_price=D(5))
        services.post_credit_memo(memo)
        cb = Chargeback.objects.create(customer=self.f.customer, invoice=inv, date=TODAY, amount=D(5))
        vr = VendorReturn.objects.create(vendor=self.f.vendor, warehouse=self.f.wh, date=TODAY)
        vc = VendorCredit.objects.create(vendor=self.f.vendor, date=TODAY, currency_id="USD")
        self.client.force_login(self.f.user)
        urls = [reverse(f"returns:{n}_list") for n in
                ("customerreturn", "creditmemo", "chargeback", "vendorreturn", "vendorcredit")]
        urls += [o.get_absolute_url() for o in (ret, memo, cb, vr, vc)]
        urls += [reverse(f"returns:{n}_create") for n in
                 ("customerreturn", "creditmemo", "chargeback", "vendorreturn", "vendorcredit")]
        urls += [reverse("returns:chargeback_report"), inv.get_absolute_url(),
                 reverse("returns:chargeback_create") + f"?invoice={inv.pk}&customer={self.f.customer.pk}",
                 reverse("accounting:ar_aging"), reverse("accounting:ap_aging")]
        for url in urls:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)

    def test_create_return_via_form(self):
        self.client.force_login(self.f.user)
        r = self.client.post(reverse("returns:customerreturn_create"), {
            "customer": self.f.customer.pk, "warehouse": self.f.wh.pk, "date": TODAY.isoformat(),
            "reason": "damaged", "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0",
            "lines-0-product": self.f.a.pk, "lines-0-quantity": "2", "lines-0-unit_price": "20",
            "lines-0-disposition": "restock"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        ret = CustomerReturn.objects.get()
        self.assertTrue(ret.number.startswith("RMA-"))
        r = self.client.post(reverse("returns:customerreturn_receive", args=[ret.pk]))
        ret.refresh_from_db()
        self.assertEqual(ret.status, CustomerReturn.RECEIVED)

    def test_permissions(self):
        so, inv = self.invoice(qty=2)
        memo = CreditMemo.objects.create(customer=self.f.customer, date=TODAY)
        CreditMemoLine.objects.create(credit_memo=memo, description="x", unit_price=D(5))
        self.f.member("wh", "Warehouse")
        self.f.member("rep", "Sales")
        self.client.login(username="wh", password="pw-test-1234")
        self.assertEqual(self.client.get(reverse("returns:creditmemo_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("returns:customerreturn_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("returns:customerreturn_create")).status_code, 403)
        self.client.login(username="rep", password="pw-test-1234")
        self.assertEqual(self.client.get(reverse("returns:customerreturn_create")).status_code, 200)
        self.assertEqual(self.client.get(reverse("returns:chargeback_create")).status_code, 200)
        self.client.post(reverse("returns:creditmemo_post", args=[memo.pk]))
        memo.refresh_from_db()
        self.assertEqual(memo.status, CreditMemo.DRAFT)  # sales can't post to the ledger
        self.assertEqual(self.client.get(reverse("returns:vendorreturn_list")).status_code, 403)


