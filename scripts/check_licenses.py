#!/usr/bin/env python3
"""Check the licences of the locked dependencies against the project's licence policy.

The policy: dependencies must be permissively licensed (MIT, BSD, Apache-2.0, ISC and the like),
because TrafficPi is MIT; GPL or AGPL needs the maintainer's explicit approval. This reads what each package says about itself, nothing
more: a wheel can bundle code under another licence than its own metadata names.

    scripts/check_licenses.py python REQUIREMENTS    the packages listed in a `uv export` file,
                                                     read from the environment that runs this
    scripts/check_licenses.py pnpm                   `pnpm licenses list --prod --json` on stdin

Exit status 0 when every package is permissive or on the reviewed list below, 1 otherwise.
"""

from __future__ import annotations

import importlib.metadata as metadata
import json
import re
import sys
from pathlib import Path

# Packages whose licence is not permissive, or does not say, and that a person has looked at. The
# reason is the point of the entry: a new one is added only after reading it.
REVIEWED: dict[str, str] = {
    "certifi": "MPL-2.0; a bundle of CA certificates used as data, not linked or modified (2026-10-09)",
    "caniuse-lite": "CC-BY-4.0; browser-support data that Next.js and Browserslist read while the dashboard is built; not part of what is built or served (2026-10-09; not GPL, so the policy against GPL is not engaged)",
    "lightningcss": "MPL-2.0; Tailwind's CSS compiler, run while the dashboard is built and not part of what is built or served; its files are not modified (2026-10-09; weak, file-level copyleft)",
    "python-prctl": "GPL-3.0; a dependency of the apt package python3-picamera2, imported as an installed system package and not distributed or in the lock file (2026-10-09)",
}

PERMISSIVE = re.compile(
    r"\b(MIT|MIT-0|BSD|Apache|ISC|PSF|Python-2\.0|Python Software Foundation|Unlicense|0BSD|CC0|"
    r"Zlib|HPND|Public Domain|BlueOak|WTFPL)",
    re.IGNORECASE,
)
WEAK_COPYLEFT = re.compile(r"\b(MPL|Mozilla|LGPL|Lesser General Public|EPL|Eclipse|CDDL)", re.IGNORECASE)
STRONG_COPYLEFT = re.compile(
    r"\b(AGPL|GPL|General Public License|SSPL|Commons Clause|BUSL|Business Source)", re.IGNORECASE
)

OK, REVIEW, BAD = "permissive", "needs a look", "copyleft"


def classify_one(text: str) -> str:
    """One licence, not an expression."""
    if WEAK_COPYLEFT.search(text):
        return REVIEW
    if STRONG_COPYLEFT.search(text):
        return BAD
    return OK if PERMISSIVE.search(text) else REVIEW  # a licence nobody can name is not a pass


def classify(expression: str) -> str:
    """A licence or an SPDX-style expression. "A OR B" is as good as the better of the two (the
    user may choose), "A AND B" as bad as the worse (both apply)."""
    order = {OK: 0, REVIEW: 1, BAD: 2}
    if not expression.strip():
        return REVIEW
    alternatives = re.split(r"\s+OR\s+|\s*\|\s*", expression.strip("() "), flags=re.IGNORECASE)
    graded = []
    for alternative in alternatives:
        parts = re.split(r"\s+AND\s+", alternative.strip("() "), flags=re.IGNORECASE)
        graded.append(max((classify_one(p) for p in parts), key=order.__getitem__))
    return min(graded, key=order.__getitem__)


def licence_of(name: str) -> str:
    """What a package says its licence is: an SPDX expression, else the licence text's first line
    if it is short, else the licence classifiers."""
    meta = metadata.distribution(name).metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    free = (meta.get("License") or "").strip()
    if free and len(free) <= 120 and "\n" not in free:
        return free
    classifiers = [
        c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")
    ]
    classifiers = [c for c in classifiers if c not in ("OSI Approved",)]
    if classifiers:
        return " OR ".join(classifiers)
    return free.splitlines()[0][:120] if free else ""


def requirement_names(path: Path) -> list[str]:
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", line)
        if match:
            names.append(match.group(1))
    return names


def report(rows: list[tuple[str, str, str]]) -> int:
    width = max((len(name) for name, _, _ in rows), default=10)
    problems = 0
    for name, licence, verdict in sorted(rows):
        reviewed = REVIEWED.get(name)
        if verdict != OK and reviewed is None:
            problems += 1
        if verdict != OK:
            note = f"reviewed: {reviewed}" if reviewed else "NOT REVIEWED"
            print(f"{name:<{width}}  {licence or '(none given)':<40}  {verdict}: {note}")
    counts = {v: sum(1 for _, _, x in rows if x == v) for v in (OK, REVIEW, BAD)}
    print(f"{len(rows)} packages: {counts[OK]} permissive, {counts[REVIEW]} to look at, {counts[BAD]} copyleft")
    return 1 if problems else 0


def check_python(requirements: Path) -> int:
    rows = []
    for name in requirement_names(requirements):
        try:
            licence = licence_of(name)
        except metadata.PackageNotFoundError:
            rows.append((name, "", REVIEW))  # locked but not installed here: it cannot be read
            continue
        rows.append((name, licence, classify(licence)))
    return report(rows)


def check_pnpm(listing: dict[str, list[dict[str, object]]]) -> int:
    rows = []
    for licence, packages in listing.items():
        verdict = classify(licence)
        rows.extend((str(p.get("name", "?")), licence, verdict) for p in packages)
    return report(rows)


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "python":
        return check_python(Path(argv[2]))
    if len(argv) == 2 and argv[1] == "pnpm":
        return check_pnpm(json.load(sys.stdin))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
