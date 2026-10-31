"""pp_c stage entry (seam / heel-side travel).

Printed logic is ``pp_d.postprocess_C`` in the q build (sha a13dbafe…).
This module is the stage the production chain calls. ``pp_d.py`` is unchanged.
"""
from __future__ import annotations


def apply(text: str, postprocess):
    import pp_d

    return pp_d.postprocess_C(text, postprocess)
