from django.db import migrations
from django.db.models import OuterRef, Subquery


def backfill(apps, schema_editor):
    SOLine = apps.get_model("sales", "SalesOrderLine")
    SO = apps.get_model("sales", "SalesOrder")
    SOLine.objects.filter(warehouse__isnull=True).update(
        warehouse=Subquery(SO.objects.filter(pk=OuterRef("order_id")).values("warehouse_id")[:1]))


class Migration(migrations.Migration):
    dependencies = [("sales", "0003_line_warehouse")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
