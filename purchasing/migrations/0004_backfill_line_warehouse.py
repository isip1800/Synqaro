from django.db import migrations
from django.db.models import OuterRef, Subquery


def backfill(apps, schema_editor):
    """Existing lines go to their order's (receipt's) warehouse."""
    POLine = apps.get_model("purchasing", "PurchaseOrderLine")
    PO = apps.get_model("purchasing", "PurchaseOrder")
    POLine.objects.filter(warehouse__isnull=True).update(
        warehouse=Subquery(PO.objects.filter(pk=OuterRef("order_id")).values("warehouse_id")[:1]))
    GRLine = apps.get_model("purchasing", "GoodsReceiptLine")
    GR = apps.get_model("purchasing", "GoodsReceipt")
    GRLine.objects.filter(warehouse__isnull=True).update(
        warehouse=Subquery(GR.objects.filter(pk=OuterRef("receipt_id")).values("warehouse_id")[:1]))


class Migration(migrations.Migration):
    dependencies = [("purchasing", "0003_line_warehouse")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
