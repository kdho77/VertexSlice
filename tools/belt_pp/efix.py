"""Interim belt-shear E correction (F files). Orca belt fork computes E from sqrt(dx^2 + 2*dy^2) in machine
coordinates (vsrepo src/libslic3r/GCode.cpp, 'Belt shear flow correction', ~L6781 and ~L6987). Rescale every
extruding XY move to the machine move length: E *= sqrt(dx^2+dy^2)/sqrt(dx^2+2*dy^2). Relative E (M83) only."""
import re,math
_N=re.compile(r'([XYZEF])(-?\d*\.?\d+)')
def fix(text):
    out=[];x=y=0.0;n=0
    for l in text.splitlines(keepends=True):
        body,sep,com=l.partition(';')
        b=body.strip()
        if b.startswith(('G0 ','G1 ')):
            d=dict(_N.findall(b));nx=float(d['X']) if 'X' in d else x;ny=float(d['Y']) if 'Y' in d else y
            if 'E' in d and float(d['E'])>0 and ('X' in d or 'Y' in d):
                dx,dy=nx-x,ny-y;den=math.sqrt(dx*dx+2*dy*dy)
                if den>0:
                    e=float(d['E'])*math.hypot(dx,dy)/den
                    nb=re.sub(r'E-?\d*\.?\d+',f'E{e:.5f}',body,count=1);n+=1
                    l=nb+sep+com
            x,y=nx,ny
        out.append(l)
    import sys;print('efix moves rescaled',n,file=sys.stderr)
    return ''.join(out)
