from .mixins import is_vendor_user
from .models import Company

NAV = [
    ("Overview", [("Dashboard", "dashboard", "dashboard")]),
    ("Sales", [
        ("Sales Orders & Quotes", "sales:salesorder_list", "sales/orders"),
        ("Deliveries", "sales:delivery_list", "sales/deliveries"),
        ("Customers", "sales:customer_list", "sales/customers"),
    ]),
    ("Purchasing", [
        ("Purchase Orders", "purchasing:purchaseorder_list", "purchasing/orders"),
        ("Goods Receipts", "purchasing:goodsreceipt_list", "purchasing/receipts"),
        ("Vendors", "purchasing:vendor_list", "purchasing/vendors"),
    ]),
    ("Import Management", [
        ("Shipments", "purchasing:shipment_list", "purchasing/shipments"),
        ("Landed Costs", "purchasing:landedcost_list", "purchasing/landed-costs"),
    ]),
    ("Accounting", [
        ("Customer Invoices (AR)", "accounting:customerinvoice_list", "accounting/invoices"),
        ("Customer Payments", "accounting:customerpayment_list", "accounting/customer-payments"),
        ("Vendor Bills (AP)", "accounting:vendorbill_list", "accounting/bills"),
        ("Vendor Payments", "accounting:vendorpayment_list", "accounting/vendor-payments"),
        ("Journal Entries", "accounting:journalentry_list", "accounting/journal"),
        ("Chart of Accounts", "accounting:account_list", "accounting/accounts"),
        ("Financial Reports", "accounting:reports", "accounting/reports"),
    ]),
    ("Products", [
        ("Stock Items", "products:product_list", "products/items"),
        ("Item Classes", "products:itemclass_list", "products/item-classes"),
        ("HTS Codes", "products:htscode_list", "products/hts-codes"),
        ("Product Setup", "products:setup", "products/setup"),
        ("Price Lists", "products:pricelist_list", "products/price-lists"),
    ]),
    ("Inventory", [
        ("Availability (Qty Buckets)", "inventory:availability", "inventory/availability"),
        ("Sales Availability", "inventory:sales_availability", "inventory/sales-availability"),
        ("Stock on Hand", "inventory:stock", "inventory/stock"),
        ("Reorder Report", "inventory:reorder", "inventory/reorder"),
        ("Adjustments", "inventory:stockadjustment_list", "inventory/adjustments"),
        ("Transfers", "inventory:stocktransfer_list", "inventory/transfers"),
        ("Stock Moves", "inventory:moves", "inventory/moves"),
        ("Warehouses", "inventory:warehouse_list", "inventory/warehouses"),
    ]),
    ("Sales Team", [
        ("Sales People", "salesteam:salesrep_list", "sales-team/reps"),
        ("Performance", "salesteam:performance", "sales-team/performance"),
        ("Commissions", "salesteam:commission_list", "sales-team/commissions"),
    ]),
    ("Settings", [
        ("Company", "core:company", "settings/company"),
        ("Currencies & FX Rates", "core:exchangerate_list", "settings/rates"),
        ("Vendor Portal Users", "portal:contact_list", "portal-users"),
        ("Email", "core:email_settings", "settings/email"),
    ]),
]


def erp(request):
    user = getattr(request, "user", None)
    return {
        "company": Company.get(),
        "nav": NAV,
        "is_vendor_user": bool(user and is_vendor_user(user)),
    }
