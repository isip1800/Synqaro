"""Shared fixtures for tests."""
import datetime
from decimal import Decimal as D

from django.contrib.auth import get_user_model

from accounting.services import acct, setup_chart_of_accounts
from core.models import Company, Currency, ExchangeRate
from inventory.models import Warehouse
from products.models import Product
from purchasing.models import PurchaseOrder, PurchaseOrderLine, Vendor
from sales.models import Customer

TODAY = datetime.date(2026, 6, 15)


class Fixture:
    def __init__(self):
        Company.clear_cache()  # a previous test's rolled-back settings must not leak
        Company.get()
        Currency.objects.get_or_create(code="CNY", defaults={"name": "Yuan"})
        ExchangeRate.objects.create(currency_id="CNY", date=TODAY - datetime.timedelta(days=60), rate=D("0.14"))
        setup_chart_of_accounts()
        self.user = get_user_model().objects.create_superuser("admin", password="pw-admin-123")
        self.wh = Warehouse.objects.create(code="WH1", name="Main")
        self.vendor = Vendor.objects.create(code="V1", name="Ningbo Supplier", currency_id="CNY")
        self.usd_vendor = Vendor.objects.create(code="V2", name="US Supplier", currency_id="USD")
        self.forwarder = Vendor.objects.create(code="F1", name="Forwarder", vendor_type=Vendor.FORWARDER)
        self.a = Product.objects.create(sku="A", name="Pan", list_price=D("20"), duty_rate=D("5"),
                                        weight_kg=D("1"), volume_cbm=D("0.01"))
        self.b = Product.objects.create(sku="B", name="Pot", list_price=D("50"), duty_rate=D("10"),
                                        weight_kg=D("3"), volume_cbm=D("0.01"))
        self.customer = Customer.objects.create(code="C1", name="Retailer", payment_terms_days=30)

    def po(self, lines, vendor=None, rate=D("0.14"), currency="CNY", send=True):
        from purchasing.services import send_po

        po = PurchaseOrder.objects.create(vendor=vendor or self.vendor, order_date=TODAY, currency_id=currency,
                                          exchange_rate=rate, warehouse=self.wh)
        for product, qty, price in lines:
            PurchaseOrderLine.objects.create(order=po, product=product, quantity=D(qty), unit_price=D(price))
        if send:
            send_po(po)
        return po

    def member(self, username, role="Administrator", company=None, password="pw-test-1234"):
        """Create a login that is a member of `company` (default: the first company) with a built-in role."""
        from core.models import Membership
        from core.permissions import ensure_roles

        company = company or Company.get_default()
        user = get_user_model().objects.create_user(username, password=password)
        Membership.objects.create(user=user, company=company, role=ensure_roles(company)[role])
        return user

    @staticmethod
    def balance(key):
        return acct(key).balance()
