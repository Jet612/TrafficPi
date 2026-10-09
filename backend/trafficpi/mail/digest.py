"""What the daily email says (plan.md D32): the numbers of a stretch of time, in text and in HTML.

The sums are the Data page's (``api/analytics.py``), taken over the stretch between two scheduled
times instead of whole local days, so that the email and the dashboard cannot disagree: the same
passes count (a pass a person called a false alarm does not), the stop rate is stopped out of those
that could be told and rounds the same way. The headline is the vehicles that did not stop, with
the same length of time before it beside it.

The mail holds numbers and words only. No picture or clip is attached or linked, nothing is loaded
when it is opened (the HTML has no images, scripts, fonts or style sheets of its own), and the only
address in it is the dashboard's, when the owner filled one in. This module does no I/O; the caller
reads the rows and hands them in.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from html import escape
from typing import Literal
from zoneinfo import ZoneInfo

from trafficpi.api import analytics
from trafficpi.api.system_history import MIN_SAMPLES_FOR_OUTAGE
from trafficpi.core.speed import speed_phrase
from trafficpi.db.store import CountRow, StopStatRow
from trafficpi.vision.health import HealthRow

MIN_DOWN_MINUTES = 5  # a camera away for less than this (a restart, the nightly reboot) is no news
MINUTE_MS = 60_000
_DASH = "\u2013"  # an en dash, for ranges of hours and for "nothing"
_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True, slots=True)
class DigestInput:
    """Everything a mail is made from. ``previous_stops`` is None when nothing was recorded in the
    stretch before this one (the first mail), so that nothing is compared with a blank."""

    start_ms: int
    end_ms: int
    tz: ZoneInfo
    stops: Sequence[StopStatRow]  # the passes judged in [start, end)
    previous_stops: Sequence[StopStatRow] | None  # and in the same length of time before
    counts: Sequence[CountRow]  # the vehicles counted in [start, end), a row a minute and line
    has_stop_sign: bool  # whether the profile has a stop sign at all
    down_minutes: int  # minutes of the stretch in which the camera was not counting
    generated_ms: int  # when the mail was made
    link_url: str = ""
    kind: Literal["daily", "test"] = "daily"
    units: str = "mph"  # "mph" or "kmh": how a speed is told


@dataclass(frozen=True, slots=True)
class DigestFacts:
    """The numbers in a mail, for tests and for anything that wants them without the words."""

    start_ms: int
    end_ms: int
    timezone: str
    has_stop_sign: bool
    did_not_stop: int
    rolling: int
    stopped: int
    could_not_tell: int
    judged: int  # all four
    told: int  # the three that could be told: stopped, rolling, did not stop
    previous_did_not_stop: int | None
    stop_rate_percent: int | None
    vehicles: int
    busiest_hour: int | None
    busiest_hour_vehicles: int
    down_minutes: int
    shares: dict[str, int]  # of all the passes judged, as the Data page rounds them (add up to 100)
    fastest_speed_ms: float | None  # the fastest estimated speed in the stretch, in metres a second
    fastest_label: str | None  # of what kind of vehicle
    fastest_at_ms: int | None  # and when it passed
    # For a stretch that touches three days or more: each local day and its fastest estimate (None
    # if no pass of that day had a speed); empty for a stretch of a day or so.
    fastest_by_day: tuple[tuple[str, float | None], ...]


@dataclass(frozen=True, slots=True)
class Digest:
    subject: str
    text: str
    html: str
    facts: DigestFacts


# --- the minutes the camera was away -------------------------------------------------------------


def camera_down_minutes(
    rows: Sequence[HealthRow], start_ms: int, end_ms: int, *, recorded_since_ms: int | None
) -> int:
    """How many whole minutes of ``[start_ms, end_ms)`` the camera was not counting.

    A minute is lost if no row exists for it (the service was not running, or the Pi was off), or
    its row says the camera never worked in it (with enough readings behind that to say so, as the
    System page does). Time before ``recorded_since_ms``, when the record began, is unknown and
    counts for nothing; with no record at all, nothing is claimed.
    """
    if recorded_since_ms is None:
        return 0
    first = math.ceil(max(start_ms, recorded_since_ms) / MINUTE_MS)
    last = end_ms // MINUTE_MS  # exclusive
    seen = {r.minute_start // 60: r for r in rows}
    lost = 0
    for minute in range(first, last):
        row = seen.get(minute)
        if row is None or (row.camera_ok_samples == 0 and row.samples >= MIN_SAMPLES_FOR_OUTAGE):
            lost += 1
    return lost


# --- words ---------------------------------------------------------------------------------------


def duration_text(minutes: int) -> str:
    """A length of time at a glance: 5 minutes, 1 hour 35 minutes, 24 hours, 3 days 1 hour."""

    def unit(n: int, word: str) -> str:
        return f"{n} {word}" if n == 1 else f"{n} {word}s"

    if minutes >= 48 * 60:
        days, rest = divmod(minutes, 24 * 60)
        hours = rest // 60
        return unit(days, "day") + (f" {unit(hours, 'hour')}" if hours else "")
    hours, mins = divmod(minutes, 60)
    parts = ([unit(hours, "hour")] if hours else []) + ([unit(mins, "minute")] if mins else [])
    return " ".join(parts) if parts else "0 minutes"


def _hour12(hour: int) -> tuple[int, str]:
    return (hour % 12 or 12), ("am" if hour < 12 else "pm")


def hour_range_text(hour: int) -> str:
    """The clock hour that begins at ``hour``: 5-6 pm, 11 am-12 pm, 12-1 am (with en dashes)."""
    start, start_suffix = _hour12(hour)
    end, end_suffix = _hour12((hour + 1) % 24)
    if start_suffix == end_suffix:
        return f"{start}{_DASH}{end} {end_suffix}"
    return f"{start} {start_suffix}{_DASH}{end} {end_suffix}"


def _clock(moment: datetime) -> str:
    hour, suffix = _hour12(moment.hour)
    return f"{hour}:{moment.minute:02d} {suffix}"


def _when(moment: datetime) -> str:
    return f"{_DAYS[moment.weekday()]} {moment.day} {_MONTHS[moment.month - 1]}, {_clock(moment)}"


def _short_day(moment: datetime) -> str:
    return f"{_DAYS[moment.weekday()][:3]} {moment.day} {_MONTHS[moment.month - 1]}"


def _vehicles(n: int) -> str:
    return f"{n} vehicle" if n == 1 else f"{n} vehicles"


def _a(label: str | None) -> str:
    kinds = {
        "car": "a car",
        "truck": "a truck",
        "bus": "a bus",
        "motorcycle": "a motorcycle",
        "bicycle": "a bicycle",
    }
    return kinds.get(label or "", "a vehicle")


def _by_day_text(days: tuple[tuple[str, float | None], ...], units: str) -> str:
    """Each day of a long stretch, with its fastest estimate (a dash for a day with none)."""
    return ", ".join(
        f"{_DAYS[date.fromisoformat(day).weekday()][:3]} "
        f"{_DASH if speed is None else speed_phrase(speed, units)}"
        for day, speed in days
    )


def _change_text(now: int, before: int) -> str:
    if now == before:
        return "the same as before"
    return f"{abs(now - before)} {'more' if now > before else 'fewer'} than before"


# --- the facts -----------------------------------------------------------------------------------


def _facts(data: DigestInput) -> DigestFacts:
    previous_start = data.start_ms - (data.end_ms - data.start_ms)
    period = analytics.window_period(
        data.start_ms,
        data.end_ms,
        data.tz,
        previous_start if data.previous_stops is not None else None,
    )
    stops = analytics.stop_stats(data.stops, data.previous_stops, data.tz, period)
    traffic = analytics.traffic_stats(list(data.counts), data.tz, period)
    totals = stops["totals"]
    told = totals["stopped"] + totals["rolling"] + totals["did_not_stop"]
    # Rounded half up, as the Data page does (Python's round() goes to the even number).
    rate = math.floor(100 * totals["stopped"] / told + 0.5) if told else None
    hours = traffic["by_hour"]
    busiest = max(hours, key=lambda h: (h["total"], -h["hour"]))
    fastest = stops["fastest"]
    days = stops["by_day"]
    return DigestFacts(
        start_ms=data.start_ms,
        end_ms=data.end_ms,
        timezone=str(data.tz),
        has_stop_sign=data.has_stop_sign,
        did_not_stop=totals["did_not_stop"],
        rolling=totals["rolling"],
        stopped=totals["stopped"],
        could_not_tell=totals["indeterminate"],
        judged=told + totals["indeterminate"],
        told=told,
        previous_did_not_stop=(
            None if stops["previous"] is None else stops["previous"]["did_not_stop"]
        ),
        stop_rate_percent=rate,
        vehicles=traffic["total"],
        busiest_hour=busiest["hour"] if busiest["total"] else None,
        busiest_hour_vehicles=busiest["total"],
        down_minutes=data.down_minutes,
        shares=analytics.shares_of(totals),
        fastest_speed_ms=None if fastest is None else fastest["speed_ms"],
        fastest_label=None if fastest is None else fastest["label"],
        fastest_at_ms=None if fastest is None else fastest["at"],
        fastest_by_day=(
            tuple(
                (d["date"], None if d["fastest"] is None else d["fastest"]["speed_ms"])
                for d in days
            )
            if len(days) >= 3
            else ()
        ),
    )


# --- the mail ------------------------------------------------------------------------------------


def build_digest(data: DigestInput) -> Digest:
    facts = _facts(data)
    start = datetime.fromtimestamp(data.start_ms / 1000, data.tz)
    end = datetime.fromtimestamp(data.end_ms / 1000, data.tz)
    made = datetime.fromtimestamp(data.generated_ms / 1000, data.tz)
    zone = end.tzname() or str(data.tz)
    period_line = f"{_when(start)} to {_when(end)} ({zone})"
    is_test = data.kind == "test"

    # The headline, and what stands beside it.
    if data.has_stop_sign:
        headline = f"{_vehicles(facts.did_not_stop)} did not stop"
        if facts.previous_did_not_stop is None:
            beside = "Nothing earlier to compare with yet."
        else:
            beside = (
                f"Before: {facts.previous_did_not_stop} "
                f"({_change_text(facts.did_not_stop, facts.previous_did_not_stop)})"
            )
        subject = (
            f"TrafficPi: {facts.did_not_stop} did not stop, {facts.rolling} rolled through "
            f"({_short_day(end)})"
        )
    else:
        headline = "No stop sign is set up"
        beside = ""
        subject = f"TrafficPi daily report ({_short_day(end)})"
    if is_test:
        subject = f"[Test] {subject}"

    # The rows.
    if facts.stop_rate_percent is None:
        rate_text = _DASH
    else:
        rate_text = f"{facts.stop_rate_percent}% (of the {facts.told} passes that could be told)"
    if facts.busiest_hour is None:
        busiest_text = f"{_DASH} (no vehicles were counted)"
    else:
        busiest_text = (
            f"{hour_range_text(facts.busiest_hour)} ({_vehicles(facts.busiest_hour_vehicles)})"
        )
    rows: list[tuple[str, str]] = []
    if data.has_stop_sign:

        def counted(key: str, count: int) -> str:
            # a share of nothing is not shown: the count is, as 0
            return f"{count} ({facts.shares[key]}%)" if facts.judged else str(count)

        rows += [
            ("Stopped", counted("stopped", facts.stopped)),
            ("Rolled through", counted("rolling", facts.rolling)),
            ("Did not stop", counted("did_not_stop", facts.did_not_stop)),
            ("Could not tell", counted("indeterminate", facts.could_not_tell)),
            ("Stop rate", rate_text),
        ]
    rows += [("Busiest hour", busiest_text), ("Vehicles counted", str(facts.vehicles))]
    if data.has_stop_sign:
        if facts.fastest_speed_ms is None:
            fastest_text = f"{_DASH} (no speed was measured in this time)"
        else:
            passed = datetime.fromtimestamp((facts.fastest_at_ms or 0) / 1000, data.tz)
            fastest_text = (
                f"{speed_phrase(facts.fastest_speed_ms, data.units)} "
                f"({_a(facts.fastest_label)}, {_short_day(passed)} {_clock(passed)})"
            )
        rows.append(("Fastest estimate", fastest_text))
        if facts.fastest_by_day:
            rows.append(("Fastest by day", _by_day_text(facts.fastest_by_day, data.units)))

    # The notes: what makes the numbers less than they look.
    notes: list[str] = []
    if is_test:
        notes.append(
            "This is a test of the daily email, sent from the dashboard. The numbers are for the "
            "time up to now; the real mail covers the time between two scheduled times."
        )
    period_minutes = (data.end_ms - data.start_ms) / MINUTE_MS
    if data.down_minutes >= 0.95 * period_minutes and data.down_minutes >= MIN_DOWN_MINUTES:
        notes.append(
            "The camera was not counting at all during this period, so there is nothing to report."
        )
    elif data.down_minutes >= MIN_DOWN_MINUTES:
        notes.append(
            f"The camera was not counting for about {duration_text(data.down_minutes)} of this "
            "period, so the numbers above may be lower than what really passed."
        )
    if not data.has_stop_sign:
        notes.append(
            f"The camera counted {_vehicles(facts.vehicles)} in this period, but nothing was "
            "judged at a stop sign because none is set up. Add one on the dashboard's Setup page."
        )
    elif facts.judged == 0 and data.down_minutes < 0.95 * period_minutes:
        notes.append("No pass was judged at a stop sign in this period.")

    caveat = (
        "A speed is an estimate: how fast a vehicle crossed the picture, compared with its own "
        "size. It is good to about 40% and is not a measurement. "
        if facts.fastest_speed_ms is not None
        else ""
    )
    footer = caveat + (
        "This email holds numbers only: no pictures or clips. You get it because the owner of "
        "this TrafficPi added your address in Settings, under Daily email; to stop it, ask them to "
        f"remove it. Made {_when(made)}."
    )
    title = "TrafficPi daily report"
    text = _text(title, period_line, headline, beside, rows, notes, data.link_url, footer)
    html = _html(title, period_line, headline, beside, rows, notes, data.link_url, footer, facts)
    return Digest(subject=subject, text=text, html=html, facts=facts)


def _text(
    title: str,
    period_line: str,
    headline: str,
    beside: str,
    rows: list[tuple[str, str]],
    notes: list[str],
    link: str,
    footer: str,
) -> str:
    width = max(len(label) for label, _ in rows) + 2
    lines = [title, period_line, "", headline]
    if beside:
        lines.append(beside)
    lines.append("")
    lines += [f"{(label + ':').ljust(width)} {value}" for label, value in rows]
    for note in notes:
        lines += ["", note]
    if link:
        lines += ["", f"Open the dashboard: {link}"]
    lines += ["", "--", footer]
    return "\n".join(lines) + "\n"


# The styles are inline, because mail clients drop or rewrite a style sheet; the few rules that
# a dark screen needs are in a <style> block, and the elements they recolour carry a class (an
# inline colour would win over them: the first dark-mode look found two such texts unreadable).
_BODY = (
    "margin:0;padding:24px 12px;background:#f2f4f7;color:#101828;"
    "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif"
)
_TITLE = "margin:0;font-size:14px;font-weight:600;letter-spacing:.02em"
_PERIOD = "margin:4px 0 0;font-size:14px;color:#475467"
_HEADLINE = "margin:20px 0 0;font-size:28px;line-height:1.2;font-weight:700"
_BESIDE = "margin:4px 0 0;color:#475467;font-size:15px"
_ROWS = "margin-top:20px;font-size:15px"
_LABEL = "padding:8px 12px 8px 0;font-weight:normal;color:#475467;border-top:1px solid #e4e7ec"
_VALUE = "padding:8px 0;font-weight:600;border-top:1px solid #e4e7ec"
_NOTE = (
    "margin:16px 0 0;padding:10px 12px;background:#fffaeb;border-left:4px solid #b54708;"
    "color:#4a2a05;font-size:14px;line-height:1.45"
)
_FOOTER = "margin:24px 0 0;color:#667085;font-size:12px;line-height:1.5"
_DARK = """\
@media (prefers-color-scheme: dark) {
  body, .page { background:#101828 !important; color:#f2f4f7 !important; }
  .card { background:#1d2939 !important; }
  th, .muted { color:#d0d5dd !important; border-color:#344054 !important; }
  td { border-color:#344054 !important; }
  .headline { color:#fda29b !important; }
  .good { color:#75e0a7 !important; }
  .link { color:#84caff !important; }
}"""


def _html(
    title: str,
    period_line: str,
    headline: str,
    beside: str,
    rows: list[tuple[str, str]],
    notes: list[str],
    link: str,
    footer: str,
    facts: DigestFacts,
) -> str:
    e = escape
    bad = facts.has_stop_sign and facts.did_not_stop > 0
    # The tint only helps: the words say the same thing, so nothing depends on seeing the colour.
    tint, cls = ("#b42318", "headline") if bad else ("#067647", "good")
    row_html = "".join(
        f'<tr><th scope="row" align="left" style="{_LABEL}">{e(label)}</th>'
        f'<td align="right" style="{_VALUE}">{e(value)}</td></tr>'
        for label, value in rows
    )
    note_html = "".join(f'<p style="{_NOTE}">{e(note)}</p>' for note in notes)
    link_html = (
        '<p style="margin:20px 0 0">'
        f'<a class="link" href="{e(link, quote=True)}" style="color:#175cd3">'
        "Open the dashboard</a></p>"
        if link
        else ""
    )
    beside_html = f'<p class="muted" style="{_BESIDE}">{e(beside)}</p>' if beside else ""
    return f"""<!doctype html>
<html lang="en" style="color-scheme:light dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{e(title)}</title>
<style>
{_DARK}
</style>
</head>
<body class="page" style="{_BODY}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px">
<tr><td class="card" style="background:#ffffff;border-radius:8px;padding:24px">
<p class="muted" style="{_TITLE}">{e(title)}</p>
<p class="muted" style="{_PERIOD}">{e(period_line)}</p>
<p class="{cls}" style="{_HEADLINE};color:{tint}">{e(headline)}</p>
{beside_html}
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="{_ROWS}">
{row_html}</table>
{note_html}
{link_html}
<p class="muted" style="{_FOOTER}">{e(footer)}</p>
</td></tr>
</table>
</td></tr></table>
</body>
</html>
"""
