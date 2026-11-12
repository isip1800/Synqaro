import io
import zipfile
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.models import Company
from core.testing import Fixture
from inventory.models import Warehouse
from products import services
from products.models import HTSCode, ItemClass, LookupValue, Product, ProductCrossReference, ProductWarehouse
from products.sample_setup import load_sample_setup
from products.upc import build, check_digit, complete, is_valid


def png_bytes(color="red"):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (800, 400), color).save(buf, "PNG")
    return buf.getvalue()


class UPCTests(TestCase):
    def test_check_digit_matches_real_barcode(self):
        self.assertEqual(check_digit("67619100973"), 5)
        self.assertEqual(build("676191", 973), "676191009735")
        self.assertTrue(is_valid("676191009735"))
        self.assertFalse(is_valid("676191009736"))
        self.assertEqual(complete("67619100973"), "676191009735")

    def test_prefix_validation_and_range(self):
        with self.assertRaises(ValueError):
            build("123", 1)
        with self.assertRaises(ValueError):
            build("1234567890", 10)  # only 1 digit of item reference left


class ProductSetupTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        load_sample_setup(enrich_demo_products=False)
        self.lv = lambda kind, code: LookupValue.objects.get(kind=kind, code=code)
        self.panties = ItemClass.objects.get(code="10PKPANTIE")

    def make(self, **kw):
        fields = dict(sku="14241190", name="10 Pack Panties - Hearts", item_class=self.panties,
                      material=self.lv("material", "INT100COT"), gender=self.lv("gender", "Girl"))
        fields.update(kw)
        return Product.objects.create(**fields)

    def test_item_class_tiers(self):
        self.assertEqual(self.panties.level, 3)
        self.assertEqual(self.panties.full_code, "INT -UNDERWEAR -10PKPANTIE")
        self.assertEqual(self.panties.full_description, "Basics / Underwear / 10 Pack Panties")
        self.assertEqual(self.panties.tier(1).description, "Basics")
        with self.assertRaises(ValidationError):
            ItemClass.objects.create(parent=self.panties, code="X", description="Too deep")

    def test_hts_applied_from_table_and_override(self):
        p = self.make()
        self.assertEqual((p.hs_code, p.duty_rate, p.tariff_rate), ("6108.21.00200", D("7.80"), D("7.50")))
        self.assertEqual(p.total_duty_rate, D("15.30"))
        # Table change flows to products
        row = HTSCode.objects.get(item_class=self.panties)
        row.duty_rate = D("8.00")
        row.save()
        p.refresh_from_db()
        self.assertEqual(p.duty_rate, D("8.00"))
        # Override keeps manual values
        p.hts_override, p.hs_code, p.duty_rate = True, "9999.99", D("1")
        p.save()
        row.duty_rate = D("9.00")
        row.save()
        p.refresh_from_db()
        self.assertEqual((p.hs_code, p.duty_rate), ("9999.99", D("1.000")))

    def test_gender_blank_row_is_fallback(self):
        mat = self.lv("material", "INT100COT")
        HTSCode.objects.create(item_class=self.panties, material=mat, gender=None, hts_code="6108.22", duty_rate=1)
        p = self.make(gender=self.lv("gender", "Boy"))
        self.assertEqual(p.hs_code, "6108.22")

    def test_container_quantities(self):
        p = self.make(carton_qty=120, carton_cbm=D("0.1229"))
        caps = {label: units for label, _, _, units in p.container_capacity}
        # full cartons only: 67/0.1229 = 545 cartons x 120
        self.assertEqual(caps["40' HQ"], 545 * 120)
        self.assertEqual(caps["20'"], 219 * 120)
        self.assertEqual(caps["45'"], 618 * 120)
        self.assertEqual(p.carton_cuft, D("4.3402"))
        self.assertEqual(p.volume_cbm, D("0.001024"))

    def test_carton_cbm_from_dimensions(self):
        p = self.make(carton_qty=12, carton_length=24, carton_width=16, carton_height=14, dimension_unit="in")
        self.assertEqual(p.carton_cbm, D("0.0881"))
        q = self.make(sku="CM", carton_qty=10, carton_length=60, carton_width=40, carton_height=50,
                      dimension_unit="cm")
        self.assertEqual(q.carton_cbm, D("0.1200"))
        company = Company.get()
        company.cbm_40hq = D("68")
        company.save()
        self.assertEqual(q.qty_per_40hq, 566 * 10)

    def test_generate_upc_sequence_skips_used(self):
        company = Company.get()
        company.upc_company_prefix, company.upc_next_item_ref = "676191", 973
        company.save()
        self.make(sku="OLD", barcode="676191009735")  # already used
        p = self.make(sku="NEW")
        upc = services.generate_upc(p)
        self.assertEqual(upc, build("676191", 974))
        p.refresh_from_db()
        self.assertEqual(p.barcode, upc)
        self.assertTrue(ProductCrossReference.objects.filter(product=p, alternate_id=upc).exists())
        self.assertEqual(Company.get().upc_next_item_ref, 975)
        with self.assertRaises(ValidationError):
            services.generate_upc(p)

    def test_images_and_excel_export_embeds_image_in_cell(self):
        p = self.make(carton_qty=120, carton_cbm=D("0.1229"))
        img = services.add_image(p, SimpleUploadedFile("a.png", png_bytes(), content_type="image/png"))
        self.assertTrue(img.is_primary)
        self.assertEqual((img.width, img.height), (800, 400))  # never upscaled
        second = services.add_image(p, SimpleUploadedFile("b.png", png_bytes("blue")), make_primary=True)
        img.refresh_from_db()
        self.assertTrue(second.is_primary)
        self.assertFalse(img.is_primary)
        with self.assertRaises(ValidationError):
            services.add_image(p, SimpleUploadedFile("x.png", b"not an image"))
        data = services.export_products_xlsx([p])
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertTrue(any(n.startswith("xl/media/") for n in names))
        self.assertIn("xl/richData/rdrichvalue.xml", names)  # Excel "Place in Cell" picture
        # Excel rejects zip part names starting with '/' ("We found a problem with some content")
        self.assertFalse([n for n in names if n.startswith("/")])
        self.assertIn("xl/richData/_rels/richValueRel.xml.rels", names)
        from openpyxl import load_workbook

        ws = load_workbook(io.BytesIO(data)).active
        self.assertEqual(ws["B2"].value, p.sku)

    def test_floating_image_export_for_older_excel(self):
        import zipfile

        p = self.make()
        services.add_image(p, SimpleUploadedFile("a.png", png_bytes(), content_type="image/png"))
        names = zipfile.ZipFile(io.BytesIO(services.export_products_xlsx([p], image_mode="float"))).namelist()
        self.assertIn("xl/drawings/drawing1.xml", names)
        self.assertFalse([n for n in names if n.startswith("/") or "richData" in n])

    def test_sanitize_repairs_leading_slash_parts(self):
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("/xl/bad.xml", "x")
            z.writestr("xl/good.xml", "y")
        fixed = zipfile.ZipFile(io.BytesIO(services._sanitize_xlsx(buf.getvalue())))
        self.assertEqual(sorted(fixed.namelist()), ["xl/bad.xml", "xl/good.xml"])

    def test_hts_export_import_round_trip(self):
        data = services.export_hts_xlsx(list(HTSCode.objects.all()))
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data))
        ws = wb.active
        ws.append(["HOME -BATH -BATHMATS", "COTTON", "NA", "5702.99.1500", 6, 0, "new"])
        ws.append(["NOPE -NOPE -NOPE", "X", "", "1", 1, 1, ""])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        created, updated, errors = services.import_hts_xlsx(buf)
        self.assertEqual(created, 1)
        self.assertEqual(updated, HTSCode.objects.count() - 1)
        self.assertEqual(len(errors), 1)
        self.assertTrue(LookupValue.objects.filter(kind="material", code="COTTON").exists())


