"""The settings that decide whether evidence of stop-sign passes is kept, and how long (D24, Q12).

Evidence is a picture and a short clip of a pass that needs a look (rolling, did not stop, could
not tell), kept on this machine for a while and then deleted (the clips of rolls and of passes that
did not stop sooner than the rest). A roll and a pass that did not stop can each be switched off
(D33). They live in the database's settings table, like the trail settings, so that the dashboard
can change them and the vision service picks the change up when it is told to reload.
"""

from __future__ import annotations

from dataclasses import dataclass

ENABLED_KEY = "evidence_enabled"
KEEP_DAYS_KEY = "evidence_keep_days"
MAX_MB_KEY = "evidence_max_mb"
ROLLING_CLIP_HOURS_KEY = "evidence_rolling_clip_hours"
DID_NOT_STOP_CLIP_HOURS_KEY = "evidence_did_not_stop_clip_hours"
KEEP_ROLLING_KEY = "evidence_keep_rolling"
KEEP_DID_NOT_STOP_KEY = "evidence_keep_did_not_stop"

DEFAULT_KEEP_DAYS = 30
DEFAULT_MAX_MB = 2048
DEFAULT_ROLLING_CLIP_HOURS = 3  # a clip is the costly part, and a roll is the mildest violation
DEFAULT_DID_NOT_STOP_CLIP_HOURS = 48
MAX_KEEP_DAYS = 3650  # a limit on typing, not a recommendation
MAX_CLIP_HOURS = MAX_KEEP_DAYS * 24
MIN_MAX_MB = 50
# While less than this is free on the disk no picture or clip is written, and the oldest are deleted
# until there is room again (plan.md D64): the database is on the same card, and a full disk would
# stop the counting. A fixed floor, not a setting: nobody wants the counting to stop to keep a clip.
MIN_FREE_BYTES = 1_000_000_000
MAX_MAX_MB = 1_000_000  # a terabyte


@dataclass(frozen=True, slots=True)
class EvidenceSettings:
    enabled: bool = True  # keep a picture and a clip of the passes that need a look
    keep_days: int = DEFAULT_KEEP_DAYS  # older evidence is deleted
    max_mb: int = DEFAULT_MAX_MB  # and the oldest goes first if there is more than this
    # The clip (not the picture, and not the numbers in the database) of a pass is deleted sooner
    # than ``keep_days`` when it was a roll or a pass that did not stop; the days still cap these.
    rolling_clip_hours: int = DEFAULT_ROLLING_CLIP_HOURS
    did_not_stop_clip_hours: int = DEFAULT_DID_NOT_STOP_CLIP_HOURS
    # One switch for each outcome, beside ``enabled`` (D33). Off means no picture and no clip of
    # that kind of pass; the pass is still judged and counted. A pass that could not be told has
    # no switch of its own and follows ``enabled``. What is already kept is not deleted.
    keep_rolling: bool = True
    keep_did_not_stop: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.keep_days <= MAX_KEEP_DAYS:
            raise ValueError(f"evidence can be kept for 1 to {MAX_KEEP_DAYS} days")
        for hours in (self.rolling_clip_hours, self.did_not_stop_clip_hours):
            if not 1 <= hours <= MAX_CLIP_HOURS:
                raise ValueError(f"a clip can be kept for 1 to {MAX_CLIP_HOURS} hours")
        if not MIN_MAX_MB <= self.max_mb <= MAX_MAX_MB:
            raise ValueError(f"evidence can take {MIN_MAX_MB} to {MAX_MAX_MB} MB")
