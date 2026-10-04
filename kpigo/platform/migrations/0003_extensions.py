from django.contrib.postgres.operations import (
    BtreeGistExtension,
    CITextExtension,
    TrigramExtension,
)
from django.db import migrations


class Migration(migrations.Migration):
    """Trusted extensions (PG13+), so the database owner can create them without superuser.

    btree_gist backs the effective-period exclusion constraints, citext the
    case-insensitive directory email, pg_trgm the metric-name similarity guard.
    """

    dependencies = [("platform", "0002_audit_log_append_only")]

    operations = [BtreeGistExtension(), CITextExtension(), TrigramExtension()]
