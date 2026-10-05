"""Agent Performance settings (PRD AP-1, AP-2, AP-5).

Agent Performance reads the daily facts ingestion already writes
(``fact_actual_daily``) and the targets the workbench publishes; it owns little
data of its own. Sales and Service are separate modules sharing one engine
(AP-4), so each carries its own settings row. No row means the defaults.
"""

import uuid

from django.db import models

from kpigo.hierarchy.models import Subject
from kpigo.platform.db import Tracked, no_overlap, one_of, valid_range

AGENT_PRODUCTS = ("agent_sales", "agent_service")
# ``daily``: month to date, paced by working day. ``weekly`` (the opt-in): week to date.
GRAINS = ("daily", "weekly")
# The peer group a leaderboard ranks within (AP-3, Scope §8.1).
COHORT_TYPES = ("all", "profile", "branch", "region", "cohort")


class AgentSettings(Tracked):
    pk = models.CompositePrimaryKey("org_id", "product")
    org_id = models.UUIDField()
    product = models.TextField()
    grain = models.TextField(db_default="daily")
    # What a lower-is-better metric with a zero actual earns, and the ceiling a
    # composite ranking reads one metric's pace at.
    pace_cap = models.DecimalField(max_digits=6, decimal_places=3, db_default=2)
    # RAG on achievement (AP-5): green at or above, amber at or above, else red.
    rag_green = models.DecimalField(max_digits=6, decimal_places=3, db_default=1)
    rag_amber = models.DecimalField(max_digits=6, decimal_places=3, db_default=0.85)
    # The leaderboard (AP-3): ranked by one metric's value, or by the composite
    # (null) mean of capped paces; ties broken by a declared second metric.
    rank_metric_code = models.TextField(null=True)
    tiebreak_metric_code = models.TextField(null=True)
    cohort_type = models.TextField(db_default="profile")

    class Meta:
        db_table = "agent_settings"
        constraints = [
            one_of("product", AGENT_PRODUCTS, "agent_settings_product_valid"),
            one_of("grain", GRAINS, "agent_settings_grain_valid"),
            one_of("cohort_type", COHORT_TYPES, "agent_settings_cohort_type_valid"),
            models.CheckConstraint(
                condition=models.Q(pace_cap__gte=1)
                & models.Q(rag_amber__gt=0)
                & models.Q(rag_green__gte=models.F("rag_amber")),
                name="agent_settings_thresholds_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product} settings {self.org_id}"


class AgentCohort(Tracked):
    """A custom peer group for a leaderboard, when profile, branch or region will not do."""

    cohort_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    product = models.TextField()
    code = models.TextField()
    name = models.TextField()
    status = models.TextField(db_default="active")

    class Meta:
        db_table = "agent_cohort"
        constraints = [
            one_of("product", AGENT_PRODUCTS, "agent_cohort_product_valid"),
            one_of("status", ("active", "retired"), "agent_cohort_status_valid"),
            models.UniqueConstraint(
                fields=["org_id", "product", "code"], name="agent_cohort_code_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product}:{self.code}"


class AgentCohortMember(Tracked):
    """Who is in a custom cohort, effective-dated (half-open, never overlapping)."""

    member_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cohort = models.ForeignKey(AgentCohort, on_delete=models.PROTECT, related_name="members")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="+")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True)

    class Meta:
        db_table = "agent_cohort_member"
        constraints = [
            valid_range("agent_cohort_member_range_valid"),
            no_overlap("agent_cohort_member_no_overlap", "cohort", "subject"),
        ]

    def __str__(self) -> str:
        return f"{self.cohort_id} ∋ {self.subject_id}"


# Who a viewer may see in a module (AP-7): open unless a rule restricts them.
VISIBILITY_APPLIES_TO = ("role", "profile")
# ``all`` lifts a restriction another rule would add; ``subtree`` is the viewer's
# hierarchy (visibility closure); ``region``/``branch`` are the viewer's own on the day.
VISIBILITY_SCOPES = ("all", "subtree", "region", "branch", "self")


class AgentVisibilityRule(Tracked):
    """Restricts which agents a role, or the agents of a profile, see in one module.

    No rule for a viewer means the module's default: open. Rules a viewer matches
    add up, so the widest one wins, as role grants do elsewhere.
    """

    rule_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    product = models.TextField()
    applies_to = models.TextField()
    applies_code = models.TextField()
    scope = models.TextField()

    class Meta:
        db_table = "agent_visibility_rule"
        constraints = [
            one_of("product", AGENT_PRODUCTS, "agent_visibility_rule_product_valid"),
            one_of("applies_to", VISIBILITY_APPLIES_TO, "agent_visibility_rule_applies_valid"),
            one_of("scope", VISIBILITY_SCOPES, "agent_visibility_rule_scope_valid"),
            models.UniqueConstraint(
                fields=["org_id", "product", "applies_to", "applies_code"],
                name="agent_visibility_rule_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product} {self.applies_to} {self.applies_code}: {self.scope}"
