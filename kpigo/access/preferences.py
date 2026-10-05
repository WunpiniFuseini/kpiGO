"""Per-user view preferences: each key, its allowed values and its default.

A preference changes how one person sees data, never what data exists or who can
see it, so it is the reader's own choice (Scope §8.5 on the matrix toggle).
"""

from __future__ import annotations

from kpigo.access.models import AppUser, UserPreference

# key → (allowed values, default)
PREFERENCES: dict[str, tuple[tuple[str, ...], str]] = {
    # The product-line matrix: one column group per line, or per product group.
    "agent.matrix.view": (("expanded", "grouped"), "expanded"),
}


def preference(account: AppUser | None, key: str) -> str:
    allowed, default = PREFERENCES[key]
    if account is None:
        return default
    row = UserPreference.objects.filter(app_user=account, key=key).first()
    return row.value if row is not None and row.value in allowed else default
