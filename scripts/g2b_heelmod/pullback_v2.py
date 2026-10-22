import re,math,json,sys
# Path wiring only. Travel selection below is the attached pullback_v2 script.
SRC=sys.argv[1]
OUT=sys.argv[2]
lines=open(SRC,newline='').read().split('\n')
SEAM=False;SP=[];SEX=[];INF={'Sparse infill','Internal solid infill'}
# seam feedrates
rf=pf=None
for l in lines:
    m=re.match(r'G1 E-([\d.]+) F(\d+)$',l)
    if m and rf is None: rf=m.group(2)
    m=re.match(r'G1 E([\d.]+) F(\d+)$',l)
    if m and pf is None: pf=m.group(2)
rf=rf or '2400'; pf=pf or '2400'
def parse(l):
    c=l.split(';')[0].strip()
    if not re.match(r'G[01]\b',c): return None
    d={k:float(v) for k,v in re.findall(r'([XYZEF])([-\d.]+)',c)}
    return d
# pass: annotate
typ=None; x=y=0.0; F=None
ins={}  # index -> list of lines inserted BEFORE that line index
pairs=[];layer=-1
i=0;n=len(lines)
lastext=None  # (idx,type)
chain=None
stats_types={}
def reset(): 
    global chain; chain=None
for i,l in enumerate(lines):
    if l.startswith(';LAYER_CHANGE'): layer+=1; reset(); lastext=None; SEAM=False
    if l.startswith(';TYPE:'): typ=l[6:].strip()
    if 'seam' in l.lower() or 'C_SEAM' in l:
        reset(); lastext=None; SEAM=True
    d=parse(l)
    if d is None: continue
    nx,ny=d.get('X',x),d.get('Y',y)
    Fb=F
    if 'F' in d: F=d['F']
    if 'E' in d:
        if 'X' in d or 'Y' in d:
            if chain and chain.get('seam') and typ=='Sparse infill' and chain['len']>10:
                (SEX if chain['y0']>2.0 else SP).append(chain); chain['land']=i; chain['ltype']=typ
            if chain and chain['ok'] and typ in INF and chain['len']>10:
                chain['land']=i; chain['ltype']=typ; pairs.append(chain)
            chain=None; lastext=(i,typ); SEAM=False
        else:
            reset(); lastext=None; SEAM=False
    elif 'Z' in d:
        reset(); lastext=None; SEAM=False
    elif 'X' in d or 'Y' in d:
        if chain is None:
            chain={'start':i,'len':0,'ok':lastext is not None and lastext[1] in INF and typ in INF,'Fbefore':Fb,'layer':layer,'stype':lastext[1] if lastext else None,'last':i,'seam':SEAM,'y0':y}
        chain['len']+=math.hypot(nx-x,ny-y); chain['last']=i; chain['Fafter']=F
        if typ not in INF: chain['ok']=False
    x,y=nx,ny
out=[];after={}
ORIG=len(pairs);pairs=pairs+SP
for c in pairs:
    pre=[f'G1 E-0.5 F{rf}']
    if c['Fbefore'] is not None: pre.append(f'G1 F{c["Fbefore"]:g}')
    ins.setdefault(c['start'],[]).extend(pre)
    after.setdefault(c['last'],[]).extend([f'G1 E0.45 F{pf}',f'G1 F{c["Fafter"]:g}'])
for i,l in enumerate(lines):
    out+=ins.get(i,[]); out.append(l); out+=after.get(i,[])
open(OUT,'w',newline='').write('\n'.join(out))
from collections import Counter
st={'pairs':len(pairs),'infill_pairs':ORIG,'seam_follow':len(SP),'excluded_high_y':len(SEX),'travel_m':round(sum(c['len'] for c in pairs)/1000,2),'retractF':rf,'primeF':pf,
 'start_types':Counter(c['stype'] for c in pairs),'land_types':Counter(c['ltype'] for c in pairs),
 'first_layer':min(c['layer'] for c in pairs) if pairs else None,'ge200':sum(c['layer']>=200 for c in pairs)}

print(json.dumps(st,default=dict,indent=1))
