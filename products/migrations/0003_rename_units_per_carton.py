from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("products", "0002_initial")]

    operations = [
        migrations.RenameField("product", "units_per_carton", "carton_qty"),
    ]
