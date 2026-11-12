from django.db import migrations


def create_main_locations(apps, schema_editor):
    """Every existing customer gets a default MAIN ship-to location; existing orders point to it."""
    Customer = apps.get_model("sales", "Customer")
    Location = apps.get_model("sales", "CustomerLocation")
    SalesOrder = apps.get_model("sales", "SalesOrder")
    for c in Customer.objects.all():
        if Location.objects.filter(customer=c).exists():
            continue
        loc = Location.objects.create(
            customer=c, location_id="MAIN", name="Primary Location", address=c.address, city=c.city, state=c.state,
            postal_code=c.postal_code, country=c.country or "US", phone=c.phone, is_default=True,
            address2=(c.shipping_address or "")[:255] if c.shipping_address and not c.address else "")
        SalesOrder.objects.filter(customer=c, ship_to__isnull=True).update(ship_to=loc)


class Migration(migrations.Migration):
    dependencies = [("sales", "0008_locations_ship_window")]
    operations = [migrations.RunPython(create_main_locations, migrations.RunPython.noop)]
