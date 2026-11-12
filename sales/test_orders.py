from decimal import Decimal as D

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from accounting.services import post_customer_invoice
from core.models import Attachment, EmailLog, Note
from core.testing import TODAY, Fixture
from inventory.models import StockAdjustment, StockAdjustmentLine
from inventory.services import post_adjustment
from sales import services
from sales.models import Customer, CustomerLocation, SalesOrder, SalesOrderLine


class OrderLifecycleTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        adj = StockAdjustment.objects.create(warehouse=self.f.wh, date=TODAY, reason="opening")
        StockAdjustmentLine.objects.create(adjustment=adj, product=self.f.a, quantity=D(100), unit_cost=D(10))
        post_adjustment(adj)
        self.client.login(username="admin", password="pw-admin-123")

    def order(self, qty=10):
        so = SalesOrder.objects.create(customer=self.f.customer, order_date=TODAY, warehouse=self.f.wh)
        services.copy_order_defaults(so)
        so.save()
        SalesOrderLine.objects.create(order=so, product=self.f.a, quantity=D(qty), unit_price=D(20))
        return so

    def post_edit(self, so, qty, extra=None):
        line = so.lines.first()
        data = {"customer": so.customer_id, "ship_to": so.ship_to_id, "order_date": "2026-06-15",
                "warehouse": self.f.wh.pk, "tax_rate": "0", "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "1",
                "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000", "lines-0-id": line.pk,
                "lines-0-product": self.f.a.pk, "lines-0-warehouse": self.f.wh.pk, "lines-0-quantity": str(qty),
                "lines-0-unit_price": "20", "lines-0-discount_pct": "0"}
        data.update(extra or {})
        return self.client.post(f"/sales/orders/{so.pk}/edit/", data)

    def test_new_customer_gets_main_location_and_order_ship_to(self):
        c = Customer.objects.create(code="TJ100", name="Tj Maxx", address="770 Cochituate Rd", city="Framingham",
                                    state="MA", postal_code="01701", country="US")
        main = c.default_location
        self.assertEqual((main.location_id, main.is_default), ("MAIN", True))
        store = CustomerLocation.objects.create(customer=c, location_id="893", name="TJ MAXX #893", city="Dallas",
                                                state="TX", is_default=True)
        main.refresh_from_db()
        self.assertFalse(main.is_default)  # only one default
        so = SalesOrder(customer=c, order_date=TODAY, warehouse=self.f.wh)
        services.copy_order_defaults(so)
        self.assertEqual(so.ship_to, store)
        self.assertIn("TJ MAXX #893", so.shipping_address)

    def test_ship_to_must_belong_to_customer_and_dates_checked(self):
        so = self.order()
        other = Customer.objects.create(code="FD100", name="Family Dollar")
        resp = self.post_edit(so, 10, {"ship_to": other.default_location.pk})
        self.assertContains(resp, "is a location of Family Dollar")
        resp = self.post_edit(so, 10, {"ship_start_date": "2026-07-10", "cancel_date": "2026-07-01"})
        self.assertContains(resp, "Cancel date is before the start ship date")
        self.post_edit(so, 10, {"customer_po": "PO-1", "master_customer_po": "MPO-9", "ship_start_date": "2026-07-01",
                                "cancel_date": "2026-07-15"})
        so.refresh_from_db()
        self.assertEqual((so.master_customer_po, str(so.cancel_date)), ("MPO-9", "2026-07-15"))

    def test_edit_open_order_until_released(self):
        so = self.order()
        services.confirm_order(so)
        self.post_edit(so, 12)
        self.assertEqual(so.lines.first().quantity, D(12))
        services.release_to_warehouse(so, self.f.user)
        so.refresh_from_db()
        self.assertFalse(so.is_editable)
        resp = self.client.get(f"/sales/orders/{so.pk}/edit/")
        self.assertRedirects(resp, so.get_absolute_url())
        self.post_edit(so, 30)
        self.assertEqual(so.lines.first().quantity, D(12))  # locked
        services.recall_from_warehouse(so)
        so.refresh_from_db()
        self.assertTrue(so.is_editable)

    def test_shipped_lines_are_protected(self):
        so = self.order()
        services.confirm_order(so)
        services.create_delivery(so, {so.lines.first().pk: D(6)}, TODAY)
        so.refresh_from_db()
        self.assertEqual(so.status, SalesOrder.PARTIAL)
        resp = self.post_edit(so, 4)
        self.assertContains(resp, "Cannot be less than the 6 already shipped")
        self.post_edit(so, 8)  # reduce the open balance: fine
        so.refresh_from_db()
        self.assertEqual(so.lines.first().quantity, D(8))
        resp = self.post_edit(so, 8, {"lines-0-DELETE": "on"})
        self.assertContains(resp, "cannot be deleted")

    def test_hold_blocks_shipping_and_release(self):
        so = self.order()
        services.confirm_order(so)
        services.hold_order(so, "Waiting for deposit")
        with self.assertRaisesMessage(ValidationError, "on hold"):
            services.create_delivery(so, {so.lines.first().pk: D(1)}, TODAY)
        with self.assertRaises(ValidationError):
            services.release_to_warehouse(so)
        services.release_hold(so)
        services.create_delivery(so, {so.lines.first().pk: D(1)}, TODAY)

    def test_reopen_cancelled_and_closed(self):
        so = self.order()
        services.cancel_order(so)
        services.reopen_order(so)
        so.refresh_from_db()
        self.assertEqual(so.status, SalesOrder.QUOTE)
        services.confirm_order(so)
        services.create_delivery(so, {so.lines.first().pk: D(10)}, TODAY)
        post_customer_invoice(services.create_invoice_from_order(so, TODAY))
        so.refresh_from_db()
        self.assertEqual(so.status, SalesOrder.CLOSED)
        self.assertFalse(so.is_editable)
        services.reopen_order(so)
        so.refresh_from_db()
        self.assertTrue(so.is_editable)
        self.post_edit(so, 15)  # add 5 more units
        so.refresh_from_db()
        self.assertEqual(so.status, SalesOrder.BACKORDER)
        self.assertFalse(so.reopened)

    def test_location_import(self):
        import io

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["Location ID", "Location Name", "City", "State", "Country", "Default"])
        ws.append([8881, "Family Dollar Stores Inc #8881", "Charlotte", "NC", "US", ""])
        ws.append(["9510", "Family Dollar Stores Inc #9510", "Ashley", "IN", "US", "Y"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = "locations.xlsx"
        self.client.post(f"/sales/customers/{self.f.customer.pk}/locations/import/", {"file": buf})
        locs = {loc.location_id: loc for loc in self.f.customer.locations.all()}
        self.assertEqual(sorted(locs), ["8881", "9510", "MAIN"])
        self.assertTrue(locs["9510"].is_default)
        self.assertEqual(locs["8881"].city, "Charlotte")
        self.assertEqual(self.client.get(f"/sales/customers/{self.f.customer.pk}/locations/export.xlsx").status_code,
                         200)


class DocumentsAndEmailTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        self.client.login(username="admin", password="pw-admin-123")
        self.so = SalesOrder.objects.create(customer=self.f.customer, order_date=TODAY, warehouse=self.f.wh,
                                            customer_po="TJ-4410")
        services.copy_order_defaults(self.so)
        self.so.save()
        SalesOrderLine.objects.create(order=self.so, product=self.f.a, quantity=D(3), unit_price=D(20))
        self.f.customer.email = "ap@retailer.example"
        self.f.customer.save()

    def test_files_and_notes(self):
        from django.contrib.contenttypes.models import ContentType
        from django.core.files.uploadedfile import SimpleUploadedFile

        ct = ContentType.objects.get_for_model(self.so).pk
        self.client.post(f"/settings/docs/{ct}/{self.so.pk}/files/upload/",
                         {"files": SimpleUploadedFile("routing.pdf", b"%PDF-1.4 test", "application/pdf")})
        att = Attachment.objects.get()
        self.assertEqual((att.filename, att.size), ("routing.pdf", 13))
        self.assertEqual(self.client.get(f"/settings/docs/files/{att.pk}/").content, b"%PDF-1.4 test")
        self.client.post(f"/settings/docs/{ct}/{self.so.pk}/notes/add/", {"text": "Customer asked to ship early"})
        page = self.client.get(self.so.get_absolute_url()).content.decode()
        self.assertIn("routing.pdf", page)
        self.assertIn("Customer asked to ship early", page)
        self.client.post(f"/settings/docs/notes/{Note.objects.get().pk}/delete/")
        self.assertFalse(Note.objects.exists())

    def test_print_and_pdf(self):
        html = self.client.get(f"/settings/print/sales_order/{self.so.pk}/").content.decode()
        self.assertIn("TJ-4410", html)
        pdf = self.client.get(f"/settings/print/sales_order/{self.so.pk}/?format=pdf")
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        po = self.f.po([(self.f.a, 5, 10)])
        self.assertTrue(self.client.get(f"/settings/print/purchase_order/{po.pk}/?format=pdf").content
                        .startswith(b"%PDF"))
        self.assertTrue(self.client.get(f"/settings/print/statement/{self.f.customer.pk}/?format=pdf").content
                        .startswith(b"%PDF"))

    def test_email_not_configured_is_logged(self):
        page = self.client.get(f"/settings/email/sales_order/{self.so.pk}/").content.decode()
        self.assertIn("ap@retailer.example", page)
        self.assertIn("TJ-4410", page)  # placeholder filled in the default template
        self.client.post(f"/settings/email/sales_order/{self.so.pk}/", {
            "to": "ap@retailer.example", "subject": "Order", "body": "Hi", "attach_pdf": "1"})
        log = EmailLog.objects.get()
        self.assertEqual(log.status, EmailLog.NOT_CONFIGURED)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_CONFIGURED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_sends_with_pdf_and_files(self):
        from django.contrib.contenttypes.models import ContentType

        att = Attachment.objects.create(content_type=ContentType.objects.get_for_model(self.so), object_id=self.so.pk,
                                        filename="spec.txt", mime_type="text/plain", size=2, data=b"hi")
        self.client.post(f"/settings/email/sales_order/{self.so.pk}/", {
            "to": "buyer@retailer.example; second@retailer.example", "cc": "", "subject": "Order SO",
            "body": "Attached", "attach_pdf": "1", "files": [att.pk]})
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["buyer@retailer.example", "second@retailer.example"])
        names = [a[0] for a in msg.attachments]
        self.assertEqual(names, [f"Order-{self.so.number}.pdf", "spec.txt"])
        self.assertEqual(EmailLog.objects.get().status, EmailLog.SENT)
        self.assertContains(self.client.get(self.so.get_absolute_url()), "Order SO")  # shown in the email history
