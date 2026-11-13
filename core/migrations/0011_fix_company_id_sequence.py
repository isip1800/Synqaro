from django.db import migrations


def reset_sequence(apps, schema_editor):
    """The first company was created with an explicit id, which leaves PostgreSQL's id sequence behind."""
    from django.core.management.color import no_style

    Company = apps.get_model("core", "Company")
    connection = schema_editor.connection
    for sql in connection.ops.sequence_reset_sql(no_style(), [Company]):
        schema_editor.execute(sql)


class Migration(migrations.Migration):
    dependencies = [("core", "0010_roles_and_memberships")]
    operations = [migrations.RunPython(reset_sequence, migrations.RunPython.noop)]
