"""The settings that decide which trails are saved and for how long.

They live in the database's settings table, so the dashboard can change them and the vision
service picks the change up when it is told to reload.
"""

from __future__ import annotations

from dataclasses import dataclass

ENABLED_KEY = "trails_enabled"
PEOPLE_KEY = "trails_people"
KEEP_DAYS_KEY = "trails_keep_days"

DEFAULT_KEEP_DAYS = 365
MAX_KEEP_DAYS = 3650  # ten years: a limit on typing, not a recommendation


@dataclass(frozen=True, slots=True)
class TrailSettings:
    enabled: bool = True  # save trails at all
    people: bool = False  # and people's, too (vehicles' only, by default)
    keep_days: int = DEFAULT_KEEP_DAYS  # older trails are deleted

    def __post_init__(self) -> None:
        if not 1 <= self.keep_days <= MAX_KEEP_DAYS:
            raise ValueError(f"trails can be kept for 1 to {MAX_KEEP_DAYS} days")
