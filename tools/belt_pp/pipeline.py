"""ApexLight G-code post-processing pipeline (Python stages after the slicer).

Chain on the heel-route box (see travelfix README):
  h_curaE -> pp_c -> pp_d -> efix (heel routing) -> apex_light_post_edit
  -> dorsal outer seam -> inside-wall travels

Only ``finalize_apex_light`` is implemented in-tree; earlier stages live in the
travelfix sandbox. This module wires the deterministic post-edit after pp_d/efix
without changing ``pp_d.py`` or ApexCore output. The dorsal seam is the closed
outer rule from ``postprocess_g2b``, not ``match()``.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import apex_light_post_edit


def finalize_apex_light(text: str) -> str:
    """Last stage: fan lock, drop pp_c seam E pairs, dorsal outer seam, then travels.

    The dorsal start and 0.4 mm overlap are the same closed-outer rule as
    ``postprocess_g2b.match``. Heel-route retract and prime stay E±1.5.
    ``match()`` is not called: that pass would replace them with E±0.5.
    """
    text = apex_light_post_edit.apply_post_edit(text)
    text = _reseat_closed_outers(text)
    return _clear_perimeter_crossings(text)


def _g2b_module():
    g2b = Path(__file__).resolve().parents[2] / "scripts" / "g2b_heelmod"
    if str(g2b) not in sys.path:
        sys.path.insert(0, str(g2b))
    import postprocess_g2b

    return postprocess_g2b


def _reseat_closed_outers(text: str) -> str:
    """Light-only seam geometry. ApexCore gets this from match() instead."""
    return _g2b_module().reseat_closed_outers(text)


def _clear_perimeter_crossings(text: str) -> str:
    """Light-only. ApexCore never reaches this module."""
    return _g2b_module().clear_perimeter_crossings(text)


def run_after_pp_d(text: str, *, material: str | None = None) -> str:
    """Apply post-pp_d stages. ApexLight only; other materials pass through."""
    if material is not None and material != "apex_light":
        return text
    return finalize_apex_light(text)
