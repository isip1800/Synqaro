import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0002_initial"), ("sales", "0004_backfill_line_warehouse")]
    operations = [
        migrations.AlterField(
            model_name="salesorderline", name="warehouse",
            field=models.ForeignKey(help_text="Warehouse this line ships from",
                                    on_delete=django.db.models.deletion.PROTECT, related_name="so_lines",
                                    to="inventory.warehouse"),
        ),
    ]
