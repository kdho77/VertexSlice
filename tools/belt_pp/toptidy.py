"""Top-surface tidy stage.

The golden ApexLight recipe leaves VS_TOPTIDY unset, so this stage does not
run on the locked print. The box script was not in the repo; calling fix()
without that source would reprint a different part, so the flag fails closed.
"""
from __future__ import annotations


def fix(text: str) -> str:
    raise RuntimeError(
        "toptidy: box script was not in the repo; refusing to rewrite G-code. "
        "Leave VS_TOPTIDY unset (the golden recipe)."
    )
