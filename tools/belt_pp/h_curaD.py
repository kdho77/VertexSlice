# Local-only driver: ApexLight slice with Cura-match overrides (variant via VS_VARIANT=A|B).
import sys,os,shutil,tempfile,json,dataclasses
_REPO=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PP=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,os.path.join(_REPO,'validation')); sys.path.insert(0,_REPO)
from pathlib import Path
variant=os.environ.get('VS_VARIANT','A')
import server.profile_registry as PR
import validation.apex_light as AL
import validation.apex_light_postprocess as PP
import inspect,re as _re
if os.environ.get('VS_VARIANT','A')!='BASE':
    # Seam fix: Orca omits ;TYPE: when a layer continues the previous feature; keep the role
    # across ;LAYER_CHANGE so those outer loops are rotated to X140 too.
    _src=inspect.getsource(PP)
    _old='elif l.startswith(";LAYER_CHANGE"):\n            role = ""\n            layer += 1'
    assert _old in _src
    exec(compile(_src.replace(_old,'elif l.startswith(";LAYER_CHANGE"):\n            layer += 1'),PP.__file__,'exec'),PP.__dict__)
def strip_micro_layers(text,min_e=0.2):
    # Arachne emits a sub-mm speck as an extra leading layer (~0.04 mm E) 1 mm above the belt; drop its moves.
    lines=text.splitlines(keepends=True);idx=[i for i,l in enumerate(lines) if l.startswith(';LAYER_CHANGE')]
    if not idx: return text
    for a,b in zip(idx,idx[1:]):
        E=0.0
        for l in lines[a:b]:
            m=_re.match(r'G1 .*?E(-?[\d.]+)',l.split(';')[0])
            if m and float(m.group(1))>0 and ('X' in l or 'Y' in l): E+=float(m.group(1))
        if E==0: continue
        if E>=min_e: break
        for i in range(a,b):
            body=lines[i].split(';')[0]
            if _re.match(r'G1 .*E',body): lines[i]=';micro-layer removed: '+lines[i]
    return ''.join(lines)
from server.slice_overrides import parse_slice_overrides
from server.slice_engine import slice_stl
import apex_belt_y_trim as BT
_force=os.environ.get('VS_FORCE_OFFSET')
import apex_first_layer_y as FLY
_fly=FLY.first_layer_extrusion_y_bounds
if variant!='BASE':
    FLY.first_layer_extrusion_y_bounds=lambda t,*a,**k:_fly(strip_micro_layers(t),*a,**k)
if _force is not None:
    BT.measured_y_mach_offset_mm=lambda *a,**k: float(_force)

out=sys.argv[1]
spec=PR._MATERIALS['apex_light']
po=dict(spec.process_overrides); mo=dict(spec.machine_overrides); fo=dict(spec.filament_overrides)
# common fixes
if variant!='BASE': po.update({
 'filter_out_gap_fill':'9999',
 'wall_generator':'arachne',
 'min_bead_width':'50%',
 'wall_transition_length':'100%',
 'reduce_crossing_wall':'1',
 'max_travel_detour_distance':'0',
})
if variant!='BASE': mo['machine_start_gcode']=AL.MACHINE_START_GCODE.replace(
 'M109 S[nozzle_temperature_initial_layer]\n',
 'M109 S[nozzle_temperature_initial_layer]\nSET_PRESSURE_ADVANCE ADVANCE=0.05\n')
if variant=='A':   # expert list literally
    po.update({'outer_wall_flow_ratio':'1.0','inner_wall_flow_ratio':'1.0','travel_speed':'150'})
    fo.update({'fan_min_speed':['6'],'fan_max_speed':['6'],'additional_cooling_fan_speed':['6'],'overhang_fan_speed':['6']})
    PP.FAN_MAX_PERCENT=6; PP.AUX_FAN_PERCENT=6; PP.TRAVEL_F=150*60.0
if variant=='C': po['wall_generator']='classic'  # comparison only
# File C experiments: extra process overrides as JSON (VS_EXTRA_PO), optional C post-process (VS_PP=C)
po.update(json.loads(os.environ.get('VS_EXTRA_PO') or '{}'))
fo.update({k:[v] for k,v in json.loads(os.environ.get('VS_EXTRA_FO') or '{}').items()})
# variant B: keep Cura-measured E/mm flow (1.42/1.31), Cura fan ramp 35/18, travel 212
_pp=PP.postprocess_apex_light
post=(lambda t:_pp(strip_micro_layers(t))) if variant!='BASE' else spec.gcode_postprocess
if os.environ.get('VS_PP')=='C':
    sys.path.insert(0,_PP); import pp_d as pp_c
    post=lambda t:pp_c.postprocess_C(strip_micro_layers(t),PP)
if os.environ.get('VS_PP')=='RAW':
    post=lambda t:strip_micro_layers(t)
spec=dataclasses.replace(spec,process_overrides=po,machine_overrides=mo,filament_overrides=fo,gcode_postprocess=post)
PR._MATERIALS['apex_light']=spec
prof=PR.get_profile('apex-v2-insole-medium',material='apex_light')
ov=parse_slice_overrides(None,'26','toe_first'); ov['print_direction']='toe_first'
ov.setdefault('wall_loops',spec.default_wall_loops)
td=Path(tempfile.mkdtemp())
stl=Path(os.environ['VS_STL'])
g,st=slice_stl(stl,prof,work_dir=td/'out',process_overrides=ov,belt_insole_place=True,print_direction='toe_first')
shutil.copy(g,out); print(json.dumps({k:st.get(k) for k in ['elapsed_s','belt_y_mach_offset_mm','initial_layer_print_height_mm','layer_height_mm','target_first_layer_y_mm']}))