class ProductScreensTests(TestCase):
    def setUp(self):
        self.f = Fixture()
        load_sample_setup(enrich_demo_products=False)
        self.client.login(username="admin", password="pw-admin-123")

    def test_create_item_via_form_with_warehouse_and_xref(self):
        lv = lambda kind, code: LookupValue.objects.get(kind=kind, code=code).pk  # noqa: E731
        wh2 = Warehouse.objects.create(code="WH2", name="Second")
        mgmt = {f"{p}-{k}": v for p in ("wh", "xref", "vp") for k, v in
                (("TOTAL_FORMS", "0"), ("INITIAL_FORMS", "0"), ("MIN_NUM_FORMS", "0"), ("MAX_NUM_FORMS", "1000"))}
        mgmt.update({"wh-TOTAL_FORMS": "1", "wh-0-warehouse": wh2.pk, "wh-0-bin_location": "A-01-03",
                     "wh-0-reorder_point": "100", "wh-0-reorder_qty": "500", "wh-0-max_qty": "0",
                     "xref-TOTAL_FORMS": "1", "xref-0-alt_type": "barcode", "xref-0-alternate_id": "67619100973"})
        data = {
            "sku": "NEW-1", "name": "Test robe", "item_class": ItemClass.objects.get(code="BABYROBES").pk,
            "brand": lv("brand", "KENSIEGIRL"), "package_size": lv("package_size", "1"), "season": lv("season", "Q426"),
            "gender": lv("gender", "Girl"), "size_range": lv("size_range", "INFANT"),
            "material": lv("material", "LUX&SHERPA"), "customer_code": lv("customer_code", "STOCK"),
            "is_active": "on", "duty_rate": "0", "tariff_rate": "0", "prepack_qty": "1", "carton_qty": "24",
            "dimension_unit": "in", "carton_length": "20", "carton_width": "15", "carton_height": "12",
            "carton_cbm": "0", "carton_weight_kg": "0", "weight_kg": "0", "volume_cbm": "0", "list_price": "12",
            "standard_cost": "0", "monthly_carrying_cost": "0", "reorder_point": "0", "reorder_qty": "0",
            "lead_time_days": "0", **mgmt,
        }
        resp = self.client.post("/products/items/new/", data)
        p = Product.objects.get(sku="NEW-1")
        self.assertRedirects(resp, p.get_absolute_url())
        self.assertEqual(p.hs_code, "6302.93.1000")  # from HTS table
        self.assertEqual(p.carton_cbm, D("0.0590"))
        self.assertEqual(ProductWarehouse.objects.get(product=p).bin_location, "A-01-03")
        self.assertEqual(p.cross_references.get().alternate_id, "676191009735")  # check digit added
        # required item-detail fields are enforced
        data2 = dict(data, sku="NEW-2", brand="")
        resp = self.client.post("/products/items/new/", data2)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Product.objects.filter(sku="NEW-2").exists())

    def test_setup_pages_and_export(self):
        for url in ["/products/setup/", "/products/setup/brand/", "/products/setup/material/new/",
                    "/products/item-classes/", "/products/item-classes/new/", "/products/hts-codes/",
                    "/products/hts-codes/?tab=waiting", "/products/hts-codes/new/", "/products/items/",
                    "/products/items/new/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        resp = self.client.get("/products/items/export.xlsx")
        self.assertEqual(resp["Content-Type"].split(";")[0],
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp = self.client.get("/products/hts-codes/export.xlsx")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.get("/products/setup/not-a-kind/").status_code, 404)

    def test_per_warehouse_reorder(self):
        p = Product.objects.create(sku="R1", name="Robe")
        ProductWarehouse.objects.create(product=p, warehouse=self.f.wh, reorder_point=D(50), reorder_qty=D(200))
        resp = self.client.get("/inventory/reorder/")
        self.assertContains(resp, "R1")
        self.assertContains(resp, "WH1")
