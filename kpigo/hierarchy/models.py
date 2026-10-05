"""Identity and hierarchy (Schema §1) and dimensions (Schema §5).

A subject is stable identity; everything that changes lives on an assignment.
Scores attach to an assignment, which is what preserves history across role
changes, and the GiST exclusion on ``assignment`` is load-bearing: the database
refuses two role periods for one subject that overlap.
"""

import uuid

from django.db import models

from kpigo.periods.models import PerformanceCycle
from kpigo.platform.db import CITextField, Tracked, no_overlap, one_of, valid_range

RELATIONSHIP_TYPES = ("solid", "dotted")
# ``available``: seen in a feed but not yet reviewed; an Admin activates it (PRD IN-11).
MEMBER_STATUSES = ("available", "active", "inactive")
PRODUCT_LINE_STATUSES = ("available", "active", "retired")
# Which Agent Performance module shows a line or group: both, or one of them.
PRODUCT_LINE_MODULES = ("agent_performance", "agent_sales", "agent_service")
VISIBILITY_VIA = ("self", "solid", "dotted")
DIMENSION_TYPE_PATTERN = r"^[a-z][a-z0-9_]*$"


class Subject(Tracked):
    subject_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    staff_no = models.TextField()
    full_name = models.TextField()
    email = CITextField()
    status = models.TextField(db_default="active")
    joined_at = models.DateField(null=True)
    left_at = models.DateField(null=True)

    class Meta:
        db_table = "subject"
        constraints = [
            models.UniqueConstraint(fields=["org_id", "email"], name="subject_email_unique"),
            models.UniqueConstraint(fields=["org_id", "staff_no"], name="subject_staff_no_unique"),
            one_of("status", ("active", "inactive"), "subject_status_valid"),
        ]

    def __str__(self) -> str:
        return f"{self.staff_no} {self.full_name}"


class Assignment(Tracked):
    assignment_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="assignments")
    role_code = models.TextField()
    profile_code = models.TextField()
    portfolio_code = models.TextField(null=True)
    staff_ref = models.TextField(null=True)
    branch_code = models.TextField(null=True)
    region_code = models.TextField(null=True)
    segment_code = models.TextField(null=True)
    cycle = models.ForeignKey(
        PerformanceCycle, on_delete=models.PROTECT, null=True, related_name="assignments"
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True)

    class Meta:
        db_table = "assignment"
        constraints = [
            valid_range("assignment_range_valid"),
            # EXCLUDE USING gist (subject_id WITH =, daterange(effective_from, effective_to) WITH &&)
            no_overlap("assignment_no_overlap", "subject"),
        ]
        indexes = [models.Index(fields=["org_id", "profile_code"], name="assignment_profile")]

    def __str__(self) -> str:
        return f"{self.subject_id} {self.role_code} from {self.effective_from}"


class ReportingEdge(Tracked):
    """A reporting line. Solid and dotted lines make the org chart a DAG, not a tree."""

    edge_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="manager_edges")
    manager = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="report_edges")
    relationship_type = models.TextField()
    counts_toward_rollup = models.BooleanField(db_default=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True)

    class Meta:
        db_table = "reporting_edge"
        constraints = [
            one_of("relationship_type", RELATIONSHIP_TYPES, "reporting_edge_type_valid"),
            models.CheckConstraint(
                condition=~models.Q(subject=models.F("manager")),
                name="reporting_edge_not_self",
            ),
            valid_range("reporting_edge_range_valid"),
            no_overlap("reporting_edge_no_overlap", "subject", "manager"),
            # One line manager at a time: role-relative assignments ("line manager
            # of") resolve against the solid line, so it must be unambiguous.
            no_overlap(
                "reporting_edge_one_solid_manager",
                "subject",
                condition=models.Q(relationship_type="solid"),
            ),
        ]
        indexes = [models.Index(fields=["org_id", "manager"], name="reporting_edge_manager")]

    def __str__(self) -> str:
        return f"{self.subject_id} → {self.manager_id} ({self.relationship_type})"


