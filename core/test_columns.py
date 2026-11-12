from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import SavedFilter, UserPreference
from core.testing import TODAY, Fixture
from products.models import ItemClass, LookupValue
from products.sample_setup import load_sample_setup
from sales.models import CustomerBuyer, SalesOrder, SalesOrderLine


class ColumnChooserTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        self.client.login(username="admin", password="pw-admin-123")

    def test_sales_order_list_columns_saved_per_user(self):
        buyer = CustomerBuyer.objects.create(customer=self.f.customer, name="Rachel Kim")
        so = SalesOrder.objects.create(customer=self.f.customer, buyer=buyer, order_date=TODAY, warehouse=self.f.wh)
        SalesOrderLine.objects.create(order=so, product=self.f.a, quantity=D(1), unit_price=D(5))
        page = self.client.get("/sales/orders/").content.decode()
        self.assertIn("☰ Columns", page)
        self.assertNotIn("Rachel Kim", page)  # buyer column is optional
        self.client.post("/settings/columns/", {"screen": "list:sales.salesorder", "col": ["number", "buyer", "total"],
                                                "next": "/sales/orders/"})
        resp = self.client.get("/sales/orders/")
        self.assertEqual([a for _, a in resp.context["columns"]], ["number", "buyer", "total"])
        self.assertContains(resp, "Rachel Kim")
        # another user still sees the defaults
        other = get_user_model().objects.create_user("clerk", password="pw-clerk-123")
        self.client.force_login(other)
        self.assertIn("status", [a for _, a in self.client.get("/sales/orders/").context["columns"]])
        self.client.force_login(self.f.user)
        self.client.post("/settings/columns/", {"screen": "list:sales.salesorder", "reset": "1", "col": ["number"]})
        self.assertFalse(UserPreference.objects.filter(key="columns:list:sales.salesorder").exists())

    def test_po_list_offers_first_sale_and_unsafe_next_is_ignored(self):
        self.f.po([(self.f.a, 10, 5)])
        self.client.post("/settings/columns/", {"screen": "list:purchasing.purchaseorder",
                                                "col": ["number", "fs_totals.usd"], "next": "/purchasing/orders/"})
        self.assertContains(self.client.get("/purchasing/orders/"), "Ext FS cost USD")
        resp = self.client.post("/settings/columns/", {"screen": "x", "col": ["a"], "next": "https://evil.example/"})
        self.assertEqual(resp["Location"], "/")

    def test_saved_filters_private_and_shared(self):
        self.client.post("/settings/filters/save/", {"screen": "availability:inventory", "name": "Baby Q4",
                                                     "query": "season=1&show=all", "next": "/inventory/availability/"})
        self.client.post("/settings/filters/save/", {"screen": "availability:inventory", "name": "Team view",
                                                     "query": "show=short", "shared": "1"})
        self.assertEqual(SavedFilter.objects.count(), 2)
        page = self.client.get("/inventory/availability/").content.decode()
        self.assertIn("/inventory/availability/?season=1&amp;show=all", page)
        other = get_user_model().objects.create_user("clerk", password="pw-clerk-123")
        self.client.force_login(other)
        page = self.client.get("/inventory/availability/").content.decode()
        self.assertIn("Team view", page)
        self.assertNotIn("Baby Q4", page)
        mine = SavedFilter.objects.get(name="Baby Q4")
        self.client.post(f"/settings/filters/{mine.pk}/delete/")  # not the owner: ignored
        self.assertTrue(SavedFilter.objects.filter(pk=mine.pk).exists())


class AvailabilityFiltersTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        load_sample_setup(enrich_demo_products=False)
        lv = lambda k, c: LookupValue.objects.get(kind=k, code=c)  # noqa: E731
        self.mittens = ItemClass.objects.get(code="MITTENS")
        a = self.f.a
        a.item_class, a.style_number, a.customer_code = self.mittens, "KG-MIT-01", lv("customer_code", "WINNERS")
        a.size_range, a.material, a.brand = lv("size_range", "INFANT"), lv("material", "INTLCK60COT40PLY"), \
            lv("brand", "KENSIEGIRL")
        a.save()
        self.client.login(username="admin", password="pw-admin-123")

    def skus(self, query):
        resp = self.client.get(f"/inventory/availability/?show=all&{query}")
        return [p.sku for p, _ in resp.context["table"]]

    def test_filters(self):
        lv = lambda k, c: LookupValue.objects.get(kind=k, code=c).pk  # noqa: E731
        self.assertEqual(self.skus(""), ["A", "B"])
        self.assertEqual(self.skus(f"product_type={self.mittens.pk}"), ["A"])
        self.assertEqual(self.skus("style=MIT"), ["A"])
        self.assertEqual(self.skus("sku=b"), ["B"])
        self.assertEqual(self.skus(f"customer_code={lv('customer_code', 'WINNERS')}"), ["A"])
        self.assertEqual(self.skus(f"size_range={lv('size_range', 'INFANT')}"), ["A"])
        self.assertEqual(self.skus(f"material={lv('material', 'INTLCK60COT40PLY')}&brand={lv('brand', 'KENSIEGIRL')}"),
                         ["A"])
        page = self.client.get("/inventory/availability/").content.decode()
        self.assertIn('<optgroup label="Baby / Baby Accessories">', page)
        self.assertIn(">Mittens</option>", page)

    def test_attribute_columns_and_export_follow_chooser(self):
        self.client.post("/settings/columns/", {"screen": "availability:sales",
                                                "col": ["style_number", "product_type", "customer_code", "ats"]})
        resp = self.client.get("/inventory/sales-availability/?show=all")
        self.assertEqual([c["key"] for c in resp.context["cols"]], ["style_number", "product_type", "customer_code",
                                                                    "ats"])
        row = dict((c["key"], v) for c, v in resp.context["table"][0][1])
        self.assertEqual((row["style_number"], row["product_type"]), ("KG-MIT-01", "Mittens"))
        import io

        from openpyxl import load_workbook

        ws = load_workbook(io.BytesIO(self.client.get(
            "/inventory/availability/export.xlsx?screen=sales&show=all").content)).active
        self.assertEqual([ws.cell(2, c).value for c in range(1, 7)],
                         ["Image", "SKU", "Style #", "Product Type", "Customer Code", "Available to Ship"])
        # the Inventory screen keeps its own (default) columns
        inv = self.client.get("/inventory/availability/?show=all")
        self.assertIn("so_open", [c["key"] for c in inv.context["cols"]])


class BuyerTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        self.client.login(username="admin", password="pw-admin-123")

    def order_post(self, buyer):
        return self.client.post("/sales/orders/new/", {
            "customer": self.f.customer.pk, "buyer": buyer.pk, "order_date": "2026-06-15",
            "ship_to": self.f.customer.default_location.pk,
            "warehouse": self.f.wh.pk, "tax_rate": "0", "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000", "lines-0-product": self.f.a.pk,
            "lines-0-warehouse": self.f.wh.pk, "lines-0-quantity": "2", "lines-0-discount_pct": "0"})

    def test_buyers_on_customer_and_order(self):
        from sales.models import Customer

        resp = self.client.post(f"/sales/customers/{self.f.customer.pk}/edit/", {
            "code": "C1", "name": "Retailer", "credit_limit": "0", "payment_terms_days": "30", "is_active": "on",
            "lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000", "lines-0-name": "Rachel Kim", "lines-0-title": "Senior Buyer",
            "lines-0-is_active": "on", "lines-1-name": "Tom Alvarez", "lines-1-is_active": "on"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(sorted(self.f.customer.buyers.values_list("name", flat=True)), ["Rachel Kim", "Tom Alvarez"])
        form_page = self.client.get(f"/sales/customers/{self.f.customer.pk}/edit/").content.decode()
        self.assertIn("<h3>Buyers</h3>", form_page)
        rachel = self.f.customer.buyers.get(name="Rachel Kim")
        self.order_post(rachel)
        so = SalesOrder.objects.get()
        self.assertEqual(so.buyer, rachel)
        self.assertContains(self.client.get(so.get_absolute_url()), "<dt>Buyer</dt><dd>Rachel Kim")
        # a buyer from another customer is rejected
        other = Customer.objects.create(code="C2", name="Other Store")
        stranger = CustomerBuyer.objects.create(customer=other, name="Someone Else")
        resp = self.order_post(stranger)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "is a buyer for Other Store")
        self.assertIn(f'data-customer="{other.pk}"', self.client.get("/sales/orders/new/").content.decode())
