#!/usr/bin/env python3
"""gtransform.py - FROZEN helper for bad-change proofs. Rewrites G-code coordinates only.
usage: gtransform.py IN OUT MODE  (MODE: mirror_x | reverse_z | rot180)
mirror_x  X -> 2*cx - X   (mirror across the belt; flips the foot)
reverse_z zt -> 2*cz - zt with zt = Z - Y, i.e. Z -> 2*cz - Z + 2*Y (toe/heel swap along the belt in the de-sheared
          part frame, belt shear kept physical; a mirror)
rot180    both            (180 deg turn on the belt = print direction flip; same foot)
The 45 deg belt shear (Z_gcode = z_part + Y) is preserved, as a real heel-first slice would have it."""
import re, sys
src, dst, mode = sys.argv[1:4]
lines = open(src, errors='replace').read().split('\n')
xs = []; zs = []
num = re.compile(r'([XZ])(-?\d*\.?\d+)')
ynum = re.compile(r'Y(-?\d*\.?\d+)')
for l in lines:
    if l.startswith(('G0', 'G1')):
        for k, v in num.findall(l.split(';', 1)[0]):
            (xs if k == 'X' else zs).append(float(v))
cx = (min(xs) + max(xs)) / 2; cz = (min(zs) + max(zs)) / 2
cur_y = [0.0]
def fix(l):
    if not l.startswith(('G0', 'G1')): return l
    code, _, com = l.partition(';')
    m = ynum.search(code)
    if m: cur_y[0] = float(m.group(1))
    def r(m):
        k, v = m.group(1), float(m.group(2))
        if k == 'X' and mode in ('mirror_x', 'rot180'): v = 2 * cx - v
        if k == 'Z' and mode in ('reverse_z', 'rot180'): v = 2 * cz - v + 2 * cur_y[0]
        return f"{k}{v:.3f}"
    code = num.sub(r, code)
    return code + (';' + com if _ else '')
open(dst, 'w').write('\n'.join(fix(l) for l in lines))
