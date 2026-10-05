"""Frozen snapshots are immutable (Scope §7.4, PRD SC-12, SC-13).

Nothing is ever overwritten. A restatement writes a new version beside the old
one, and the only change the database accepts to a frozen row is the old
version's ``is_current`` going from true to false. Deletes are refused.
"""

from django.db import migrations

FORWARD = """
CREATE FUNCTION score_snapshot_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is frozen: a published period changes only by restatement', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

-- Totals and history: the one permitted change is retiring a version.
CREATE FUNCTION score_rows_immutable() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.is_current AND NOT NEW.is_current
           AND (to_jsonb(NEW) - 'is_current') = (to_jsonb(OLD) - 'is_current') THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION '% is frozen: a published period changes only by restatement', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER score_snapshot_immutable BEFORE UPDATE OR DELETE ON score_snapshot
    FOR EACH ROW EXECUTE FUNCTION score_snapshot_immutable();
CREATE TRIGGER score_total_immutable BEFORE UPDATE OR DELETE ON score_total
    FOR EACH ROW EXECUTE FUNCTION score_rows_immutable();
CREATE TRIGGER score_history_immutable BEFORE UPDATE OR DELETE ON score_history
    FOR EACH ROW EXECUTE FUNCTION score_rows_immutable();
"""

REVERSE = """
DROP TRIGGER IF EXISTS score_history_immutable ON score_history;
DROP TRIGGER IF EXISTS score_total_immutable ON score_total;
DROP TRIGGER IF EXISTS score_snapshot_immutable ON score_snapshot;
DROP FUNCTION IF EXISTS score_rows_immutable();
DROP FUNCTION IF EXISTS score_snapshot_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("scorecards", "0004_score_snapshots")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
