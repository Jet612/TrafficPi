#!/usr/bin/env python3
"""Find defective RAM pages, report where they are, and keep them out of use until the next reboot.

Run as root (it reads physical page numbers and pins memory):

    sudo scripts/find_bad_memory.py                  # test 4 GiB and report
    sudo scripts/find_bad_memory.py --hold &         # ... and keep the bad pages out of circulation

How it works. A big block of memory is locked in RAM and filled and read back with several
patterns. A page that returns something other than what was written is bad. Its physical address
comes from /proc/self/pagemap. With --hold the bad pages are handed to a pipe (vmsplice), which
keeps a reference to each, and every good page is given back: nobody else can be given the bad
pages while this process lives. This is a stop-gap for one boot. It cannot test or retire pages
that are in use by other processes at the time, so run it early, and run it again after a reboot.
The real fixes are replacing the board, or reserving the pages in a device-tree overlay.

The kernel on Raspberry Pi OS has no page-offlining support (no soft_offline_page), which is why
this holds pages by hand.
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import mmap
import os
import signal
import struct
import sys
import time
from pathlib import Path

import numpy as np

PAGE = os.sysconf("SC_PAGE_SIZE")  # 16384 on a Pi 5
WORDS_PER_PAGE = PAGE // 8
CHUNK_PAGES = 4096  # 64 MiB at a time, to keep the temporary arrays small
F_SETPIPE_SZ = 1031
PATTERNS = [
    ("zeros", 0x0000000000000000),
    ("ones", 0xFFFFFFFFFFFFFFFF),
    ("0xAA", 0xAAAAAAAAAAAAAAAA),
    ("0x55", 0x5555555555555555),
    ("walking", None),  # a different value in every word, so address lines get exercised
]

libc = ctypes.CDLL("libc.so.6", use_errno=True)


class IoVec(ctypes.Structure):
    _fields_ = [("base", ctypes.c_void_p), ("len", ctypes.c_size_t)]


def pfn_of(pagemap, address: int) -> int | None:
    pagemap.seek(address // PAGE * 8)
    (entry,) = struct.unpack("Q", pagemap.read(8))
    return entry & ((1 << 55) - 1) if entry >> 63 & 1 else None


def test_block(words: np.ndarray, passes: int) -> dict[int, dict[str, set]]:
    """Fill and verify the block. Returns, for each bad page (its number within the block), the
    patterns that failed and which 4 KiB pieces of the page held wrong words."""
    bad: dict[int, dict[str, set]] = {}
    pages = words.size // WORDS_PER_PAGE
    words_per_piece = 4096 // 8
    for _ in range(passes):
        for name, value in PATTERNS:
            for first in range(0, pages, CHUNK_PAGES):
                chunk = words[first * WORDS_PER_PAGE : (first + CHUNK_PAGES) * WORDS_PER_PAGE]
                if value is None:
                    start = np.uint64(first * WORDS_PER_PAGE)
                    expected = (np.arange(chunk.size, dtype=np.uint64) + start) * np.uint64(
                        0x9E3779B97F4A7C15
                    )
                else:
                    expected = np.uint64(value)
                chunk[:] = expected
                for index in np.flatnonzero(chunk != expected):
                    page = first + int(index) // WORDS_PER_PAGE
                    entry = bad.setdefault(page, {"patterns": set(), "pieces": set()})
                    entry["patterns"].add(name)
                    entry["pieces"].add(int(index) % WORDS_PER_PAGE // words_per_piece)
    return bad


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--gib", type=float, default=4.0, help="how much memory to test (default 4)"
    )
    parser.add_argument("--passes", type=int, default=2, help="times to repeat the patterns")
    parser.add_argument("--json", type=Path, help="write the findings here")
    parser.add_argument(
        "--hold", action="store_true", help="keep the bad pages out of use until stopped"
    )
    args = parser.parse_args()
    if os.geteuid() != 0:
        print("run as root (physical addresses and pinning need it)", file=sys.stderr)
        return 1

    size = int(args.gib * (1 << 30)) // PAGE * PAGE
    block = mmap.mmap(-1, size, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
    base = ctypes.addressof(ctypes.c_char.from_buffer(block))
    if libc.mlock(ctypes.c_void_p(base), ctypes.c_size_t(size)) != 0:
        print(
            f"could not lock {size >> 20} MiB: {os.strerror(ctypes.get_errno())}", file=sys.stderr
        )
        return 1
    words = np.frombuffer(block, dtype=np.uint64)
    print(
        f"testing {size >> 20} MiB in {args.passes} pass(es) of {len(PATTERNS)} patterns ...",
        flush=True,
    )
    started = time.monotonic()
    bad = test_block(words, args.passes)
    print(f"done in {time.monotonic() - started:.0f} s: {len(bad)} bad page(s) of {size // PAGE}")

    findings = []
    with Path("/proc/self/pagemap").open("rb") as pagemap:
        for page, what in sorted(bad.items()):
            pfn = pfn_of(pagemap, base + page * PAGE)
            findings.append(
                {
                    "physical_address": hex(pfn * PAGE) if pfn is not None else None,
                    "page_size": PAGE,
                    "failed_patterns": sorted(what["patterns"]),
                    "bad_4k_pieces": sorted(what["pieces"]),
                }
            )
    for finding in findings:
        print(
            f"  physical {finding['physical_address']} ({PAGE} bytes), 4 KiB pieces "
            f"{finding['bad_4k_pieces']}: failed {', '.join(finding['failed_patterns'])}"
        )
    if args.json:
        args.json.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.json}")
    if not findings:
        print("no bad pages in the tested memory (this does not cover memory in use elsewhere)")
        return 0

    if args.hold:
        # Hand each bad page to a pipe, which keeps a reference to it; then give the rest back.
        _pipe_read, pipe_write = os.pipe()  # nobody reads it: the pipe only holds the pages
        fcntl.fcntl(pipe_write, F_SETPIPE_SZ, max(65536, len(bad) * PAGE * 2))
        for page in bad:
            iov = IoVec(base + page * PAGE, PAGE)
            if libc.vmsplice(pipe_write, ctypes.byref(iov), 1, 0) != PAGE:
                print(f"vmsplice failed: {os.strerror(ctypes.get_errno())}", file=sys.stderr)
                return 1
        libc.munlock(ctypes.c_void_p(base), ctypes.c_size_t(size))
        libc.madvise(ctypes.c_void_p(base), ctypes.c_size_t(size), 4)  # MADV_DONTNEED
        print(
            f"holding {len(bad)} bad page(s) out of use (pid {os.getpid()}); "
            f"stop with: kill {os.getpid()}",
            flush=True,
        )
        signal.sigwait([signal.SIGTERM, signal.SIGINT])
    return 2


if __name__ == "__main__":
    sys.exit(main())
