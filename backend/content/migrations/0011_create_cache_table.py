"""Create the content cache table.

The cache is the database backend (see home_improvement.settings.CACHES)
because Gunicorn runs multiple workers, and a local-memory cache is per
process: an editor's save would invalidate one worker while the other kept
serving the previous navigation.

Shipping the table as a migration rather than asking the operator to run
`manage.py createcachetable` means the cache works on the first boot after a
deploy, and exists in the test database too.
"""
from django.conf import settings
from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command("createcachetable", verbosity=0,
                 database=schema_editor.connection.alias)


def drop_cache_table(apps, schema_editor):
    from django.core.cache.backends.db import CacheEntry

    with schema_editor.connection.schema_editor() as editor:
        editor.delete_model(CacheEntry)


class Migration(migrations.Migration):
    dependencies = [
        ("content", "0010_alter_page_status"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]
