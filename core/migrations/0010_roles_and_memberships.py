from django.db import migrations

ROLES = {
    "Owner": ["*"],
    "Administrator": ["*", "-users.manage"],
    "Sales": ["sales.view", "sales.edit", "sales.ship", "returns.edit", "products.view", "inventory.view",
              "salesteam.view"],
}


def create(apps, schema_editor):
    """Built-in roles for every company; existing staff logins become members so nobody is locked out."""
    from core.permissions import ROLE_TEMPLATES

    Company = apps.get_model("core", "Company")
    Role = apps.get_model("core", "Role")
    Membership = apps.get_model("core", "Membership")
    User = apps.get_model("auth", "User")
    VendorContact = apps.get_model("portal", "VendorContact")
    SalesRep = apps.get_model("salesteam", "SalesRep")
    vendor_users = set(VendorContact.objects.values_list("user_id", flat=True))
    rep_users = set(SalesRep.objects.exclude(user__isnull=True).values_list("user_id", flat=True))
    for company in Company.objects.all():
        roles = {}
        for name, (desc, perms) in ROLE_TEMPLATES.items():
            roles[name], _ = Role.objects.get_or_create(company=company, name=name, defaults={
                "description": desc, "permissions": perms, "is_system": True})
        for user in User.objects.filter(is_active=True).exclude(pk__in=vendor_users):
            role = roles["Owner"] if user.is_superuser else roles["Sales"] if user.pk in rep_users \
                else roles["Administrator"]
            Membership.objects.get_or_create(user=user, company=company, defaults={"role": role})


class Migration(migrations.Migration):
    dependencies = [("core", "0009_assign_company"), ("portal", "0004_assign_company"),
                    ("salesteam", "0003_assign_company")]
    operations = [migrations.RunPython(create, migrations.RunPython.noop)]
