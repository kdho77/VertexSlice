# Thin-column composition: columns (X step 0.5) whose bead span <=4mm. Uses fill_where's bead model.
import sys,math,json,os,collections
import numpy as np
fn=sys.argv[1];A=math.pi*(1.75/2)**2;H=float(os.environ.get('H','0.46'));ADJ=H*(1-0.25*math.pi)
layers=[];x=y=None;typ=None
for line in open(fn):
    if line.startswith(';LAYER_CHANGE'): layers.append([]);continue
    if line.startswith(';TYPE:'): typ=line[6:].strip()
    if line[:3]=='G1 ':
        w=dict((m[0],float(m[1:])) for m in line.split(';')[0].split()[1:] if m[0] in 'XYZE')
        nx,ny=w.get('X',x),w.get('Y',y);e=w.get('E',0)
        if layers and e>0 and x is not None and (nx,ny)!=(x,y):
            l=math.dist((x,y),(nx,ny)); layers[-1].append((x,y,nx,ny,e*A/l/H+ADJ,typ))
        x,y=nx,ny
N=len(layers)
def band(lo,hi):
    r=collections.Counter();ws=collections.defaultdict(float);mid=collections.Counter()
    for i in range(int(lo*N),int(hi*N)):
        sg=layers[i]
        if not sg: continue
        xs=[v for s in sg for v in (s[0],s[2])]
        for cx in np.arange(math.floor(min(xs))+0.25,max(xs),0.5):
            iv=[]
            for ax,ay,bx,by,w,t in sg:
                if abs(bx-ax)<1e-9: continue
                tt=(cx-ax)/(bx-ax)
                if tt<0 or tt>1: continue
                yy=ay+tt*(by-ay); sl=abs((by-ay)/(bx-ax)); half=min(w/2*math.sqrt(1+sl*sl),w*2)
                iv.append((yy-half,yy+half,t,w))
            if len(iv)<2: continue
            iv.sort()
            span=max(b for a,b,t,w in iv)-iv[0][0]
            if span>4: continue
            k='%.0f-%.0fmm'%(math.floor(span),math.floor(span)+1)
            r['cols']+=1; r['cols_'+k]+=1
            air=0;m=iv[0][1]
            for a,b,t,w in iv[1:]:
                if a>m+0.05: air+=a-m
                m=max(m,b)
            types=set(t for a,b,t,w in iv)
            inf=any(t in('Sparse infill','Internal solid infill','Solid infill','Top surface','Bottom surface') for t in types)
            comp='+'.join(sorted(types))
            mid[comp]+=1
            if air>0:
                r['air_cols']+=1; ws['air_mm3']+=air*0.5*H; ws['worst']=max(ws['worst'],air)
                r['air_cols_'+k]+=1
                if not inf: r['air_noinfill_cols']+=1; ws['air_noinfill_mm3']+=air*0.5*H; ws['worst_noinfill']=max(ws['worst_noinfill'],air)
            # beads crossing the column (excluding walls)
            r['beads']+=len(iv)
    d=dict(r); d.update({k:round(v,2) for k,v in ws.items()}); d['composition_top']=mid.most_common(6)
    return d
res={'file':os.path.basename(fn),'layers':N,'toe_0_15':band(0,.15),'b15_30':band(.15,.30)}
print(json.dumps(res))
# GATE (Kendon 2026-11-18): every top skin bonds to the bottom skin by direct contact or through infill.
bad=[b for b in ('toe_0_15','b15_30') if res[b].get('air_noinfill_cols',0)>0]
if os.environ.get('BELT_AIR_GATE','1')=='1' and bad:
    print('FAIL: columns with air and no infill crossing in', bad, file=sys.stderr); sys.exit(1)
