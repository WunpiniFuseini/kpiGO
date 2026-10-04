from django.db import migrations

FORWARD = """
CREATE FUNCTION audit_log_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log;
DROP FUNCTION IF EXISTS audit_log_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("platform", "0001_initial")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
