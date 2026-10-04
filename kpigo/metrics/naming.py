"""Metric name normalisation and the duplicate / similarity guards (PRD MR-2, MR-3)."""

from __future__ import annotations

import re
import unicodedata

from django.contrib.postgres.search import TrigramSimilarity

from kpigo.metrics.models import MetricFamily

# Names at or above this trigram similarity are shown in the comparison panel.
SIMILARITY_THRESHOLD = 0.45
MAX_SIMILAR = 5

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise_name(name: str) -> str:
    """Lowercased, accent-folded, de-punctuated, single-spaced: 'Total Deposits (GHS)' →
    'total deposits ghs'."""
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    return _NON_ALNUM.sub(" ", ascii_only.replace("&", " and ")).strip()


def code_from_name(normalised: str) -> str:
    code = normalised.replace(" ", "_")[:60] or "metric"
    return code if code[0].isalpha() else f"m_{code}"


def exact_family(org_id: str, normalised: str) -> MetricFamily | None:
    return MetricFamily.objects.filter(org_id=org_id, normalised_name=normalised).first()


def similar_families(org_id: str, normalised: str) -> list[tuple[MetricFamily, float]]:
    """Families whose normalised name is close to this one, closest first (exact excluded)."""
    found = (
        MetricFamily.objects.filter(org_id=org_id)
        .exclude(normalised_name=normalised)
        .annotate(similarity=TrigramSimilarity("normalised_name", normalised))
        .filter(similarity__gte=SIMILARITY_THRESHOLD)
        .order_by("-similarity", "normalised_name")[:MAX_SIMILAR]
    )
    return [(f, round(float(f.similarity), 3)) for f in found]
