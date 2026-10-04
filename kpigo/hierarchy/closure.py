"""The visibility closure builder (TDD §6.1).

Dotted lines make the org chart a DAG, so visibility is a closure table rebuilt
per period: every (viewer, visible) pair, how it was reached, and whether it
counts toward the viewer's roll-up. The build is one recursive CTE over the
edges effective in the period. Its ``CYCLE`` clause both guarantees the walk
terminates and reports the cycle; if one exists the build refuses to commit and
the period keeps its previous closure.

Resolution rules, each a deliberate choice:

* An edge is in the period if its effective range overlaps the month at all: a
  manager who led someone for part of the month sees them for that month.
* A subject is in the period (and sees themself) if they hold an assignment
  overlapping the month or appear on an edge in it.
* Visibility is transitive through solid and dotted lines alike; a pair reached
  through any dotted hop is ``via = 'dotted'`` unless an all-solid path exists.
* ``counts_toward_rollup`` on an edge is overridden by ``rollup_policy`` for the
  subordinate's profile, else their role, using the assignment and policy in
  force on the last day of the month. A pair counts if any path to it counts
  on every hop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.db import connection, transaction

from kpigo.action.errors import Conflict
from kpigo.platform.vocab import month_bounds

_SCOPE = """
WITH RECURSIVE
asg AS (
    SELECT DISTINCT ON (subject_id) subject_id, role_code, profile_code
    FROM assignment
    WHERE org_id = %(org)s AND effective_from <= %(last)s
      AND (effective_to IS NULL OR effective_to > %(last)s)
    ORDER BY subject_id, effective_from DESC
),
pol AS (
    SELECT scope_type, scope_code, relationship_type, counts_toward_rollup
    FROM rollup_policy
    WHERE org_id = %(org)s AND effective_from <= %(last)s
      AND (effective_to IS NULL OR effective_to > %(last)s)
),
edge AS (
    SELECT e.subject_id, e.manager_id, e.relationship_type,
           COALESCE(pp.counts_toward_rollup, rp.counts_toward_rollup,
                    e.counts_toward_rollup) AS counts
    FROM reporting_edge e
    LEFT JOIN asg a ON a.subject_id = e.subject_id
    LEFT JOIN pol pp ON pp.scope_type = 'profile' AND pp.scope_code = a.profile_code
                    AND pp.relationship_type = e.relationship_type
    LEFT JOIN pol rp ON rp.scope_type = 'role' AND rp.scope_code = a.role_code
                    AND rp.relationship_type = e.relationship_type
    WHERE e.org_id = %(org)s AND e.effective_from < %(end)s
      AND (e.effective_to IS NULL OR e.effective_to > %(first)s)
),
walk (viewer, visible, solid, counts) AS (
    SELECT manager_id, subject_id, relationship_type = 'solid', counts FROM edge
    UNION ALL
    SELECT e.manager_id, w.visible, w.solid AND e.relationship_type = 'solid',
           w.counts AND e.counts
    FROM walk w JOIN edge e ON e.subject_id = w.viewer
) CYCLE viewer SET is_cycle USING path
"""

_FIND_CYCLE = (
    _SCOPE
    + """
SELECT visible, path::text FROM walk WHERE is_cycle LIMIT 1
"""
)

_INSERT = (
    _SCOPE
    + """,
members AS (
    SELECT subject_id FROM assignment
    WHERE org_id = %(org)s AND effective_from < %(end)s
      AND (effective_to IS NULL OR effective_to > %(first)s)
    UNION SELECT subject_id FROM edge
    UNION SELECT manager_id FROM edge
)
INSERT INTO visibility_closure
    (org_id, period_key, viewer_subject_id, visible_subject_id, via,
     counts_toward_rollup, created_at, created_by)
SELECT %(org)s, %(period)s, viewer, visible, via, counts, now(), %(user)s FROM (
    SELECT viewer, visible,
           CASE WHEN bool_or(solid) THEN 'solid' ELSE 'dotted' END AS via,
           bool_or(counts) AS counts
    FROM walk GROUP BY viewer, visible
    UNION ALL
    SELECT subject_id, subject_id, 'self', true FROM members
) pairs
"""
)


@dataclass(frozen=True)
class BuildResult:
    period_key: str
    rows: int
    subjects: int
    edges: int


def find_cycle(org_id: str, period_key: str) -> list[str] | None:
    """The subject ids around a reporting cycle in the period, or None if the graph is a DAG."""
    first, end = month_bounds(period_key)
    with connection.cursor() as cur:
        cur.execute(_FIND_CYCLE, _params(org_id, period_key, first, end, None))
        row = cur.fetchone()
    if row is None:
        return None
    # path is a text rendering of an array of 1-tuples: {"(a)","(b)",...}
    path = [p.strip('"(){} ') for p in str(row[1]).split(",") if p.strip('"(){} ')]
    start = path[-1]
    return path[path.index(start) :]


def build(org_id: str, period_key: str, user_id: int | None) -> BuildResult:
    """Replace the period's closure. Refuses (raises ``Conflict``) if the graph has a cycle."""
    first, end = month_bounds(period_key)
    cycle = find_cycle(org_id, period_key)
    if cycle is not None:
        raise Conflict(
            f"The reporting lines for {period_key} contain a cycle; the closure was not "
            "rebuilt and the previous one stands.",
            detail={"cycle": cycle},
        )
    params = _params(org_id, period_key, first, end, user_id)
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute(
            "DELETE FROM visibility_closure WHERE org_id = %s AND period_key = %s",
            [org_id, period_key],
        )
        cur.execute(_INSERT, params)
        rows = cur.rowcount
        cur.execute(
            "SELECT count(*) FILTER (WHERE via = 'self'), "
            "count(DISTINCT visible_subject_id) FILTER (WHERE via <> 'self') "
            "FROM visibility_closure WHERE org_id = %s AND period_key = %s",
            [org_id, period_key],
        )
        counted = cur.fetchone()
        cur.execute(
            "SELECT count(*) FROM reporting_edge WHERE org_id = %(org)s "
            "AND effective_from < %(end)s AND (effective_to IS NULL OR effective_to > %(first)s)",
            params,
        )
        edge_count = cur.fetchone()
    return BuildResult(
        period_key=period_key,
        rows=rows,
        subjects=int(counted[0]) if counted else 0,
        edges=int(edge_count[0]) if edge_count else 0,
    )


def _params(
    org_id: str, period_key: str, first: date, end: date, user_id: int | None
) -> dict[str, Any]:
    return {
        "org": org_id,
        "period": period_key,
        "first": first,
        "end": end,
        "last": end - timedelta(days=1),
        "user": user_id,
    }
