from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounting.models import CustomerInvoice, CustomerPayment, CustomerPaymentAllocation
from accounting.services import acct, auto_allocate_customer_payment, post_customer_invoice, post_customer_payment
from core.testing import TODAY, Fixture
from inventory.models import StockAdjustment, StockAdjustmentLine
from inventory.services import InsufficientStock, post_adjustment, receive_stock
from products.models import PriceList, PriceListItem
from sales import services
from sales.models import SalesOrder, SalesOrderLine
from salesteam.models import Commission, SalesRep
from salesteam.services import pay_commissions


class SalesFlowTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        adj = StockAdjustment.objects.create(warehouse=self.f.wh, date=TODAY, reason="opening")
        StockAdjustmentLine.objects.create(adjustment=adj, product=self.f.a, quantity=D(100), unit_cost=D(10))
        StockAdjustmentLine.objects.create(adjustment=adj, product=self.f.b, quantity=D(20), unit_cost=D(30))
        post_adjustment(adj)

    def order(self, rep=None, tax=D("8")):
        so = SalesOrder.objects.create(customer=self.f.customer, order_date=TODAY, warehouse=self.f.wh,
                                       tax_rate=tax, sales_rep=rep)
        SalesOrderLine.objects.create(order=so, product=self.f.a, quantity=D(10))  # list price 20
        SalesOrderLine.objects.create(order=so, product=self.f.b, quantity=D(2), discount_pct=D(10))  # 90
        return so

    def ship_and_invoice(self, so):
        services.confirm_order(so)
        services.create_delivery(so, {ln.pk: ln.quantity for ln in so.lines.all()}, TODAY)
        inv = services.create_invoice_from_order(so, TODAY)
        return post_customer_invoice(inv)

    def test_moving_average_cost(self):
        receive_stock(self.f.a, self.f.wh, D(100), D(20), TODAY)
        self.f.a.refresh_from_db()
        self.assertEqual(self.f.a.avg_cost, D("15.0000"))

    def test_order_to_cash(self):
        so = self.order()
        self.assertEqual(so.subtotal, D("290.00"))
        inv = self.ship_and_invoice(so)
        self.assertEqual(inv.total, D("313.20"))
        self.assertEqual(self.f.balance("AR"), D("313.20"))
        self.assertEqual(self.f.balance("SALES"), D("290.00"))
        self.assertEqual(self.f.balance("SALES_TAX"), D("23.20"))
        self.assertEqual(self.f.balance("COGS"), D("160.00"))  # 10*10 + 2*30
        self.assertEqual(inv.gross_margin, D("130.00"))
        so.refresh_from_db()
        self.assertEqual(so.status, SalesOrder.CLOSED)
        self.assertEqual(inv.due_date, TODAY.replace(month=7, day=15))

        pay = CustomerPayment.objects.create(customer=self.f.customer, date=TODAY, amount=D("313.20"),
                                             deposit_account=acct("CASH"))
        auto_allocate_customer_payment(pay)
        post_customer_payment(pay)
        inv.refresh_from_db()
        self.assertEqual(inv.status, CustomerInvoice.PAID)
        self.assertEqual(self.f.customer.balance, D("0.00"))

    def test_price_list_tiers(self):
        pl = PriceList.objects.create(name="Dist")
        PriceListItem.objects.create(price_list=pl, product=self.f.a, min_qty=1, price=D("18"))
        PriceListItem.objects.create(price_list=pl, product=self.f.a, min_qty=50, price=D("16"))
        self.f.customer.price_list = pl
        self.f.customer.save()
        so = SalesOrder.objects.create(customer=self.f.customer, order_date=TODAY, warehouse=self.f.wh)
        small = SalesOrderLine.objects.create(order=so, product=self.f.a, quantity=D(5))
        big = SalesOrderLine.objects.create(order=so, product=self.f.a, quantity=D(60))
        self.assertEqual((small.unit_price, big.unit_price), (D("18"), D("16")))

    def test_credit_limit_blocks_unless_overridden(self):
        self.f.customer.credit_limit = D("100")
        self.f.customer.save()
        so = self.order()
        with self.assertRaisesMessage(ValidationError, "Credit limit"):
            services.confirm_order(so)
        services.confirm_order(so, override_credit=True)
        so.refresh_from_db()
        self.assertTrue(so.credit_override)

    def test_cannot_ship_more_than_stock_or_order(self):
        so = SalesOrder.objects.create(customer=self.f.customer, order_date=TODAY, warehouse=self.f.wh)
        line = SalesOrderLine.objects.create(order=so, product=self.f.b, quantity=D(50))
        services.confirm_order(so)
        with self.assertRaises(InsufficientStock):
            services.create_delivery(so, {line.pk: D(50)}, TODAY)
        with self.assertRaises(ValidationError):
            services.create_delivery(so, {line.pk: D(51)}, TODAY)
        services.create_delivery(so, {line.pk: D(20)}, TODAY)
        so.refresh_from_db()
        self.assertEqual(so.status, SalesOrder.PARTIAL)

    def test_cannot_invoice_more_than_shipped(self):
        so = self.order()
        services.confirm_order(so)
        first = so.lines.first()
        services.create_delivery(so, {first.pk: D(4)}, TODAY)
        inv = services.create_invoice_from_order(so, TODAY)
        self.assertEqual([ln.quantity for ln in inv.lines.all()], [D(4)])
        line = inv.lines.first()
        line.quantity = D(9)
        line.save()
        with self.assertRaises(ValidationError):
            post_customer_invoice(inv)

    def test_payment_overallocation_rejected(self):
        inv = self.ship_and_invoice(self.order())
        pay = CustomerPayment.objects.create(customer=self.f.customer, date=TODAY, amount=D("100"),
                                             deposit_account=acct("CASH"))
        CustomerPaymentAllocation.objects.create(payment=pay, invoice=inv, amount=D("150"))
        with self.assertRaises(ValidationError):
            post_customer_payment(pay)

    def test_commission_on_invoice_revenue_and_payout(self):
        rep = SalesRep.objects.create(code="R1", name="Rep", commission_rate=D("5"))
        inv = self.ship_and_invoice(self.order(rep=rep))
        c = Commission.objects.get(invoice=inv)
        self.assertEqual(c.amount, D("14.50"))
        self.assertEqual(self.f.balance("COMMISSIONS_PAYABLE"), D("14.50"))
        pay_commissions(rep, [c], acct("CASH"), TODAY)
        self.assertEqual(self.f.balance("COMMISSIONS_PAYABLE"), D("0.00"))
        c.refresh_from_db()
        self.assertEqual(c.status, Commission.PAID)

    def test_commission_on_payment_margin_is_proportional(self):
        rep = SalesRep.objects.create(code="R2", name="Rep2", commission_rate=D("10"),
                                      commission_basis=SalesRep.MARGIN, commission_trigger=SalesRep.ON_PAYMENT)
        inv = self.ship_and_invoice(self.order(rep=rep))
        self.assertFalse(Commission.objects.exists())
        pay = CustomerPayment.objects.create(customer=self.f.customer, date=TODAY, amount=D("156.60"),
                                             deposit_account=acct("CASH"))
        CustomerPaymentAllocation.objects.create(payment=pay, invoice=inv, amount=D("156.60"))
        post_customer_payment(pay)
        c = Commission.objects.get()
        self.assertEqual((c.basis_amount, c.amount), (D("65.00"), D("6.50")))
        inv.refresh_from_db()
        self.assertEqual(inv.status, CustomerInvoice.PARTIAL)
