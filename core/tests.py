"""Smoke test: load the demo dataset and render every page."""
import io
import re

from django.core.management import call_command
from django.test import TestCase
from django.urls import URLResolver, get_resolver

from accounting.models import Account, CustomerInvoice, CustomerPayment, JournalEntry, VendorBill, VendorPayment
from accounting.services import trial_balance
from inventory.models import StockAdjustment, StockTransfer, Warehouse
from products.models import Category, PriceList, Product
from purchasing.models import GoodsReceipt, PurchaseOrder, Shipment, Vendor
from sales.models import Customer, Delivery, SalesOrder
from salesteam.models import SalesRep

DETAIL_MODELS = {
    "products/items/": Product, "products/categories/": Category, "products/price-lists/": PriceList,
    "inventory/warehouses/": Warehouse, "inventory/adjustments/": StockAdjustment,
    "inventory/transfers/": StockTransfer, "purchasing/vendors/": Vendor, "purchasing/orders/": PurchaseOrder,
    "purchasing/shipments/": Shipment, "purchasing/receipts/": GoodsReceipt, "sales/customers/": Customer,
    "sales/orders/": SalesOrder, "sales/deliveries/": Delivery, "accounting/accounts/": Account,
    "accounting/journal/": JournalEntry, "accounting/invoices/": CustomerInvoice,
    "accounting/customer-payments/": CustomerPayment, "accounting/bills/": VendorBill,
    "accounting/vendor-payments/": VendorPayment, "sales-team/reps/": SalesRep,
}


def walk(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from walk(p.url_patterns, prefix + str(p.pattern))
        else:
            yield prefix + str(p.pattern), p


class SmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=io.StringIO())

    def test_demo_books_balance(self):
        tb = trial_balance()
        self.assertEqual(tb["total_debit"], tb["total_credit"])
        self.assertGreater(tb["total_debit"], 0)

    def test_every_staff_page_renders(self):
        self.client.login(username="admin", password="admin12345")
        checked = 0
        for route, p in walk(get_resolver().url_patterns):
            if route.startswith(("admin", "portal/", "login", "logout", "media")):
                continue
            if "<" not in route:
                urls = ["/" + route]
            elif route.endswith(("<int:pk>/", "<int:pk>/edit/", "<int:pk>/statement/", "<int:pk>/receive/",
                                 "<int:pk>/ship/")):
                base = route.split("<")[0]
                model = DETAIL_MODELS.get(base)
                if route.endswith("<int:pk>/receive/") and model is not PurchaseOrder:
                    continue  # shipment receiving is POST-only
                if model is None:
                    continue
                urls = ["/" + re.sub(r"<int:pk>", str(o.pk), route) for o in model.objects.all()[:3]]
            else:
                continue
            for url in urls:
                resp = self.client.get(url, follow=True)
                self.assertEqual(resp.status_code, 200, url)
                checked += 1
        self.assertGreater(checked, 120)

    def test_portal_pages_render(self):
        self.client.login(username="sunrise", password="demo12345")
        for url in ["/portal/", "/portal/orders/", "/portal/shipments/", "/portal/shipments/new/",
                    "/portal/invoices/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_login_required(self):
        self.assertRedirects(self.client.get("/sales/orders/"), "/login/?next=/sales/orders/")
