"""The settings that decide whether evidence of stop-sign passes is kept, and how long (D24, Q12).

Evidence is a picture and a short clip of a pass that needs a look (rolling, did not stop, could
not tell), kept on this machine for a while and then deleted. They live in the database's settings
table, like the trail settings, so that the dashboard can change them and the vision service picks
the change up when it is told to reload.
"""

from __future__ import annotations

from dataclasses import dataclass

ENABLED_KEY = "evidence_enabled"
KEEP_DAYS_KEY = "evidence_keep_days"
MAX_MB_KEY = "evidence_max_mb"

DEFAULT_KEEP_DAYS = 30
DEFAULT_MAX_MB = 2048
MAX_KEEP_DAYS = 3650  # a limit on typing, not a recommendation
MIN_MAX_MB = 50
MAX_MAX_MB = 1_000_000  # a terabyte


@dataclass(frozen=True, slots=True)
class EvidenceSettings:
    enabled: bool = True  # keep a picture and a clip of the passes that need a look
    keep_days: int = DEFAULT_KEEP_DAYS  # older evidence is deleted
    max_mb: int = DEFAULT_MAX_MB  # and the oldest goes first if there is more than this

    def __post_init__(self) -> None:
        if not 1 <= self.keep_days <= MAX_KEEP_DAYS:
            raise ValueError(f"evidence can be kept for 1 to {MAX_KEEP_DAYS} days")
        if not MIN_MAX_MB <= self.max_mb <= MAX_MAX_MB:
            raise ValueError(f"evidence can take {MIN_MAX_MB} to {MAX_MAX_MB} MB")
