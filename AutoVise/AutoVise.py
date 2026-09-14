import adsk.core
import adsk.fusion
import adsk.cam
import json
import os
import traceback
from datetime import datetime

APP_NAME = 'Auto Vise'
CMD_ID = 'JK_AutoVise_Command'
PANEL_ID = 'JK_AutoVise_Panel'
ATTR = 'JK_AutoVise'
HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, 'Resources', 'AutoVise')
SETTINGS = os.path.join(HERE, 'settings.json')
_handlers = []
_debug_groups = []


def _app_ui():
    app = adsk.core.Application.get()
    return app, app.userInterface


def _cam(doc):
    return adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType'))


def _design(doc):
    d = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    return d or adsk.fusion.Design.cast(doc.products.itemByProductType('WorkingModelProductType'))


def _p(setup, name):
    p = setup.parameters.itemByName(name)
    if not p or not p.value:
        raise RuntimeError(f'Missing CAM parameter: {name}')
    return p.value.value


def _stock(setup):
    if setup.stockMode not in (adsk.cam.SetupStockModes.FixedBoxStock, adsk.cam.SetupStockModes.RelativeBoxStock):
        raise RuntimeError('Only Fixed Size Box and Relative Size Box stock are supported.')
    x0, x1 = float(_p(setup, 'stockXLow')), float(_p(setup, 'stockXHigh'))
    y0, y1 = float(_p(setup, 'stockYLow')), float(_p(setup, 'stockYHigh'))
    z0, z1 = float(_p(setup, 'stockZLow')), float(_p(setup, 'stockZHigh'))
    s = dict(min_x=min(x0, x1), max_x=max(x0, x1), min_y=min(y0, y1), max_y=max(y0, y1), min_z=min(z0, z1), max_z=max(z0, z1))
    s['size_x'] = s['max_x'] - s['min_x']; s['size_y'] = s['max_y'] - s['min_y']; s['size_z'] = s['max_z'] - s['min_z']
    return s


def _v(v, k): return adsk.core.Vector3D.create(v.x*k, v.y*k, v.z*k)


def _pt(p, *vs):
    q = adsk.core.Point3D.create(p.x, p.y, p.z)
    for v in vs:
        q.translateBy(v)
    return q


def _center(bb):
    return adsk.core.Point3D.create((bb.minPoint.x+bb.maxPoint.x)/2, (bb.minPoint.y+bb.maxPoint.y)/2, (bb.minPoint.z+bb.maxPoint.z)/2)


def _children(occ):
    out = []
    for i in range(occ.childOccurrences.count):
        c = occ.childOccurrences.item(i); out.append(c); out.extend(_children(c))
    return out


def _named(occ, text):
    text = text.lower()
    for c in _children(occ):
        for n in (c.name or '', c.component.name if c.component else ''):
            if n.lower().split(':')[0].strip() == text:
                return c
    return None


def _range(bb, axis):
    if abs(axis.x) > .5: a, b = axis.x*bb.minPoint.x, axis.x*bb.maxPoint.x
    elif abs(axis.y) > .5: a, b = axis.y*bb.minPoint.y, axis.y*bb.maxPoint.y
    else: a, b = axis.z*bb.minPoint.z, axis.z*bb.maxPoint.z
    return min(a, b), max(a, b)


def _overlap(a, b, axis):
    a0,a1=_range(a,axis); b0,b1=_range(b,axis)
    return max(0.0, min(a1,b1)-max(a0,b0))


def _fixed_face(fixed, moving_bb, clamp, across):
    """Find the actual fixed gripping face. `fixed jaw` also contains the vise base."""
    moving_inner,_ = _range(moving_bb, clamp)
    a0,a1 = _range(moving_bb, across); aw=max(a1-a0,1e-9)
    zh=max(moving_bb.maxPoint.z-moving_bb.minPoint.z,1e-9)
    candidates=[]
    for bi in range(fixed.bRepBodies.count):
        body=fixed.bRepBodies.item(bi)
        for fi in range(body.faces.count):
            face=body.faces.item(fi)
            try:
                bb=face.boundingBox; c0,c1=_range(bb,clamp)
                if c1-c0 > .001: continue
                pos=(c0+c1)/2
                if pos >= moving_inner-.0001: continue
                if _overlap(bb,moving_bb,across) < aw*.5: continue
                zov=max(0,min(bb.maxPoint.z,moving_bb.maxPoint.z)-max(bb.minPoint.z,moving_bb.minPoint.z))
                if zov < zh*.35 or bb.maxPoint.z < moving_bb.maxPoint.z-.75: continue
                candidates.append((pos, getattr(face,'area',0.0), bb, bi, fi))
            except Exception:
                pass
    if not candidates:
        raise RuntimeError('Could not identify the fixed jaw gripping face. Send last_debug.txt.')
    candidates.sort(key=lambda x:(x[0],x[1]), reverse=True)
    return candidates[0], candidates


def _vise_info(vise):
    fixed=_named(vise,'fixed jaw'); moving=_named(vise,'movable jaw')
    if not fixed or not moving: raise RuntimeError('Vise needs child components named "fixed jaw" and "movable jaw".')
    rb=vise.preciseBoundingBox; mb=moving.preciseBoundingBox; rc=_center(rb); mc=_center(mb)
    dx,dy=mc.x-rc.x,mc.y-rc.y
    clamp=adsk.core.Vector3D.create(1 if dx>=0 else -1,0,0) if abs(dx)>=abs(dy) else adsk.core.Vector3D.create(0,1 if dy>=0 else -1,0)
    across=clamp.crossProduct(adsk.core.Vector3D.create(0,0,1)); across.normalize()
    chosen,cands=_fixed_face(fixed,mb,clamp,across)
    fixed_inner,_,fbb,bi,fi=chosen; moving_inner,_=_range(mb,clamp); c0,c1=_range(fbb,across)
    return dict(fixed=fixed,moving=moving,clamp=clamp,across=across,fixed_inner=fixed_inner,moving_inner=moving_inner,
                gap=moving_inner-fixed_inner,across_center=(c0+c1)/2,jaw_top=fbb.maxPoint.z,contact_bb=fbb,
                contact_body=bi,contact_face=fi,candidates=cands)


def _place(vise, info, setup, stock, grip, axis, side):
    o,sx,sy,sz=setup.workCoordinateSystem.getAsCoordinateSystem(); sx.normalize(); sy.normalize(); sz.normalize()
    src_x=info['across'].copy(); src_y=info['clamp'].copy(); src_z=adsk.core.Vector3D.create(0,0,1)
    if axis=='Y':
        inward=sy.copy(); face=stock['max_y'] if side=='Y+' else stock['min_y']
        if side=='Y+': inward.scaleBy(-1)
        anchor=_pt(o,_v(sx,(stock['min_x']+stock['max_x'])/2),_v(sy,face),_v(sz,stock['min_z']+grip))
    else:
        inward=sx.copy(); face=stock['max_x'] if side=='X+' else stock['min_x']
        if side=='X+': inward.scaleBy(-1)
        anchor=_pt(o,_v(sx,face),_v(sy,(stock['min_y']+stock['max_y'])/2),_v(sz,stock['min_z']+grip))
    tx=inward.crossProduct(sz); tx.normalize()
    dest=_pt(anchor,_v(tx,-info['across_center']),_v(inward,-info['fixed_inner']),_v(sz,-info['jaw_top']))
    m=adsk.core.Matrix3D.create()
    if not m.setToAlignCoordinateSystems(adsk.core.Point3D.create(0,0,0),src_x,src_y,src_z,dest,tx,inward,sz):
        raise RuntimeError('Failed to build vise placement transform.')
    vise.transform2=m
    world=info['clamp'].copy(); world.transformBy(m); world.normalize()
    return m,anchor,world


def _move_jaw(design, moving, world, delta):
    m=moving.transform2.copy(); t=m.translation
    t.x+=world.x*delta; t.y+=world.y*delta; t.z+=world.z*delta; m.translation=t
    try:
        if design.rootComponent.transformOccurrences([moving],[m],True): return True
    except Exception: pass
    try: moving.transform2=m; return True
    except Exception: return False


def _load_settings():
    try:
        with open(SETTINGS,'r',encoding='utf-8') as f: return json.load(f)
    except Exception: return {}


def _save_master(df):
    try:
        with open(SETTINGS,'w',encoding='utf-8') as f: json.dump({'vise_data_file_id':df.id,'vise_name':df.name},f,indent=2)
    except Exception: pass


def _master(doc, choose=False):
    app,ui=_app_ui(); st=_load_settings()
    if not choose and st.get('vise_data_file_id'):
        try:
            df=app.data.findFileById(st['vise_data_file_id'])
            if df and (not doc.dataFile or df.parentProject.id==doc.dataFile.parentProject.id): return df,True
        except Exception: pass
    dlg=ui.createCloudFileDialog(); dlg.title='Select vise master (remembered after this)'; dlg.isMultiSelectEnabled=False; dlg.filter='*'
    try:
        if doc.dataFile: dlg.dataFolder=doc.dataFile.parentFolder
    except Exception: pass
    if dlg.showOpen()!=adsk.core.DialogResults.DialogOK or not dlg.dataFile: return None,False
    df=dlg.dataFile
    if not doc.dataFile: raise RuntimeError('Save the machining document to Fusion cloud first.')
    if df.parentProject.id!=doc.dataFile.parentProject.id: raise RuntimeError('Vise and machining file must be in the same Fusion project.')
    _save_master(df); return df,False


def _insert(design, df):
    root=design.rootComponent
    for i in range(root.occurrences.count-1,-1,-1):
        o=root.occurrences.item(i); a=o.attributes.itemByName(ATTR,'managed')
        if a and a.value=='1':
            try:o.deleteMe()
            except Exception:pass
    o=root.occurrences.addByInsert(df,adsk.core.Matrix3D.create(),True)
    if not o: raise RuntimeError('Failed to insert linked vise.')
    try:o.name='AUTO_VISE: '+df.name
    except Exception:pass
    o.attributes.add(ATTR,'managed','1'); return o


def _fixture(setup,vise):
    setup.fixtureEnabled=True; c=adsk.core.ObjectCollection.create(); c.add(vise); setup.fixtures=c


def _xyz(v): return f'({v.x:.6f}, {v.y:.6f}, {v.z:.6f})'

def _bb(bb): return f'min={_xyz(bb.minPoint)} max={_xyz(bb.maxPoint)}'

def _mat(m):
    try:return '['+', '.join(f'{x:.6f}' for x in m.asArray())+']'
    except Exception:return '<unavailable>'


def _debug_stock(design,setup,s):
    global _debug_groups
    for g in _debug_groups:
        try:g.deleteMe()
        except Exception:pass
    _debug_groups=[]; g=design.rootComponent.customGraphicsGroups.add(); _debug_groups.append(g)
    o,x,y,z=setup.workCoordinateSystem.getAsCoordinateSystem(); x.normalize(); y.normalize(); z.normalize()
    c=_pt(o,_v(x,(s['min_x']+s['max_x'])/2),_v(y,(s['min_y']+s['max_y'])/2),_v(z,(s['min_z']+s['max_z'])/2))
    box=adsk.core.OrientedBoundingBox3D.create(c,x,y,s['size_x'],s['size_y'],s['size_z'])
    b=g.addBRepBody(adsk.fusion.TemporaryBRepManager.get().createBox(box))
    try:b.setOpacity(.18,True)
    except Exception:pass


def _write(lines):
    path=os.path.join(HERE,'last_debug.txt'); text='\n'.join(lines)+'\n'
    try:
        with open(path,'w',encoding='utf-8') as f:f.write(text)
    except Exception:path='<write failed>'
    try:adsk.core.Application.get().log(text)
    except Exception:pass
    return path


def _log_info(lines,info,label):
    lines += [label,f'  fixed bbox: {_bb(info["fixed"].preciseBoundingBox)}',f'  moving bbox: {_bb(info["moving"].preciseBoundingBox)}',
              f'  clamp={_xyz(info["clamp"])} across={_xyz(info["across"])}',
              f'  fixed contact={info["fixed_inner"]:.6f} moving contact={info["moving_inner"]:.6f} gap={info["gap"]*10:.3f} mm',
              f'  chosen contact body={info["contact_body"]} face={info["contact_face"]} {_bb(info["contact_bb"])}',
              f'  candidates={len(info["candidates"])}']
    for pos,area,bb,bi,fi in info['candidates'][:8]: lines.append(f'    body={bi} face={fi} pos={pos:.6f} area={area:.4f} {_bb(bb)}')


class Created(adsk.core.CommandCreatedEventHandler):
    def notify(self,args):
        app,ui=_app_ui()
        try:
            cam=_cam(app.activeDocument)
            if not cam or not cam.setups.count: raise RuntimeError('Create a Manufacture Setup first.')
            i=args.command.commandInputs
            dd=i.addDropDownCommandInput('setup','Setup',adsk.core.DropDownStyles.TextListDropDownStyle)
            for n in range(cam.setups.count): dd.listItems.add(cam.setups.item(n).name,n==0,'')
            ax=i.addDropDownCommandInput('axis','Clamp along setup axis',adsk.core.DropDownStyles.TextListDropDownStyle); ax.listItems.add('Y',True,''); ax.listItems.add('X',False,'')
            sd=i.addDropDownCommandInput('side','Fixed jaw side',adsk.core.DropDownStyles.TextListDropDownStyle)
            for n in ('Y+','Y-','X+','X-'): sd.listItems.add(n,n=='Y+','')
            i.addValueInput('grip','Grip depth','mm',adsk.core.ValueInput.createByString('4 mm'))
            i.addBoolValueInput('fixture','Add vise to Setup fixtures',True,'',True)
            i.addBoolValueInput('choose','Choose/change vise master',True,'',False)
            i.addBoolValueInput('debug','Show debug stock + write log',True,'',True)
            name=_load_settings().get('vise_name','none yet')
            i.addTextBoxCommandInput('info','',f'Remembered vise: {name}. First selection is remembered; normal runs bypass the cloud picker.',3,True)
            h=Execute(); args.command.execute.add(h); _handlers.append(h)
        except Exception: ui.messageBox(traceback.format_exc(),APP_NAME)


class Execute(adsk.core.CommandEventHandler):
    def notify(self,args):
        app,ui=_app_ui(); lines=[]
        try:
            doc=app.activeDocument; cam=_cam(doc); design=_design(doc); i=args.command.commandInputs
            name=i.itemById('setup').selectedItem.name; setup=next((cam.setups.item(n) for n in range(cam.setups.count) if cam.setups.item(n).name==name),None)
            axis=i.itemById('axis').selectedItem.name; side=i.itemById('side').selectedItem.name
            if not side.startswith(axis): raise RuntimeError(f'{side} does not match clamp axis {axis}.')
            s=_stock(setup); o,wx,wy,wz=setup.workCoordinateSystem.getAsCoordinateSystem()
            lines=[f'Auto Vise debug {datetime.now().isoformat(timespec="seconds")}',f'Document: {doc.name}',f'Setup: {setup.name}',
                   f'Stock X {s["min_x"]:.6f}..{s["max_x"]:.6f} Y {s["min_y"]:.6f}..{s["max_y"]:.6f} Z {s["min_z"]:.6f}..{s["max_z"]:.6f}',
                   f'Stock size {s["size_x"]*10:.3f} x {s["size_y"]*10:.3f} x {s["size_z"]*10:.3f} mm',f'WCS origin {_xyz(o)}',f'WCS matrix {_mat(setup.workCoordinateSystem)}',f'Fixed side {side}']
            if i.itemById('debug').value:_debug_stock(design,setup,s)
            df,remembered=_master(doc,i.itemById('choose').value)
            if not df:return
            lines.append(f'Vise master {df.name}, remembered={remembered}, id={df.id}')
            vise=_insert(design,df); info=_vise_info(vise); _log_info(lines,info,'Master vise:')
            m,anchor,world=_place(vise,info,setup,s,i.itemById('grip').value,axis,side)
            required=s['size_y'] if axis=='Y' else s['size_x']; delta=required-info['gap']
            lines += [f'Anchor {_xyz(anchor)}',f'Placement {_mat(m)}',f'World clamp {_xyz(world)}',f'Required gap {required*10:.3f} mm',f'Jaw delta {delta*10:.3f} mm']
            ok=_move_jaw(design,info['moving'],world,delta); lines.append(f'Jaw move success={ok}')
            if not ok: raise RuntimeError('Fusion rejected the movable-jaw transform.')
            if i.itemById('fixture').value:_fixture(setup,vise); lines.append('Fixture assigned')
            path=_write(lines)
            ui.messageBox(f'Auto Vise updated for {setup.name}.\nJaw moved {delta*10:.3f} mm.\nDebug log: {path}',APP_NAME)
        except Exception as e:
            lines += ['',f'EXCEPTION: {e}',traceback.format_exc()]; _write(lines); ui.messageBox(f'{e}\n\n{traceback.format_exc()}',APP_NAME)


def run(context):
    _,ui=_app_ui()
    try:
        ws=ui.workspaces.itemById('CAMEnvironment'); cmd=ui.commandDefinitions.itemById(CMD_ID)
        if not cmd: cmd=ui.commandDefinitions.addButtonDefinition(CMD_ID,'Auto Vise','Place a linked vise from CAM stock.',RES)
        h=Created(); cmd.commandCreated.add(h); _handlers.append(h)
        panel=ws.toolbarPanels.itemById(PANEL_ID) or ws.toolbarPanels.add(PANEL_ID,'Auto Vise')
        if not panel.controls.itemById(CMD_ID):
            c=panel.controls.addCommand(cmd); c.isPromotedByDefault=True; c.isPromoted=True
    except Exception: ui.messageBox(traceback.format_exc(),APP_NAME)


def stop(context):
    _,ui=_app_ui()
    try:
        ws=ui.workspaces.itemById('CAMEnvironment'); panel=ws.toolbarPanels.itemById(PANEL_ID) if ws else None
        if panel:
            c=panel.controls.itemById(CMD_ID)
            if c:c.deleteMe()
            panel.deleteMe()
        cmd=ui.commandDefinitions.itemById(CMD_ID)
        if cmd:cmd.deleteMe()
        for g in _debug_groups:
            try:g.deleteMe()
            except Exception:pass
    except Exception: pass
