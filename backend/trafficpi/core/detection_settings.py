"""What the camera looks for, as the owner sets it in the dashboard (plan.md D34).

The setting lives in the database's settings table, so the dashboard can change it and the vision
service picks the change up when it is told to reload.
"""

from __future__ import annotations

from typing import Any

DETECT_PEOPLE_KEY = "detect_people"


def people_wanted(stored: Any) -> bool:
    """Whether people are looked for, from what is stored. Only a plain ``false`` turns it off, so
    that a missing, damaged or odd value never leaves the camera blind to anything by accident."""
    return stored is not False
