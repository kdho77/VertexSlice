"""D pipeline head.

h_curaE execs the prefix of this file and discards the tail. The head must
leave bound: po, mo, fo, post, spec, PR, AL, PP, parse_slice_overrides,
slice_stl, Path, out.
"""
import dataclasses
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(REPO), str(REPO / "validation"), str(HERE)]

import server.profile_registry as PR
import validation.apex_light as AL
import validation.apex_light_postprocess as PP
from server.slice_engine import slice_stl
from server.slice_overrides import parse_slice_overrides
import pp_c

spec = PR.resolve_material("apex_light")
po = dict(spec.process_overrides)
mo = dict(spec.machine_overrides)
fo = dict(spec.filament_overrides)


def post(text):
    return pp_c.apply(text, PP)


out = sys.argv[1] if len(sys.argv) > 1 else "out.gcode"

# h_curaE splits on the next line. The tail is D's own slice, which E replaces.
spec=dataclasses.replace(spec,
    process_overrides=po,
    machine_overrides=mo,
    filament_overrides=fo,
    gcode_postprocess=post,
)
if __name__ == "__main__":
    raise SystemExit("h_curaD is exec'd by h_curaE; run h_curaE.py OUT.gcode")
