"""A target's scope_type must match its metric's declared target_scope (Schema §6).

The check that stops a client half-populating both shapes. Enforced in the
database as well as the workbench, so no path writes a mismatched row.
"""

from django.db import migrations

FORWARD = """
CREATE FUNCTION target_scope_matches_metric() RETURNS trigger AS $$
DECLARE
    declared text;
BEGIN
    SELECT target_scope INTO declared FROM metric WHERE metric_id = NEW.metric_id;
    IF declared IS DISTINCT FROM NEW.scope_type THEN
        RAISE EXCEPTION 'target scope_type % does not match the metric''s target_scope %',
            NEW.scope_type, declared
            USING ERRCODE = 'check_violation', CONSTRAINT = 'target_scope_matches_metric';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER target_scope_matches_metric
    BEFORE INSERT OR UPDATE OF metric_id, scope_type ON target
    FOR EACH ROW EXECUTE FUNCTION target_scope_matches_metric();
"""

REVERSE = """
DROP TRIGGER IF EXISTS target_scope_matches_metric ON target;
DROP FUNCTION IF EXISTS target_scope_matches_metric();
"""


class Migration(migrations.Migration):
    dependencies = [("scorecards", "0001_initial")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
