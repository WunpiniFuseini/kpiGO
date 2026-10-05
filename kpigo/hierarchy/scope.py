"""Subject scope backed by the visibility closure (TDD §6.2).

Failures fail closed: a viewer with no subject, a period with no closure, or an
error reading it yields an empty scope, never an unfiltered one.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.utils import timezone

from kpigo.action.context import NoSubjects, SubjectScope
from kpigo.hierarchy.models import Subject, VisibilityClosure
from kpigo.platform.config import reporting_zone
from kpigo.platform.vocab import period_key_for


@dataclass(frozen=True)
class ClosureScope:
    org_id: str
    viewer_subject_id: str
    period_key: str

    def contains(self, subject_id: str) -> bool:
        try:
            return VisibilityClosure.objects.filter(
                org_id=self.org_id,
                period_key=self.period_key,
                viewer_subject_id=self.viewer_subject_id,
                visible_subject_id=subject_id,
            ).exists()
        except (ValidationError, ValueError):
            return False


def current_period_key(org_id: str) -> str:
    return period_key_for(timezone.now().astimezone(reporting_zone(org_id)).date())


def scope_for_subject(subject_id: str, org_id: str) -> SubjectScope:
    """The closure scope of a viewer subject this period. An inactive subject sees nothing."""
    if not Subject.objects.filter(org_id=org_id, subject_id=subject_id, status="active").exists():
        return NoSubjects()
    return ClosureScope(
        org_id=org_id, viewer_subject_id=str(subject_id), period_key=current_period_key(org_id)
    )
