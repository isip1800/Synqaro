from decimal import Decimal as D

from django.test import TestCase

from core.audit import history_for
from core.models import AuditLog
from core.services import create_company
from core.tenancy import use_company
from core.testing import TODAY, Fixture
from sales import services
from sales.models import SalesOrder, SalesOrderLine


class ChangeHistoryTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        self.client.login(username="admin", password="pw-admin-123")

    def make_order(self):
        so = SalesOrder.objects.create(customer=self.f.customer, order_date=TODAY, warehouse=self.f.wh)
        services.copy_order_defaults(so)
        so.save()
        SalesOrderLine.objects.create(order=so, product=self.f.a, quantity=D(10), unit_price=D(20))
        return so

    def test_edit_through_screen_records_user_and_field_changes(self):
        so = self.make_order()
        line = so.lines.get()
        self.client.post(f"/sales/orders/{so.pk}/edit/", {
            "customer": so.customer_id, "ship_to": so.ship_to_id, "order_date": "2026-06-15",
            "customer_po": "PO-777", "warehouse": self.f.wh.pk, "tax_rate": "0", "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "1", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000",
            "lines-0-id": line.pk, "lines-0-product": self.f.a.pk, "lines-0-warehouse": self.f.wh.pk,
            "lines-0-quantity": "15", "lines-0-unit_price": "20", "lines-0-discount_pct": "0"})
        entries = list(history_for(so))
        header = next(e for e in entries if e.content_type.model == "salesorder" and "customer_po" in e.changes)
        self.assertEqual(header.changes["customer_po"], ["", "PO-777"])
        self.assertEqual(header.user, self.f.user)
        line_change = next(e for e in entries if e.content_type.model == "salesorderline" and e.action == "update")
        self.assertEqual(line_change.changes["quantity"], ["10", "15"])
        page = self.client.get(so.get_absolute_url()).content.decode()
        self.assertIn("PO-777", page)
        self.assertIn('class="old">10</span> → <span class="new">15</span>', page)

    def test_status_changes_and_deletes_are_logged(self):
        so = self.make_order()
        services.confirm_order(so)
        status = AuditLog.objects.filter(object_id=so.pk, content_type__model="salesorder", action="update").first()
        self.assertEqual(status.changes["status"], ["Quotation", "Open"])
        line = so.lines.get()
        line.delete()
        self.assertTrue(AuditLog.objects.filter(action="delete", content_type__model="salesorderline",
                                                parent_id=so.pk).exists())

    def test_history_is_per_company_and_costs_hidden(self):
        po = self.f.po([(self.f.a, 10, 5)], send=False)
        line = po.lines.get()
        line.unit_price = D("6")
        line.save()
        owner = self.f.member("bowner")
        b = create_company("B", owner=owner)
        with use_company(b):
            self.assertFalse(AuditLog.objects.filter(content_type__model__in=["purchaseorder", "purchaseorderline"])
                             .exists())
            self.assertTrue(AuditLog.objects.filter(content_type__model="account").exists())  # B's own setup
        self.f.member("picker", role="Warehouse")
        self.client.login(username="picker", password="pw-test-1234")
        page = self.client.get(f"/purchasing/orders/{po.pk}/").content.decode()
        self.assertNotIn('class="new">6</span>', page)
        self.client.login(username="admin", password="pw-admin-123")
        page = self.client.get(f"/purchasing/orders/{po.pk}/").content.decode()
        self.assertIn('class="new">6</span>', page)
        log = self.client.get("/settings/change-log/?q=PO-00001")
        self.assertEqual(log.status_code, 200)
