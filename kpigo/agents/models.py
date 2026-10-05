"""Agent Performance settings (PRD AP-1, AP-2, AP-5).

Agent Performance reads the daily facts ingestion already writes
(``fact_actual_daily``) and the targets the workbench publishes; it owns little
data of its own. Sales and Service are separate modules sharing one engine
(AP-4), so each carries its own settings row. No row means the defaults.
"""

from django.db import models

from kpigo.platform.db import Tracked, one_of

AGENT_PRODUCTS = ("agent_sales", "agent_service")
# ``daily``: month to date, paced by working day. ``weekly`` (the opt-in): week to date.
GRAINS = ("daily", "weekly")


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

    class Meta:
        db_table = "agent_settings"
        constraints = [
            one_of("product", AGENT_PRODUCTS, "agent_settings_product_valid"),
            one_of("grain", GRAINS, "agent_settings_grain_valid"),
            models.CheckConstraint(
                condition=models.Q(pace_cap__gte=1)
                & models.Q(rag_amber__gt=0)
                & models.Q(rag_green__gte=models.F("rag_amber")),
                name="agent_settings_thresholds_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product} settings {self.org_id}"
