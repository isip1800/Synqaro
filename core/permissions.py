"""Permissions, built-in roles and the URL -> permission map enforced by core.middleware."""

PERMISSIONS = [
    # code, group, label
    ("sales.view", "Sales", "View customers, sales orders, deliveries"),
    ("sales.edit", "Sales", "Create / edit customers and sales orders, release to warehouse"),
    ("sales.approve", "Sales", "Approve orders (credit holds)"),
    ("sales.ship", "Sales", "Ship / deliver orders"),
    ("returns.edit", "Sales", "Customer returns, credit memos and chargebacks"),
    ("purchasing.view", "Purchasing", "View vendors, purchase orders, shipments"),
    ("purchasing.edit", "Purchasing", "Create / edit vendors, POs, shipments, landed costs"),
    ("inventory.view", "Inventory", "View stock, availability, moves"),
    ("inventory.edit", "Inventory", "Adjust and transfer stock"),
    ("inventory.receive", "Inventory", "Receive goods (POs and shipments)"),
    ("products.view", "Products", "View items and product setup"),
    ("products.edit", "Products", "Create / edit items, item classes, HTS codes, price lists"),
    ("costs.view", "Costs", "See costs, landed costs, margins, PO prices and inventory value"),
    ("accounting.view", "Accounting", "View invoices, bills, payments, journals and financial reports"),
    ("accounting.post", "Accounting", "Create and post invoices, bills, payments and journal entries"),
    ("accounting.close", "Accounting", "Bank reconciliation, close / reopen periods"),
    ("salesteam.view", "Sales team", "View sales people, performance, commissions"),
    ("salesteam.manage", "Sales team", "Approve and pay commissions, edit sales people"),
    ("settings.manage", "Administration", "Company settings, currencies, email, imports"),
    ("users.manage", "Administration", "Users, roles and vendor portal users"),
]
ALL = [p[0] for p in PERMISSIONS]
VIEW_ALL = [p for p in ALL if p.endswith(".view")]

ROLE_TEMPLATES = {
    "Owner": ("Full access, including users and settings", ALL),
    "Administrator": ("Everything except managing users", [p for p in ALL if p != "users.manage"]),
    "Accounting": ("Finance: AR, AP, journals, bank, period close; read-only operations", VIEW_ALL + [
        "accounting.post", "accounting.close", "returns.edit", "costs.view", "salesteam.manage"]),
    "Sales": ("Customers and orders; no costs or accounting", [
        "sales.view", "sales.edit", "sales.ship", "returns.edit", "products.view", "inventory.view",
        "salesteam.view"]),
    "Sales Manager": ("Sales plus approvals, margins and commissions", [
        "sales.view", "sales.edit", "sales.approve", "sales.ship", "returns.edit", "products.view",
        "inventory.view", "costs.view", "salesteam.view", "salesteam.manage", "accounting.view"]),
    "Purchasing": ("Vendors, POs, imports, landed costs", [
        "purchasing.view", "purchasing.edit", "inventory.view", "inventory.receive", "products.view",
        "products.edit", "costs.view", "sales.view"]),
    "Warehouse": ("Receive, ship, adjust and transfer stock; no prices or costs", [
        "inventory.view", "inventory.edit", "inventory.receive", "sales.view", "sales.ship", "purchasing.view",
        "products.view"]),
    "Read Only": ("View everything except costs", [p for p in VIEW_ALL if p != "costs.view"]),
}


def ensure_roles(company):
    """Create the built-in roles for a company (idempotent). Returns {name: Role}."""
    from .models import Role

    roles = {}
    for name, (desc, perms) in ROLE_TEMPLATES.items():
        role, _ = Role.objects.get_or_create(company=company, name=name,
                                             defaults={"description": desc, "permissions": perms, "is_system": True})
        roles[name] = role
    return roles


# ----------------------------------------------------------------------------- URL -> permission

MODULES = {"sales": "sales", "purchasing": "purchasing", "inventory": "inventory", "products": "products",
           "accounting": "accounting", "salesteam": "salesteam"}

