"""Load a realistic demo dataset by running the real business flows end to end."""
import datetime
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounting import services as acc
from accounting.models import Account, CustomerPayment, CustomerPaymentAllocation, VendorBill, VendorBillLine, \
    VendorPayment, VendorPaymentAllocation
from core.models import Company, ExchangeRate
from inventory.models import StockAdjustment, StockAdjustmentLine, Warehouse
from inventory.services import post_adjustment
from portal.models import VendorContact
from products.models import Category, PriceList, PriceListItem, Product, UnitOfMeasure, VendorProduct
from purchasing import services as pur
from purchasing.models import LandedCost, PurchaseOrder, PurchaseOrderLine, Shipment, ShipmentLine, Vendor
from sales import services as sal
from sales.models import Customer, SalesOrder, SalesOrderLine
from salesteam.models import SalesRep


class Command(BaseCommand):
    help = "Create demo data (users admin/admin12345, sales rep maria/demo12345, vendor portal sunrise/demo12345)."

    def add_arguments(self, parser):
        parser.add_argument("--admin", default="admin")
        parser.add_argument("--password", default="admin12345")
        parser.add_argument("--if-empty", action="store_true", help="Skip quietly if data already exists")

    def handle(self, *args, **opts):
        if Product.objects.exists():
            if opts["if_empty"]:
                self.stdout.write("Data already exists; demo seed skipped.")
                return
            raise CommandError("Data already exists; run on an empty database.")
        call_command("setup_erp", admin=opts["admin"], password=opts["password"], stdout=self.stdout)
        with transaction.atomic():
            self.seed(opts["admin"])
        self.stdout.write(self.style.SUCCESS(
            f"Demo data loaded.\n  Staff:        {opts['admin']}\n  Sales rep:    maria / demo12345\n"
            "  Vendor portal: sunrise / demo12345"))

    def seed(self, admin_username="admin"):
        User = get_user_model()
        admin = User.objects.get(username=admin_username)
        today = datetime.date.today()
        ago = lambda n: today - datetime.timedelta(days=n)  # noqa: E731

        company = Company.get()
        company.name = "Pacific Coast Imports Inc."
        company.address = "1200 Harbor Blvd\nLong Beach, CA 90802\nUSA"
        company.default_sales_tax_rate = D("0")
        company.save()
        ExchangeRate.objects.create(currency_id="CNY", date=ago(120), rate=D("0.1385"))
        ExchangeRate.objects.create(currency_id="CNY", date=ago(30), rate=D("0.1392"))
        ExchangeRate.objects.create(currency_id="EUR", date=ago(120), rate=D("1.0850"))
        ExchangeRate.objects.create(currency_id="EUR", date=ago(30), rate=D("1.0920"))

        main = Warehouse.objects.create(code="LB1", name="Long Beach DC", address="1200 Harbor Blvd",
                                        city="Long Beach", state="CA", country="USA")
        company.default_warehouse = main
        company.save()
        Warehouse.objects.create(code="DAL", name="Dallas Satellite", city="Dallas", state="TX", country="USA")
        ea = UnitOfMeasure.objects.create(code="EA", name="Each")
        UnitOfMeasure.objects.create(code="CTN", name="Carton")
        home = Category.objects.create(name="Home & Kitchen")
        cook = Category.objects.create(name="Cookware", parent=home)
        light = Category.objects.create(name="Lighting")
        tools = Category.objects.create(name="Hand Tools")

        sunrise = Vendor.objects.create(code="V-SUN", name="Sunrise Manufacturing Co., Ltd.", country="China",
                                        city="Ningbo", currency_id="CNY", payment_terms_days=30,
                                        default_incoterm="FOB", contact_name="Lily Zhang",
                                        email="lily@sunrise.example")
        euro = Vendor.objects.create(code="V-EUR", name="Brightline Lighting GmbH", country="Germany",
                                     city="Hamburg", currency_id="EUR", payment_terms_days=45,
                                     default_incoterm="FCA", contact_name="Jonas Weber")
        fwd = Vendor.objects.create(code="V-FWD", name="OceanLink Logistics", vendor_type=Vendor.FORWARDER,
                                    country="USA", currency_id="USD", payment_terms_days=15)
        broker = Vendor.objects.create(code="V-CB", name="Harbor Customs Brokers", vendor_type=Vendor.BROKER,
                                       country="USA", currency_id="USD", payment_terms_days=10)

        P = []
        for sku, name, cat, hs, duty, w, v, price, cost, vend, rp, rq in [
            ("CK-1001", "Stainless Steel Frying Pan 28cm", cook, "7323.93.0035", "2.0", "1.10", "0.0080", "24.00",
             "60.00", sunrise, 100, 400),
            ("CK-1002", "Non-stick Saucepan Set (3pc)", cook, "7615.10.7130", "3.1", "2.40", "0.0210", "49.00",
             "128.00", sunrise, 60, 200),
            ("CK-1003", "Silicone Utensil Set (6pc)", home, "3924.10.4000", "3.4", "0.55", "0.0030", "14.50",
             "28.00", sunrise, 150, 500),
            ("LT-2001", "LED Desk Lamp, dimmable", light, "9405.21.6010", "3.9", "1.30", "0.0120", "39.00",
             "16.50", euro, 50, 150),
            ("LT-2002", "Smart LED Bulb E26 (4-pack)", light, "8539.52.0090", "2.0", "0.40", "0.0020", "22.00",
             "7.90", euro, 120, 400),
            ("TL-3001", "Ratchet Screwdriver Set 24pc", tools, "8205.40.0000", "6.2", "0.90", "0.0040", "19.90",
             "42.00", sunrise, 80, 300),
        ]:
            p = Product.objects.create(sku=sku, name=name, category=cat, uom=ea, hs_code=hs, duty_rate=D(duty),
                                       weight_kg=D(w), volume_cbm=D(v), list_price=D(price), standard_cost=D(cost),
                                       preferred_vendor=vend, reorder_point=D(rp), reorder_qty=D(rq),
                                       lead_time_days=60 if vend == sunrise else 30, country_of_origin=vend.country,
                                       units_per_carton=12)
            VendorProduct.objects.create(vendor=vend, product=p, vendor_sku=f"{vend.code[2:]}-{sku}",
                                         unit_cost=D(cost), currency=vend.currency, moq=D(rq),
                                         lead_time_days=p.lead_time_days)
            P.append(p)

        tier = PriceList.objects.create(name="Distributor", description="Volume distributors")
        for p in P:
            PriceListItem.objects.create(price_list=tier, product=p, min_qty=1, price=(p.list_price * D("0.9"))
                                         .quantize(D("0.01")))
            PriceListItem.objects.create(price_list=tier, product=p, min_qty=100, price=(p.list_price * D("0.82"))
                                         .quantize(D("0.01")))

        maria_user = User.objects.create_user("maria", password="demo12345", first_name="Maria", last_name="Lopez")
        maria = SalesRep.objects.create(user=maria_user, code="SR-01", name="Maria Lopez", territory="West Coast",
                                        commission_rate=D("5"), monthly_target=D("25000"), hire_date=ago(700))
        james = SalesRep.objects.create(code="SR-02", name="James Carter", territory="Texas & South",
                                        commission_rate=D("12"), commission_basis=SalesRep.MARGIN,
                                        commission_trigger=SalesRep.ON_PAYMENT, monthly_target=D("20000"),
                                        manager=maria)
        cust = [
            Customer.objects.create(code="C-HOME", name="HomeStyle Retail Group", city="San Diego", state="CA",
                                    country="USA", credit_limit=D("50000"), payment_terms_days=30, sales_rep=maria,
                                    email="ap@homestyle.example"),
            Customer.objects.create(code="C-KDS", name="Kitchen Depot Supply", city="Phoenix", state="AZ",
                                    country="USA", credit_limit=D("30000"), payment_terms_days=45,
                                    price_list=tier, sales_rep=maria),
            Customer.objects.create(code="C-LSD", name="Lone Star Distributors", city="Houston", state="TX",
                                    country="USA", credit_limit=D("8000"), payment_terms_days=30,
                                    price_list=tier, sales_rep=james),
            Customer.objects.create(code="C-HW", name="Hardware Hub Inc.", city="Dallas", state="TX", country="USA",
                                    payment_terms_days=15, sales_rep=james, tax_exempt=True),
        ]

        # Opening stock and owner's capital
        adj = StockAdjustment.objects.create(warehouse=main, date=ago(100), reason="opening",
                                             notes="Opening balances")
        for p, q in zip(P, [250, 80, 300, 90, 200, 120]):
            StockAdjustmentLine.objects.create(adjustment=adj, product=p, quantity=D(q),
                                               unit_cost=(p.standard_cost * D("0.14") if p.preferred_vendor == sunrise
                                                          else p.standard_cost * D("1.09")).quantize(D("0.0001")))
        post_adjustment(adj, admin)
        acc.post_journal(ago(100), "Owner capital contribution",
                         [{"account": acc.acct("CASH"), "debit": D("250000")},
                          {"account": acc.acct("EQUITY"), "credit": D("250000")}], "manual", "CAPITAL", admin)

        # ---- Import cycle 1: China PO, ocean container, landed costs, received, billed and paid
        po1 = PurchaseOrder.objects.create(vendor=sunrise, order_date=ago(80), expected_date=ago(55),
                                           currency_id="CNY", exchange_rate=D("0.1385"), incoterm="FOB",
                                           warehouse=main, payment_terms="30% deposit, 70% against B/L copy",
                                           created_by=admin)
        for p, q in [(P[0], 600), (P[1], 240), (P[2], 800), (P[5], 400)]:
            PurchaseOrderLine.objects.create(order=po1, product=p, quantity=D(q), unit_price=p.standard_cost)
        pur.send_po(po1)
        pur.confirm_po(po1, ship_date=ago(58), vendor_reference="PI-SUN-2291", by="lily")
        s1 = pur.create_shipment_for_po(po1, forwarder=fwd, mode="sea_fcl", carrier="COSCO", vessel_voyage="CSCL "
                                        "Saturn 041E", origin_port="Ningbo", destination_port="Long Beach",
                                        container_numbers="CSNU7203311 40HQ", bill_of_lading="COSU6331208840",
                                        etd=ago(56), eta=ago(36), allocation_method=Shipment.ALLOC_VOLUME)
        s1.actual_arrival, s1.customs_entry_number, s1.customs_cleared_on = ago(35), "ENT-2291-883", ago(33)
        s1.status = Shipment.CLEARED
        s1.save()
        LandedCost.objects.create(shipment=s1, cost_type="freight", description="Ocean freight 40HQ", vendor=fwd,
                                  amount=D("3850.00"))
        LandedCost.objects.create(shipment=s1, cost_type="insurance", description="Cargo insurance", vendor=fwd,
                                  amount=D("210.00"))
        LandedCost.objects.create(shipment=s1, cost_type="broker", description="Entry & ISF filing", vendor=broker,
                                  amount=D("325.00"))
        LandedCost.objects.create(shipment=s1, cost_type="inland", description="Drayage to DC", vendor=fwd,
                                  amount=D("640.00"))
        pur.estimate_duty(s1)
        pur.receive_shipment(s1, ago(32), admin)
        bill1 = pur.create_bill_from_po(po1, "SUN-INV-2291", ago(57))
        bill1.exchange_rate = D("0.1390")
        bill1.save()
        acc.post_vendor_bill(bill1, admin)
        fwd_bill = VendorBill.objects.create(vendor=fwd, vendor_invoice_number="OL-55810", bill_date=ago(34),
                                             currency_id="USD", shipment=s1)
        for lc in s1.landed_costs.filter(vendor=fwd):
            VendorBillLine.objects.create(bill=fwd_bill, description=lc.get_cost_type_display(),
                                          account=acc.acct("ACCRUED_LANDED"), unit_price=lc.amount)
        acc.post_vendor_bill(fwd_bill, admin)
        duty_total = sum((lc.amount for lc in s1.landed_costs.filter(cost_type="duty")), D(0))
        cb_bill = VendorBill.objects.create(vendor=broker, vendor_invoice_number="HCB-1182", bill_date=ago(33),
                                            currency_id="USD", shipment=s1)
        VendorBillLine.objects.create(bill=cb_bill, description="Brokerage", account=acc.acct("ACCRUED_LANDED"),
                                      unit_price=D("325.00"))
        VendorBillLine.objects.create(bill=cb_bill, description="Duties advanced (CBP)",
                                      account=acc.acct("ACCRUED_LANDED"), unit_price=duty_total)
        acc.post_vendor_bill(cb_bill, admin)
        pay1 = VendorPayment.objects.create(vendor=sunrise, date=ago(30), currency_id="CNY",
                                            exchange_rate=D("0.1392"), amount=bill1.total, method="wire",
                                            reference="TT 88231", paid_from=acc.acct("CASH"))
        VendorPaymentAllocation.objects.create(payment=pay1, bill=bill1, amount=bill1.total)
        acc.post_vendor_payment(pay1, admin)
        pay2 = VendorPayment.objects.create(vendor=fwd, date=ago(20), currency_id="USD", amount=fwd_bill.total,
                                            method="ach", paid_from=acc.acct("CASH"))
        VendorPaymentAllocation.objects.create(payment=pay2, bill=fwd_bill, amount=fwd_bill.total)
        acc.post_vendor_payment(pay2, admin)

        # ---- Import cycle 2: EU PO in transit (air), portal-confirmed
        po2 = PurchaseOrder.objects.create(vendor=euro, order_date=ago(25), expected_date=ago(8), currency_id="EUR",
                                           exchange_rate=D("1.0920"), incoterm="FCA", warehouse=main,
                                           created_by=admin)
        PurchaseOrderLine.objects.create(order=po2, product=P[3], quantity=D(150), unit_price=D("16.50"))
        PurchaseOrderLine.objects.create(order=po2, product=P[4], quantity=D(400), unit_price=D("7.90"))
        pur.send_po(po2)
        pur.confirm_po(po2, ship_date=ago(9), vendor_reference="AB-77120", by="jonas")
        s2 = pur.create_shipment_for_po(po2, forwarder=fwd, mode="air", carrier="Lufthansa Cargo",
                                        vessel_voyage="LH8160", origin_port="HAM", destination_port="LAX",
                                        bill_of_lading="020-55123344", etd=ago(4), eta=today + datetime.timedelta(3))
        s2.status = Shipment.IN_TRANSIT
        s2.save()
        LandedCost.objects.create(shipment=s2, cost_type="freight", description="Air freight", vendor=fwd,
                                  amount=D("1480.00"))
        pur.estimate_duty(s2)

        # ---- PO 3 sent, awaiting vendor confirmation in portal
        po3 = PurchaseOrder.objects.create(vendor=sunrise, order_date=ago(2), expected_date=today +
                                           datetime.timedelta(40), currency_id="CNY", exchange_rate=D("0.1392"),
                                           incoterm="FOB", warehouse=main, created_by=admin)
        PurchaseOrderLine.objects.create(order=po3, product=P[2], quantity=D(1000), unit_price=D("27.50"))
        PurchaseOrderLine.objects.create(order=po3, product=P[0], quantity=D(500), unit_price=D("59.00"))
        pur.send_po(po3)

        # ---- Sales
        def order(customer, days, lines, rep=None, po=""):
            so = SalesOrder.objects.create(customer=customer, order_date=ago(days), warehouse=main,
                                           sales_rep=rep or customer.sales_rep, customer_po=po, created_by=admin)
            sal.copy_order_defaults(so)
            so.save()
            for p, q in lines:
                SalesOrderLine.objects.create(order=so, product=p, quantity=D(q))
            return so

        def full_cycle(so, ship_days, inv_days, pay_days=None, pay_amount=None):
            sal.confirm_order(so)
            sal.create_delivery(so, {ln.pk: ln.quantity for ln in so.lines.all()}, ago(ship_days), "UPS Freight",
                                f"1Z{so.pk:08d}", admin)
            inv = sal.create_invoice_from_order(so, ago(inv_days))
            acc.post_customer_invoice(inv, admin)
            if pay_days is not None:
                inv.refresh_from_db()
                amount = pay_amount or inv.total
                pay = CustomerPayment.objects.create(customer=so.customer, date=ago(pay_days), amount=amount,
                                                     method="ach", deposit_account=acc.acct("CASH"))
                CustomerPaymentAllocation.objects.create(payment=pay, invoice=inv, amount=amount)
                acc.post_customer_payment(pay, admin)
            return inv

        full_cycle(order(cust[0], 28, [(P[0], 120), (P[2], 200)], po="HS-4410"), 27, 27, 5)
        full_cycle(order(cust[1], 22, [(P[1], 60), (P[0], 150)], po="KD-9921"), 21, 21, 3, D("4000"))
        full_cycle(order(cust[2], 50, [(P[5], 90), (P[3], 20)]), 49, 49)  # overdue
        full_cycle(order(cust[3], 15, [(P[5], 60), (P[2], 100)]), 14, 14, 2)
        full_cycle(order(cust[0], 6, [(P[4], 80), (P[3], 30)], po="HS-4502"), 5, 5)
        partial = order(cust[1], 4, [(P[0], 300), (P[1], 40)], po="KD-9960")
        sal.confirm_order(partial)
        sal.create_delivery(partial, {partial.lines.first().pk: D(200)}, ago(3), "FedEx Freight", "7712", admin)
        sal.confirm_order(order(cust[3], 1, [(P[2], 50), (P[0], 40)]))
        order(cust[2], 0, [(P[0], 200), (P[1], 60)])  # quote over credit limit

        # ---- Vendor portal login
        u = User.objects.create_user("sunrise", password="demo12345", first_name="Lily", last_name="Zhang",
                                     email="lily@sunrise.example")
        VendorContact.objects.create(user=u, vendor=sunrise, title="Export Sales Manager")

        # Rent & payroll so the P&L looks real
        for n, (acct_code, amt, memo) in enumerate([("6000", "8500", "Warehouse rent"),
                                                    ("6200", "21400", "Payroll"),
                                                    ("6400", "185", "Bank fees")]):
            acc.post_journal(ago(10 + n), memo, [{"account": Account.objects.get(code=acct_code), "debit": D(amt)},
                                                 {"account": acc.acct("CASH"), "credit": D(amt)}],
                             "manual", "", admin)
