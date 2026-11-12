import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0002_initial"), ("purchasing", "0004_backfill_line_warehouse")]
    operations = [
        migrations.AlterField(
            model_name="purchaseorderline", name="warehouse",
            field=models.ForeignKey(help_text="Warehouse this line is received into",
                                    on_delete=django.db.models.deletion.PROTECT, related_name="po_lines",
                                    to="inventory.warehouse"),
        ),
        migrations.AlterField(
            model_name="goodsreceiptline", name="warehouse",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+",
                                    to="inventory.warehouse"),
        ),
    ]