OVERRIDES = {
    # (namespace, url_name): permission  (applies to GET and POST unless a "|POST" key exists)
    ("sales", "order_approve"): "sales.approve",
    ("sales", "order_reject"): "sales.approve",
    ("sales", "order_ship"): "sales.ship",
    ("sales", "order_invoice"): "accounting.post",
    ("purchasing", "po_receive"): "inventory.receive",
    ("purchasing", "shipment_receive"): "inventory.receive",
    ("purchasing", "landed_cost_capitalize"): "accounting.post",
    ("accounting", "period_close_action"): "accounting.close",
    ("accounting", "period_reopen"): "accounting.close",
    ("accounting", "bankrec_undo"): "accounting.close",
    ("accounting", "bankrec_start"): "accounting.close",
    ("accounting", "bankrec_save"): "accounting.close",
    ("accounting", "bankrec_import"): "accounting.close",
    ("accounting", "bankrec_item"): "accounting.close",
    ("accounting", "bankrec_delete"): "accounting.close",
    ("purchasing", "po_create_bill"): "accounting.post",
    ("purchasing", "goodsreceipt_list"): "inventory.view",
    ("purchasing", "goodsreceipt_detail"): "inventory.view",
    ("purchasing", "landedcost_list"): "costs.view",
    ("products", "vendorproduct_list"): "costs.view",
    ("products", "vendorproduct_create"): "costs.view",
    ("products", "vendorproduct_update"): "costs.view",
    ("salesteam", "pay_commissions"): "salesteam.manage",
    ("salesteam", "approve_commissions"): "salesteam.manage",
    ("core", "company"): "settings.manage",
    ("core", "email_settings"): "settings.manage",
    ("core", "email_log"): "settings.manage",
    ("portal", "contact_list"): "users.manage",
    ("portal", "contact_create"): "users.manage",
    ("portal", "contact_toggle"): "users.manage",
}
# core views any member may use (their data is protected by the document's own screens)
OPEN_CORE = {"columns_save", "filter_save", "filter_delete", "file_upload", "file_download", "file_delete",
             "note_add", "note_delete", "print_document", "email_compose", "switch_company", "no_access"}
CORE_SETTINGS_PREFIXES = ("currency_", "exchangerate_", "emailtemplate_", "import", "role", "user", "membership",
                          "company")


def required_permission(namespace, url_name, method):
    """Permission code needed for a request, '' for any member, None when not governed here."""
    if (namespace, url_name) in OVERRIDES:
        perm = OVERRIDES[(namespace, url_name)]
        if namespace == "salesteam" and method != "POST" and perm == "salesteam.manage":
            return "salesteam.view"
        return perm
    if namespace in MODULES:
        mod = MODULES[namespace]
        if method in ("GET", "HEAD", "OPTIONS"):
            return f"{mod}.view"
        if mod == "accounting":
            return "accounting.post"
        if mod == "salesteam":
            return "salesteam.manage"
        return f"{mod}.edit"
    if namespace == "returns":
        return _returns_permission(url_name, method)
    if namespace == "core":
        if url_name in OPEN_CORE:
            return ""
        if url_name.startswith(("user", "role", "membership")):
            return "users.manage"
        if url_name == "company_list":
            return ""  # listing your companies / creating one is checked in the view
        return "settings.manage"
    return None


RETURNS_RULES = (
    # url_name prefix: (view permission, change permission)
    ("customerreturn", "sales.view", "returns.edit"),
    ("chargeback", "sales.view", "returns.edit"),
    ("creditmemo", "accounting.view", "accounting.post"),
    ("vendorcredit", "accounting.view", "accounting.post"),
    ("vendorreturn", "purchasing.view", "purchasing.edit"),
)


def _returns_permission(url_name, method):
    for prefix, view, change in RETURNS_RULES:
        if url_name.startswith(prefix):
            if method in ("GET", "HEAD", "OPTIONS") and not url_name.endswith(("_create", "_update")):
                return view
            return change
    return "settings.manage"
