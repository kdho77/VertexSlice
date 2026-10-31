"""Cura-style belt-contact outer wall pass: outer-wall extrusions lying within BAND of the layer's lowest Y run at F_BELT."""
import re
_F=re.compile(r'F(\d*\.?\d+)')
def fix(text,band=0.25,fbelt=1200):
    L=text.splitlines(keepends=True);out=[];i=0
    # split into layers by ;LAYER_CHANGE
    idx=[k for k,l in enumerate(L) if l.startswith(';LAYER_CHANGE')]+[len(L)]
    out=L[:idx[0]];n=0
    for a,b in zip(idx,idx[1:]):
        seg=L[a:b];x=y=None;ys=[];t=''
        for l in seg:
            if l.startswith(';TYPE:'): t=l[6:].strip()
            s=l.split(';')[0]
            if t=='Outer wall' and s.startswith('G1') and 'E' in s:
                m=re.search(r'Y(-?\d*\.?\d+)',s)
                if m: ys.append(float(m.group(1)))
        ymin=min(ys) if ys else None;F=None;t='';x=y=None
        for l in seg:
            if l.startswith(';TYPE:'): t=l[6:].strip()
            s=l.split(';')[0]
            if s.startswith(('G0','G1')):
                mf=_F.search(s)
                if mf: F=mf.group(1)
                my=re.search(r'Y(-?\d*\.?\d+)',s);ny=float(my.group(1)) if my else y
                if t=='Outer wall' and ymin is not None and 'E' in s and ('X' in s or 'Y' in s) and y is not None and max(y,ny)<=ymin+band and F and float(F)>fbelt:
                    body=_F.sub('',s).rstrip()+f' F{fbelt}';l=body+'\n';n+=1
                    out.append(l);y=ny;out.append(f'G1 F{F}\n');continue
                y=ny
            out.append(l)
    import sys;print('beltslow moves',n,file=sys.stderr)
    return ''.join(out)
