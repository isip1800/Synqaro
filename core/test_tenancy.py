from decimal import Decimal as D

from django.test import TestCase

from accounting.models import Account, JournalEntry
from core.models import Company, Membership, Role
from core.services import create_company
from core.tenancy import use_company
from core.testing import TODAY, Fixture
from inventory.models import Warehouse
from products.models import Product
from purchasing.models import PurchaseOrder, PurchaseOrderLine
from sales.models import Customer


class MultiCompanyTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        self.a = Company.get_default()
        self.owner = self.f.member("owner", role="Owner")
        self.b = create_company("Second Co", owner=self.owner)

    def test_new_company_is_set_up(self):
        with use_company(self.b):
            self.assertTrue(Account.objects.filter(system_key="AR").exists())
            self.assertEqual(list(Warehouse.objects.values_list("code", flat=True)), ["MAIN"])
            self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(Membership.objects.get(user=self.owner, company=self.b).role.name, "Owner")
        self.assertEqual(Role.objects.filter(company=self.b).count(), 8)

    def test_data_is_isolated_and_codes_numbers_are_per_company(self):
        with use_company(self.b):
            Product.objects.create(sku="A", name="Same SKU, other company")
            c = Customer.objects.create(code="C1", name="Same code")
            self.assertEqual(c.default_location.location_id, "MAIN")
            po = PurchaseOrder.objects.create(vendor=self._vendor(), order_date=TODAY, currency_id="USD",
                                              warehouse=Warehouse.objects.get())
            PurchaseOrderLine.objects.create(order=po, product=Product.objects.get(), quantity=D(1), unit_price=D(1))
            self.assertEqual(Product.objects.count(), 1)
            self.assertEqual(po.number, "PO-00001")
            self.assertEqual(po.lines.get().company_id, self.b.pk)
        with use_company(self.a):
            self.assertEqual(sorted(Product.objects.values_list("sku", flat=True)), ["A", "B"])
            po_a = self.f.po([(self.f.a, 1, 1)])
            self.assertEqual(po_a.number, "PO-00001")  # own sequence per company
            self.assertFalse(Account.objects.filter(company=self.b).exists())

    def _vendor(self):
        from purchasing.models import Vendor

        return Vendor.objects.create(code="V1", name="Vendor B")

    def test_switching_and_url_isolation(self):
        with use_company(self.b):
            p_b = Product.objects.create(sku="ONLY-B", name="B item")
        self.client.login(username="owner", password="pw-test-1234")
        self.client.post("/settings/switch-company/", {"company_id": self.a.pk})
        self.assertNotContains(self.client.get("/products/items/"), "ONLY-B")
        self.assertEqual(self.client.get(f"/products/items/{p_b.pk}/").status_code, 404)
        self.client.post("/settings/switch-company/", {"company_id": self.b.pk})
        self.assertContains(self.client.get("/products/items/"), "ONLY-B")
        self.assertEqual(self.client.get(f"/products/items/{self.f.a.pk}/").status_code, 404)
        page = self.client.get("/").content.decode()
        self.assertIn('name="company_id"', page)  # switcher shown for 2+ companies

    def test_cannot_switch_into_a_company_you_dont_belong_to(self):
        user = self.f.member("clerk")  # member of A only
        self.client.login(username="clerk", password="pw-test-1234")
        self.client.post("/settings/switch-company/", {"company_id": self.b.pk})
        self.assertEqual(self.client.get("/").context["company"].pk, self.a.pk)
        self.assertEqual(user.memberships.count(), 1)

    def test_login_without_membership_gets_no_access(self):
        from django.contrib.auth import get_user_model

        get_user_model().objects.create_user("stranger", password="pw-test-1234")
        self.client.login(username="stranger", password="pw-test-1234")
        resp = self.client.get("/sales/orders/")
        self.assertEqual(resp.status_code, 403)
        self.assertContains(resp, "No company access", status_code=403)

    def test_create_company_screen_copies_setup(self):
        from products.models import ItemClass
        from products.sample_setup import load_sample_setup

        load_sample_setup(enrich_demo_products=False)
        self.client.login(username="owner", password="pw-test-1234")
        self.client.post("/settings/switch-company/", {"company_id": self.a.pk})
        self.client.post("/settings/companies/", {"name": "Third Co", "base_currency": "USD", "copy_setup": "on"})
        third = Company.objects.get(name="Third Co")
        with use_company(third):
            self.assertTrue(ItemClass.objects.filter(code="MITTENS").exists())


class PermissionTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        self.po = self.f.po([(self.f.a, 10, 7)])
        self.f.a.avg_cost = D("12.3456")
        self.f.a.save()

    def login(self, role):
        self.f.member(role.lower().replace(" ", ""), role=role)
        self.client.login(username=role.lower().replace(" ", ""), password="pw-test-1234")

    def test_warehouse_role(self):
        self.login("Warehouse")
        self.assertEqual(self.client.get("/accounting/journal/").status_code, 403)
        self.assertEqual(self.client.post("/accounting/journal/new/", {}).status_code, 403)
        self.assertEqual(self.client.get("/settings/company/").status_code, 403)
        self.assertEqual(self.client.get("/inventory/adjustments/new/").status_code, 200)
        page = self.client.get(f"/products/items/{self.f.a.pk}/").content.decode()
        self.assertNotIn("12.3456", page)
        self.assertNotIn("Avg landed cost", page)
        po_page = self.client.get(f"/purchasing/orders/{self.po.pk}/").content.decode()
        self.assertNotIn("Unit price", po_page)
        self.assertNotIn("Bill this PO", po_page)
        self.assertEqual(self.client.get(f"/purchasing/orders/{self.po.pk}/receive/").status_code, 200)
        self.assertEqual(self.client.get(f"/settings/print/purchase_order/{self.po.pk}/").status_code, 403)
        self.assertEqual(self.client.get("/purchasing/landed-costs/").status_code, 403)
        nav = self.client.get("/").content.decode()
        self.assertNotIn("Chart of Accounts", nav)
        self.assertIn("Stock on Hand", nav)
        cols = [k for k, _l, _on in self.client.get("/products/items/").context["chooser"]["options"]]
        self.assertNotIn("avg_cost", cols)

    def test_sales_role_cannot_approve_or_post(self):
        from sales.models import SalesOrder, SalesOrderLine
        from sales.services import confirm_order

        so = SalesOrder.objects.create(customer=self.f.customer, order_date=TODAY, warehouse=self.f.wh)
        SalesOrderLine.objects.create(order=so, product=self.f.a, quantity=D(1), unit_price=D(1))
        self.f.customer.credit_limit = D("0.01")
        self.f.customer.save()
        confirm_order(so)
        self.login("Sales")
        self.assertEqual(self.client.get("/sales/orders/").status_code, 200)
        self.assertEqual(self.client.post(f"/sales/orders/{so.pk}/approve/").status_code, 403)
        self.assertEqual(self.client.get("/accounting/invoices/").status_code, 403)
        self.assertEqual(self.client.get("/settings/users/").status_code, 403)

    def test_accounting_role_posts_journals(self):
        self.login("Accounting")
        self.assertEqual(self.client.get("/accounting/journal/new/").status_code, 200)
        self.assertEqual(self.client.get(f"/products/items/{self.f.a.pk}/").status_code, 200)
        self.assertEqual(self.client.post("/products/items/new/", {}).status_code, 403)  # products view only

    def test_owner_manages_users_and_roles(self):
        self.login("Owner")
        self.client.post("/settings/users/new/", {"username": "picker", "password": "pw-picker-123",
                                                  "role": Role.objects.get(company=Company.get_default(),
                                                                           name="Warehouse").pk,
                                                  "is_active": "on"})
        m = Membership.objects.get(user__username="picker")
        self.assertEqual(m.role.name, "Warehouse")
        self.client.post("/settings/roles/new/", {"name": "Customer Service", "perm": ["sales.view", "sales.edit"]})
        role = Role.objects.get(name="Customer Service")
        self.assertEqual(sorted(role.permissions), ["sales.edit", "sales.view"])
        owner_m = Membership.objects.get(user__username="owner")
        self.client.post(f"/settings/users/{owner_m.pk}/edit/", {"username": "owner", "role": role.pk})
        owner_m.refresh_from_db()
        self.assertEqual(owner_m.role.name, "Owner")  # last owner cannot be demoted

    def test_journal_entries_are_unreachable_across_companies(self):
        from accounting.services import acct, post_journal

        entry = post_journal(TODAY, "A only", [{"account": acct("CASH"), "debit": D(1)},
                                               {"account": acct("EQUITY"), "credit": D(1)}])
        owner = self.f.member("bowner")
        b = create_company("B", owner=owner)
        self.client.login(username="bowner", password="pw-test-1234")
        self.client.post("/settings/switch-company/", {"company_id": b.pk})
        self.assertEqual(self.client.get(f"/accounting/journal/{entry.pk}/").status_code, 404)
        with use_company(b):
            self.assertEqual(JournalEntry.objects.count(), 0)


class CreateButtonTests(TestCase):
    def test_new_buttons_follow_permissions(self):
        f = Fixture()
        f.member("rep", role="Sales")
        self.client.login(username="rep", password="pw-test-1234")
        self.assertIsNone(self.client.get("/purchasing/receipts/").context.get("create_url"))
        self.assertNotIn("+ New item", self.client.get("/products/items/").content.decode())
        self.assertIsNotNone(self.client.get("/sales/orders/").context["create_url"])
