import pytest

from trafficpi.core.evidence_settings import (
    DEFAULT_KEEP_DAYS,
    DEFAULT_MAX_MB,
    MAX_KEEP_DAYS,
    MAX_MAX_MB,
    MIN_MAX_MB,
    EvidenceSettings,
)


def test_evidence_is_kept_for_a_month_up_to_two_gigabytes_unless_said_otherwise() -> None:
    settings = EvidenceSettings()
    assert (settings.enabled, settings.keep_days, settings.max_mb) == (True, 30, 2048)
    assert (DEFAULT_KEEP_DAYS, DEFAULT_MAX_MB) == (30, 2048)


@pytest.mark.parametrize("days", [0, -1, MAX_KEEP_DAYS + 1])
def test_a_keep_time_that_makes_no_sense_is_refused(days: int) -> None:
    with pytest.raises(ValueError, match="1 to 3650 days"):
        EvidenceSettings(keep_days=days)


@pytest.mark.parametrize("megabytes", [0, MIN_MAX_MB - 1, MAX_MAX_MB + 1])
def test_a_size_that_makes_no_sense_is_refused(megabytes: int) -> None:
    with pytest.raises(ValueError, match="MB"):
        EvidenceSettings(max_mb=megabytes)


def test_the_least_and_most_that_are_allowed_are_allowed() -> None:
    EvidenceSettings(keep_days=1, max_mb=MIN_MAX_MB)
    EvidenceSettings(keep_days=MAX_KEEP_DAYS, max_mb=MAX_MAX_MB)
