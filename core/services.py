"""Company (tenant) setup."""
from django.db import transaction

from .models import Company, Currency, Membership
from .permissions import ensure_roles
from .tenancy import use_company


@transaction.atomic
def create_company(name, base_currency="USD", owner=None, copy_setup_from=None):
    """New company with chart of accounts, a main warehouse, built-in roles and the owner as member."""
    from accounting.services import setup_chart_of_accounts
    from inventory.models import Warehouse
    from products.models import UnitOfMeasure

    Currency.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "symbol": "$"})
    company = Company.objects.create(name=name, base_currency_id=base_currency)
    roles = ensure_roles(company)
    with use_company(company):
        setup_chart_of_accounts()
        wh = Warehouse.objects.create(code="MAIN", name="Main Warehouse")
        company.default_warehouse = wh
        company.save(update_fields=["default_warehouse"])
        UnitOfMeasure.objects.get_or_create(code="EA", defaults={"name": "Each"})
        if copy_setup_from is not None:
            copy_product_setup(copy_setup_from, company)
    if owner is not None:
        Membership.objects.create(user=owner, company=company, role=roles["Owner"])
    Company.clear_cache()
    return company


def copy_product_setup(source, target):
    """Copy item classes, item-detail lookups and HTS rows from another company."""
    from products.models import HTSCode, ItemClass, LookupValue

    with use_company(source):
        lookups = list(LookupValue.objects.all())
        classes = list(ItemClass.objects.order_by("level"))
        hts = list(HTSCode.objects.select_related("item_class", "material", "gender"))
    with use_company(target):
        lv_map, ic_map = {}, {}
        for lv in lookups:
            lv_map[lv.pk] = LookupValue.objects.create(kind=lv.kind, code=lv.code, description=lv.description,
                                                       is_active=lv.is_active)
        for ic in classes:
            ic_map[ic.pk] = ItemClass.objects.create(parent=ic_map.get(ic.parent_id), code=ic.code,
                                                     description=ic.description, is_active=ic.is_active)
        for h in hts:
            HTSCode.objects.create(item_class=ic_map[h.item_class_id], material=lv_map[h.material_id],
                                   gender=lv_map.get(h.gender_id), hts_code=h.hts_code, duty_rate=h.duty_rate,
                                   tariff_rate=h.tariff_rate, notes=h.notes)
