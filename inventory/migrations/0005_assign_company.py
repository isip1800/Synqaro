from django.db import migrations


def assign(apps, schema_editor):
    """Existing rows belong to the first (original) company."""
    Company = apps.get_model("core", "Company")
    first = Company.objects.order_by("pk").first()
    if first is None:
        return
    for model in apps.get_app_config("inventory").get_models():
        if any(f.name == "company" for f in model._meta.fields) and model.__name__ not in ("Role", "Membership"):
            model._base_manager.filter(company__isnull=True).update(company=first)


class Migration(migrations.Migration):
    dependencies = [("inventory", "0004_multi_company")]
    operations = [migrations.RunPython(assign, migrations.RunPython.noop)]