class RollupPolicy(Tracked):
    """Admin override of whether a line counts toward roll-up, by role or profile."""

    policy_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    scope_type = models.TextField()
    scope_code = models.TextField()
    relationship_type = models.TextField()
    counts_toward_rollup = models.BooleanField()
    effective_from = models.DateField()
    effective_to = models.DateField(null=True)

    class Meta:
        db_table = "rollup_policy"
        constraints = [
            one_of("scope_type", ("role", "profile"), "rollup_policy_scope_type_valid"),
            one_of("relationship_type", RELATIONSHIP_TYPES, "rollup_policy_type_valid"),
            valid_range("rollup_policy_range_valid"),
            no_overlap(
                "rollup_policy_no_overlap",
                "org_id",
                "scope_type",
                "scope_code",
                "relationship_type",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.scope_type}:{self.scope_code} {self.relationship_type}"


class VisibilityClosure(models.Model):
    """Who can see whom in a period. Materialised by ``visibility.rebuild``, never edited."""

    pk = models.CompositePrimaryKey(
        "org_id", "period_key", "viewer_subject_id", "visible_subject_id"
    )
    org_id = models.UUIDField()
    period_key = models.CharField(max_length=6)
    viewer_subject_id = models.UUIDField()
    visible_subject_id = models.UUIDField()
    via = models.TextField()
    counts_toward_rollup = models.BooleanField()
    created_at = models.DateTimeField(db_default=models.functions.Now())
    created_by = models.BigIntegerField(null=True)

    class Meta:
        db_table = "visibility_closure"
        constraints = [one_of("via", VISIBILITY_VIA, "visibility_closure_via_valid")]

    def __str__(self) -> str:
        return f"{self.period_key} {self.viewer_subject_id} sees {self.visible_subject_id}"


class Dimension(Tracked):
    pk = models.CompositePrimaryKey("org_id", "dimension_type")
    org_id = models.UUIDField()
    dimension_type = models.TextField()
    display_name = models.TextField()
    is_custom = models.BooleanField(db_default=False)
    sort_order = models.IntegerField(db_default=0)

    class Meta:
        db_table = "dim_dimension"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(dimension_type__regex=DIMENSION_TYPE_PATTERN),
                name="dim_dimension_type_format",
            )
        ]

    def __str__(self) -> str:
        return str(self.dimension_type)


class DimMember(Tracked):
    pk = models.CompositePrimaryKey("org_id", "dimension_type", "member_code")
    org_id = models.UUIDField()
    dimension_type = models.TextField()
    member_code = models.TextField()
    member_name = models.TextField()
    parent_code = models.TextField(null=True)
    sort_order = models.IntegerField(db_default=0)
    status = models.TextField(db_default="active")
    # When ingestion first saw the code, for members it registered as available.
    first_detected_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "dim_member"
        constraints = [one_of("status", MEMBER_STATUSES, "dim_member_status_valid")]

    def __str__(self) -> str:
        return f"{self.dimension_type}:{self.member_code}"


class ProductGroup(Tracked):
    group_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    code = models.TextField()
    display_name = models.TextField()
    sort_order = models.IntegerField(db_default=0)
    module = models.TextField(db_default="agent_performance")
    status = models.TextField(db_default="active")

    class Meta:
        db_table = "product_group"
        constraints = [
            models.UniqueConstraint(fields=["org_id", "code"], name="product_group_code_unique"),
            one_of("status", ("active", "retired"), "product_group_status_valid"),
            one_of("module", PRODUCT_LINE_MODULES, "product_group_module_valid"),
        ]

    def __str__(self) -> str:
        return str(self.code)


class ProductLine(Tracked):
    """``available → active → retired`` (App Flow §8).

    A line enters as ``available`` when ingestion sees a code with data and no
    line, so an Admin can never activate a line with no data behind it. It has
    no group until the Admin names and groups it; activating it requires one.
    ``group`` is the group in force now; ``product_line_group`` keeps the
    effective-dated history, so a past period keeps the grouping it had.
    """

    line_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    code = models.TextField()
    display_name = models.TextField()
    sort_order = models.IntegerField(db_default=0)
    group = models.ForeignKey(
        ProductGroup, on_delete=models.PROTECT, null=True, related_name="lines"
    )
    module = models.TextField(db_default="agent_performance")
    status = models.TextField(db_default="available")
    source_member_code = models.TextField(null=True)
    first_detected_at = models.DateTimeField(null=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True)
    # Per-line RAG thresholds on % achieved; null takes the module's.
    rag_green = models.DecimalField(max_digits=6, decimal_places=3, null=True)
    rag_amber = models.DecimalField(max_digits=6, decimal_places=3, null=True)

    class Meta:
        db_table = "product_line"
        constraints = [
            models.UniqueConstraint(fields=["org_id", "code"], name="product_line_code_unique"),
            one_of("status", PRODUCT_LINE_STATUSES, "product_line_status_valid"),
            one_of("module", PRODUCT_LINE_MODULES, "product_line_module_valid"),
            models.CheckConstraint(
                condition=(models.Q(rag_green__isnull=True) & models.Q(rag_amber__isnull=True))
                | (models.Q(rag_amber__gt=0) & models.Q(rag_green__gte=models.F("rag_amber"))),
                name="product_line_rag_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status="available") | models.Q(group__isnull=False),
                name="product_line_grouped_unless_available",
            ),
            valid_range("product_line_range_valid"),
        ]

    def __str__(self) -> str:
        return f"{self.code} ({self.status})"


class ProductLineGroup(Tracked):
    """Which group a line sat in, and when (Scope §8.5): a move is effective-dated."""

    membership_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    line = models.ForeignKey(ProductLine, on_delete=models.PROTECT, related_name="groupings")
    group = models.ForeignKey(ProductGroup, on_delete=models.PROTECT, related_name="+")
    effective_from = models.DateField()
    effective_to = models.DateField(null=True)

    class Meta:
        db_table = "product_line_group"
        constraints = [
            valid_range("product_line_group_range_valid"),
            no_overlap("product_line_group_no_overlap", "line"),
        ]

    def __str__(self) -> str:
        return f"{self.line_id} in {self.group_id}"
