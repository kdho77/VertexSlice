#!/usr/bin/env python3
"""slice_time.py — slice wall time must not be worse than 260 s.

This is the slicer clock (the server's x-slice-seconds), not the footer print
estimate. ApexLight live was 176.059 s on the PM's host and 158.672 s on the
main docker-proof host. Both are under the cap. A missing time fails closed.
"""
from __future__ import annotations

CAP_S = 260.0


def evaluate(seconds):
    fails = []
    if seconds is None:
        fails.append("slice time was not supplied — fail closed")
    elif seconds > CAP_S + 1e-9:
        fails.append("slice time %.3f s is worse than %.0f s" % (seconds, CAP_S))
    return {
        "gate": "T slice_time",
        "slice_seconds": seconds,
        "cap_seconds": CAP_S,
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }
