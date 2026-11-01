# Local-only E driver: one file per material (VS_MAT=apex_light|apex_core) from the same STL, D's pipeline.
import sys,os,json,dataclasses,tempfile,shutil
from pathlib import Path
_HERE = Path(__file__).resolve().parent
if os.environ.get('VS_FORCE_OFFSET','')=='': os.environ.pop('VS_FORCE_OFFSET',None)
os.environ.setdefault('VS_VARIANT','B'); os.environ['VS_PP']='C'
MAT=os.environ.get('VS_MAT','apex_light')
sys.path.insert(0,str(_HERE))
src=(_HERE / 'h_curaD.py').read_text()
head,tail=src.split("spec=dataclasses.replace(spec,")
exec(compile(head,'h_curaD_head','exec'))      # builds light po/mo/fo/post exactly as D (B flow/fan, PA 0.05, Arachne...)
E_PO={'wall_loops':'3','wall_generator':'arachne','min_bead_width':'50%','infill_wall_overlap':'15%',
      'slow_down_layer_time':'4','slow_down_min_speed':'20'}
E_FO={'slow_down_layer_time':['4'],'slow_down_min_speed':['20'],'slow_down_for_layer_cooling':['1']}
po.update(E_PO); fo.update(E_FO)
po.update(json.loads(os.environ.get('VS_E_PO') or '{}'))
import pp_d
if os.environ.get('VS_EFIX')=='1':
    import efix; _post0=post; post=lambda t: efix.fix(_post0(t))
FM=float(os.environ.get('VS_FLOWMUL') or 1)
def _mul(d,keys):
    for k in keys:
        m=float(os.environ.get('VS_FLOWMUL_INFILL') or FM) if k=='sparse_infill_flow_ratio' else (float(os.environ.get('VS_FLOWMUL_INNER') or FM) if k=='inner_wall_flow_ratio' else FM)
        if k in d: d[k]=f'{float(d[k])*m:.4f}'; print('flow',k,d[k],file=sys.stderr)
if os.environ.get('VS_TOPREV')=='1':
    # E: an open path that starts on the top side and ends off it is printed reversed (start off-top, free tip only an end)
    pp_d.TOP_REVERSE=True
    pp_d._top_rev=lambda segs,layer,ymin,sx: pp_d.is_top(layer,segs[0][0],ymin) and not pp_d.is_top(layer,segs[-1][1],ymin)
if MAT=='apex_light' and FM!=1: _mul(po,('outer_wall_flow_ratio','inner_wall_flow_ratio','sparse_infill_flow_ratio'))
if os.environ.get('VS_DBASE')=='1' and MAT=='apex_core':
    # D-equivalent ApexCore (what D on ApexCore was: ApexCore spec as-is + D pipeline/pp, PA 0.02)
    core=PR._MATERIALS['apex_core']; dpo=dict(core.process_overrides)
    dpo.update({'filter_out_gap_fill':'9999','wall_generator':'arachne','min_bead_width':'50%','wall_transition_length':'100%','infill_wall_overlap':'40%','seam_position':'aligned','seam_gap':'0'})
    dmo=dict(core.machine_overrides); dmo['use_relative_e_distances']='1'; dmo['before_layer_change_gcode']=mo['before_layer_change_gcode']
    dmo['machine_start_gcode']=AL.MACHINE_START_GCODE.replace('M109 S[nozzle_temperature_initial_layer]\n','M109 S[nozzle_temperature_initial_layer]\nSET_PRESSURE_ADVANCE ADVANCE=0.02\n')
    PP.FAN_MAX_PERCENT=50; PP.AUX_FAN_PERCENT=25
    PR._MATERIALS['apex_core']=dataclasses.replace(core,process_overrides=dpo,machine_overrides=dmo,gcode_postprocess=post)
    prof=PR.get_profile('apex-v2-insole-medium',material='apex_core')
elif MAT=='apex_light':
    spec=dataclasses.replace(spec,process_overrides=po,machine_overrides=mo,filament_overrides=fo,gcode_postprocess=post)
    PR._MATERIALS['apex_light']=spec
    prof=PR.get_profile('apex-v2-insole-medium',material='apex_light')
else:
    core=PR._MATERIALS['apex_core']
    cpo=dict(po)
    for k in ('layer_height','first_layer_height','initial_layer_print_height'): cpo.pop(k,None)  # ApexCore 0.42 profile layers
    for k in list(cpo):
        if 'flow_ratio' in k: cpo.pop(k)
    cpo.update({k:v for k,v in core.process_overrides.items() if 'flow' in k})   # ApexCore flows only
    cpo.setdefault('inner_wall_flow_ratio','1')
    if FM!=1: _mul(cpo,('outer_wall_flow_ratio','inner_wall_flow_ratio','sparse_infill_flow_ratio'))
    cfo=dict(core.filament_overrides); cfo.update(E_FO); cfo.update({'filament_retraction_length':['0'],'filament_retraction_speed':['0'],'filament_deretraction_speed':['0'],'filament_retract_when_changing_layer':['0']})  # no 1.5 mm general retract (ApexLight retract/wipe)                         # ApexCore filament + shine fix
    cmo=dict(mo); cmo['machine_start_gcode']=cmo['machine_start_gcode'].replace('ADVANCE=0.05','ADVANCE=0.02')
    PP.FAN_MAX_PERCENT=50; PP.AUX_FAN_PERCENT=25   # ApexCore filament fan (50 / aux 25)
    cpo.update(json.loads(os.environ.get('VS_FLOWS') or '{}')); cfo.update({k:[v] for k,v in json.loads(os.environ.get('VS_E_FO') or '{}').items()})
    if os.environ.get('VS_PP_RETRACT'): pp_d.RETRACT_MM=float(os.environ['VS_PP_RETRACT'])
    if os.environ.get('VS_TRAVEL_F'): PP.TRAVEL_F=float(os.environ['VS_TRAVEL_F'])*60
    if os.environ.get('VS_BELTSLOW')=='1':
        import beltslow; _p1=post; post=lambda t: beltslow.fix(_p1(t))
    if os.environ.get('VS_TOPTIDY')=='1':
        import toptidy; _p2=post; post=lambda t: toptidy.fix(_p2(t))
    spec=dataclasses.replace(core,process_overrides=cpo,machine_overrides=cmo,filament_overrides=cfo,gcode_postprocess=post,default_wall_loops=3)
    PR._MATERIALS['apex_core']=spec
    prof=PR.get_profile('apex-v2-insole-medium',material='apex_core')
DIR=os.environ.get('VS_DIR','toe_first')
ov=parse_slice_overrides(None,'26',DIR); ov['print_direction']=DIR; ov['wall_loops']=1 if os.environ.get('VS_DBASE')=='1' else int(os.environ.get('VS_WALLS') or 3)
td=Path(tempfile.mkdtemp())
g,st=slice_stl(Path(os.environ['VS_STL']),prof,work_dir=td/'out',process_overrides=ov,belt_insole_place=True,print_direction=DIR)
shutil.copy(g,out); print(json.dumps({k:st.get(k) for k in ['elapsed_s','belt_y_mach_offset_mm','initial_layer_print_height_mm','layer_height_mm','target_first_layer_y_mm']}))
