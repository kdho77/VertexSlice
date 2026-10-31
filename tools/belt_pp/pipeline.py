"""ApexLight G-code post-processing pipeline (Python stages after the slicer).

Chain on the heel-route box (see travelfix README):
  h_curaE -> pp_c -> pp_d -> efix (heel routing) -> apex_light_post_edit

Only ``finalize_apex_light`` is implemented in-tree; earlier stages live in the
travelfix sandbox. This module wires the deterministic post-edit after pp_d/efix
without changing ``pp_d.py`` or ApexCore output.
"""
from __future__ import annotations

from . import apex_light_post_edit


def finalize_apex_light(text: str) -> str:
    """Last stage: fan lock + drop pp_c seam E-0.5/E0.45 pairs."""
    return apex_light_post_edit.apply_post_edit(text)


def run_after_pp_d(text: str, *, material: str | None = None) -> str:
    """Apply post-pp_d stages. ApexLight only; other materials pass through."""
    if material is not None and material != "apex_light":
        return text
    return finalize_apex_light(text)
