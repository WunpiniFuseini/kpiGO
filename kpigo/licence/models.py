"""The installed licence (Schema §13).

Each activation adds a row and supersedes the previous one, so the licence
history of an install kpiGo cannot otherwise see is kept. ``document`` is the
signed file exactly as issued; every other column is read from it.
"""

import uuid

from django.db import models

from kpigo.platform.db import Stamped, one_of

INSTANCE_KINDS = ("production", "non_production")
LICENCE_ROW_STATES = ("active", "superseded")


class Licence(Stamped):
    licence_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    org_id = models.UUIDField()
    install_fingerprint = models.TextField()
    licence_key = models.TextField()
    customer = models.TextField(db_default="")
    tier = models.TextField(db_default="")
    seats = models.IntegerField()
    modules = models.JSONField()
    max_major_version = models.IntegerField()
    instance_kind = models.TextField()
    trial_major_until = models.DateTimeField(null=True)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    last_heartbeat_at = models.DateTimeField(null=True)
    state = models.TextField(db_default="active")
    key_id = models.TextField()
    document = models.TextField()

    class Meta:
        db_table = "licence"
        constraints = [
            one_of("instance_kind", INSTANCE_KINDS, "licence_instance_kind_valid"),
            one_of("state", LICENCE_ROW_STATES, "licence_state_valid"),
            models.UniqueConstraint(
                fields=["org_id"],
                condition=models.Q(state="active"),
                name="licence_one_active",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("issued_at")),
                name="licence_expires_after_issue",
            ),
            models.CheckConstraint(
                condition=models.Q(seats__gte=0, max_major_version__gte=0),
                name="licence_counts_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.licence_key} ({self.state})"
