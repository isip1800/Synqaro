"""Change history: records who changed which fields of tracked records, and when."""
import contextvars
import datetime
import decimal

from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete, post_save, pre_save

_user = contextvars.ContextVar("synqaro_audit_user", default=None)

# model label -> parent attribute (changes are also shown on the parent document), or None
TRACKED = {
    "sales.Customer": None, "sales.CustomerLocation": "customer", "sales.CustomerBuyer": "customer",
    "sales.SalesOrder": None, "sales.SalesOrderLine": "order",
    "purchasing.Vendor": None, "purchasing.PurchaseOrder": None, "purchasing.PurchaseOrderLine": "order",
    "purchasing.Shipment": None, "purchasing.ShipmentLine": "shipment", "purchasing.LandedCost": "shipment",
    "accounting.Account": None, "accounting.BankReconciliation": None, "accounting.CustomerInvoice": None, "accounting.CustomerInvoiceLine": "invoice",
    "accounting.CustomerPayment": None, "accounting.CustomerPaymentAllocation": "payment",
    "accounting.VendorBill": None, "accounting.VendorBillLine": "bill",
    "accounting.VendorPayment": None, "accounting.VendorPaymentAllocation": "payment",
    "products.Product": None, "products.ProductWarehouse": "product", "products.ProductCrossReference": "product",
    "products.VendorProduct": "product", "products.HTSCode": None, "products.ItemClass": None,
    "products.PriceList": None, "products.PriceListItem": "price_list",
    "inventory.Warehouse": None, "inventory.StockAdjustment": None, "inventory.StockAdjustmentLine": "adjustment",
    "inventory.StockTransfer": None, "inventory.StockTransferLine": "transfer",
    "returns.CustomerReturn": None, "returns.CustomerReturnLine": "customer_return",
    "returns.CreditMemo": None, "returns.CreditMemoLine": "credit_memo", "returns.Chargeback": None,
    "returns.VendorReturn": None, "returns.VendorReturnLine": "vendor_return",
    "returns.VendorCredit": None, "returns.VendorCreditLine": "vendor_credit",
    "salesteam.SalesRep": None, "core.Company": None, "core.Role": None, "core.Membership": None,
}
IGNORED_FIELDS = {"id", "created_at", "updated_at", "company", "data", "thumbnail", "logo",
                  # system-calculated: recorded elsewhere (stock moves / journals), too noisy for history
                  "avg_cost", "cost_shipped", "posted_total", "volume_cbm"}

# Fields only shown to users who may see costs (costs.view)
COST_FIELDS = {
    "products.Product": {"standard_cost", "monthly_carrying_cost"},
    "products.VendorProduct": {"unit_cost"},
    "purchasing.PurchaseOrder": {"exchange_rate"},
    "purchasing.PurchaseOrderLine": {"unit_price", "fs_cost_usd", "fs_cost_rmb"},
    "purchasing.ShipmentLine": {"allocated_landed_cost", "unit_landed_cost"},
    "purchasing.LandedCost": {"amount"},
    "inventory.StockAdjustmentLine": {"unit_cost"},
    "returns.VendorReturnLine": {"unit_cost"},
}


def visible_changes(entry, can_see_costs):
    hidden = set() if can_see_costs else COST_FIELDS.get(entry.content_type.model_class()._meta.label, set())
    return [(field_label(entry.content_type, k), v[0], v[1]) for k, v in entry.changes.items() if k not in hidden]


def set_user(user):
    return _user.set(user if getattr(user, "is_authenticated", False) else None)


def reset_user(token):
    _user.reset(token)


def _display(instance, field):
    value = getattr(instance, field.attname)
    if value is None or value == "":
        return ""
    if field.is_relation:
        try:
            return str(getattr(instance, field.name))
        except Exception:
            return f"#{value}"
    if field.choices:
        return str(dict(field.flatchoices).get(value, value))
    if isinstance(value, decimal.Decimal):
        return format(value.normalize(), "f") if value == value.to_integral() else str(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat(sep=" ", timespec="minutes") if isinstance(value, datetime.datetime) \
            else value.isoformat()
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)[:300]


def _fields(model):
    return [f for f in model._meta.concrete_fields if f.name not in IGNORED_FIELDS]


def _pre_save(sender, instance, **kwargs):
    instance._audit_old = None
    if instance.pk:
        old = sender._base_manager.filter(pk=instance.pk).first()
        if old is not None:
            instance._audit_old = {f.name: (getattr(old, f.attname), _display(old, f)) for f in _fields(sender)}


def _parent(instance):
    attr = TRACKED.get(instance._meta.label)
    if not attr:
        return None, None
    parent = getattr(instance, attr, None)
    if parent is None:
        return None, None
    return ContentType.objects.get_for_model(parent), parent.pk


def _log(instance, action, changes):
    from .models import AuditLog

    parent_type, parent_id = _parent(instance)
    AuditLog.objects.create(
        content_type=ContentType.objects.get_for_model(instance), object_id=instance.pk,
        object_repr=str(instance)[:200], parent_type=parent_type, parent_id=parent_id, action=action,
        changes=changes, user=_user.get(),
        company_id=getattr(instance, "company_id", None) if instance._meta.label != "core.Company" else None,
    )


def _post_save(sender, instance, created, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    if created or getattr(instance, "_audit_old", None) is None:
        changes = {f.name: ["", _display(instance, f)] for f in _fields(sender) if _display(instance, f) != ""}
        _log(instance, "create", changes)
        return
    old = instance._audit_old
    changes = {}
    for f in _fields(sender):
        if update_fields is not None and f.name not in update_fields and f.attname not in update_fields:
            continue
        if old[f.name][0] != getattr(instance, f.attname):
            changes[f.name] = [old[f.name][1], _display(instance, f)]
    if changes:
        _log(instance, "update", changes)


def _post_delete(sender, instance, **kwargs):
    _log(instance, "delete", {})


def connect():
    from django.apps import apps

    for label in TRACKED:
        model = apps.get_model(label)
        uid = f"audit-{label}"
        pre_save.connect(_pre_save, sender=model, dispatch_uid=uid + "-pre")
        post_save.connect(_post_save, sender=model, dispatch_uid=uid + "-post")
        post_delete.connect(_post_delete, sender=model, dispatch_uid=uid + "-del")


def history_for(obj, limit=200):
    """Entries for a record and its lines (newest first)."""
    from django.db.models import Q

    from .models import AuditLog

    ct = ContentType.objects.get_for_model(obj)
    return AuditLog.objects.filter(Q(content_type=ct, object_id=obj.pk) | Q(parent_type=ct, parent_id=obj.pk)) \
        .select_related("user", "content_type")[:limit]


def field_label(content_type, name):
    model = content_type.model_class()
    try:
        return str(model._meta.get_field(name).verbose_name).capitalize()
    except Exception:
        return name
