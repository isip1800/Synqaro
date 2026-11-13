from .mixins import is_vendor_user
from .models import Company

NAV = [
    ("Overview", [("Dashboard", "dashboard", "dashboard")]),
    ("Sales", [
        ("Sales Orders & Quotes", "sales:salesorder_list", "sales/orders"),
        ("Deliveries", "sales:delivery_list", "sales/deliveries"),
        ("Customers", "sales:customer_list", "sales/customers"),
        ("Customer Returns (RMA)", "returns:customerreturn_list", "returns/customer-returns"),
        ("Chargebacks", "returns:chargeback_list", "returns/chargebacks"),
    ]),
    ("Purchasing", [
        ("Purchase Orders", "purchasing:purchaseorder_list", "purchasing/orders"),
        ("Goods Receipts", "purchasing:goodsreceipt_list", "purchasing/receipts"),
        ("Vendors", "purchasing:vendor_list", "purchasing/vendors"),
        ("Returns to Vendor", "returns:vendorreturn_list", "returns/vendor-returns"),
    ]),
    ("Import Management", [
        ("Shipments", "purchasing:shipment_list", "purchasing/shipments"),
        ("Landed Costs", "purchasing:landedcost_list", "purchasing/landed-costs"),
    ]),
    ("Accounting", [
        ("Customer Invoices (AR)", "accounting:customerinvoice_list", "accounting/invoices"),
        ("Customer Payments", "accounting:customerpayment_list", "accounting/customer-payments"),
        ("Credit Memos", "returns:creditmemo_list", "returns/credit-memos"),
        ("Vendor Bills (AP)", "accounting:vendorbill_list", "accounting/bills"),
        ("Vendor Payments", "accounting:vendorpayment_list", "accounting/vendor-payments"),
        ("Vendor Credits", "returns:vendorcredit_list", "returns/vendor-credits"),
        ("Journal Entries", "accounting:journalentry_list", "accounting/journal"),
        ("Bank Reconciliation", "accounting:bankreconciliation_list", "accounting/bank-reconciliation"),
        ("Month-End Close", "accounting:period_close", "accounting/month-end-close"),
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
        ("Users & Roles", "core:user_list", "settings/users"),
        ("Companies", "core:company_list", "settings/companies"),
        ("Currencies & FX Rates", "core:exchangerate_list", "settings/rates"),
        ("Vendor Portal Users", "portal:contact_list", "portal-users"),
        ("Email", "core:email_settings", "settings/email"),
        ("Change log", "core:audit_log", "settings/change-log"),
    ]),
]


def _nav_for(request):
    from django.urls import NoReverseMatch, resolve, reverse

    from .permissions import required_permission

    perms = getattr(request, "perms", frozenset())
    out = []
    for section, items in NAV:
        allowed = []
        for label, url_name, key in items:
            try:
                match = resolve(reverse(url_name))
            except NoReverseMatch:
                continue
            need = required_permission(match.namespace, match.url_name or "", "GET")
            if not need or need in perms:
                allowed.append((label, url_name, key))
        if allowed:
            out.append((section, allowed))
    return out


def erp(request):
    from .models import Membership

    user = getattr(request, "user", None)
    perms = getattr(request, "perms", frozenset())
    companies = []
    if user is not None and user.is_authenticated and not is_vendor_user(user):
        if user.is_superuser:
            companies = list(Company.objects.filter(is_active=True).order_by("name"))
        else:
            companies = [m.company for m in Membership.objects.filter(user=user, is_active=True,
                                                                      company__is_active=True)
                         .select_related("company")]
    return {
        "company": getattr(request, "company", None) or Company.get(),
        "nav": _nav_for(request) if user is not None and user.is_authenticated else NAV,
        "is_vendor_user": bool(user and is_vendor_user(user)),
        "perms": perms,
        "can_see_costs": "costs.view" in perms,
        "my_companies": companies,
        "membership": getattr(request, "membership", None),
    }
