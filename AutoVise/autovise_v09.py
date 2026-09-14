"""V0.9 runtime fixes and deep diagnostics for Auto Vise.

This module monkey-patches the v0.8 geometry/support modules at load time so we can
iterate without duplicating the full add-in implementation.
"""
import math
import adsk.core
import adsk.fusion
import adsk.cam

import autovise_geometry as g
import autovise_support as s

DEEP_ROWS = []
LAST_FRAME = None
_ORIG_WRITE = s._write


def _v(v, k):
    return adsk.core.Vector3D.create(v.x*k, v.y*k, v.z*k)


def _pt(p, *vectors):
    q = adsk.core.Point3D.create(p.x, p.y, p.z)
    for vec in vectors:
        q.translateBy(vec)
    return q


def _bbox_tuple(bb):
    return (bb.minPoint.x, bb.minPoint.y, bb.minPoint.z,
            bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z)


def _bbox_union(bounds):
    if not bounds:
        return None
    return (min(b[0] for b in bounds), min(b[1] for b in bounds), min(b[2] for b in bounds),
            max(b[3] for b in bounds), max(b[4] for b in bounds), max(b[5] for b in bounds))


def _center(b):
    return ((b[0]+b[3])/2, (b[1]+b[4])/2, (b[2]+b[5])/2)


def _setup_model_bounds(setup):
    rows, bounds = [], []
    try:
        models = setup.models
        rows.append(f'Setup.models count={models.count}')
        for i in range(models.count):
            obj = models.item(i)
            typ = getattr(obj, 'objectType', type(obj).__name__)
            name = getattr(obj, 'name', '<unnamed>')
            bb = getattr(obj, 'preciseBoundingBox', None) or getattr(obj, 'boundingBox', None)
            rows.append(f'  model[{i}] type={typ} name={name}')
            if bb:
                b = _bbox_tuple(bb); bounds.append(b)
                rows.append('    bbox cm: '
                            f'({b[0]:.6f},{b[1]:.6f},{b[2]:.6f})..'
                            f'({b[3]:.6f},{b[4]:.6f},{b[5]:.6f})')
            occ = adsk.fusion.Occurrence.cast(obj)
            if occ:
                rows.append('    transform2=' + ', '.join(f'{x:.6f}' for x in occ.transform2.asArray()))
            body = adsk.fusion.BRepBody.cast(obj)
            if body:
                ctx = body.assemblyContext
                rows.append(f'    assemblyContext={getattr(ctx, "name", "<none>") if ctx else "<none>"}')
    except Exception as exc:
        rows.append(f'Setup model enumeration failed: {exc}')
    combined = _bbox_union(bounds)
    if combined:
        c = _center(combined)
        rows.append('Combined model bbox cm: '
                    f'({combined[0]:.6f},{combined[1]:.6f},{combined[2]:.6f})..'
                    f'({combined[3]:.6f},{combined[4]:.6f},{combined[5]:.6f}); '
                    f'center=({c[0]:.6f},{c[1]:.6f},{c[2]:.6f})')
    return combined, rows


def _frame(matrix, label, scale=1.0):
    o, x, y, z = matrix.getAsCoordinateSystem()
    if scale != 1.0:
        o = adsk.core.Point3D.create(o.x*scale, o.y*scale, o.z*scale)
    x.normalize(); y.normalize(); z.normalize()
    return {'origin': o, 'x': x, 'y': y, 'z': z, 'label': label, 'scale': scale}


def _stock_corners(frame, stock):
    pts = []
    for xv in (stock['min_x'], stock['max_x']):
        for yv in (stock['min_y'], stock['max_y']):
            for zv in (stock['min_z'], stock['max_z']):
                pts.append(_pt(frame['origin'], _v(frame['x'], xv), _v(frame['y'], yv), _v(frame['z'], zv)))
    return pts


def _points_bbox(pts):
    return (min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts),
            max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts))


def _score(frame, stock, model):
    sb = _points_bbox(_stock_corners(frame, stock))
    if not model:
        return 0.0, 0.0, 0.0, sb
    sc, mc = _center(sb), _center(model)
    dist = math.sqrt(sum((a-b)**2 for a,b in zip(sc,mc)))
    violation = (max(0,sb[0]-model[0]) + max(0,model[3]-sb[3]) +
                 max(0,sb[1]-model[1]) + max(0,model[4]-sb[4]) +
                 max(0,sb[2]-model[2]) + max(0,model[5]-sb[5]))
    return dist + 20*violation, dist, violation, sb


def resolve_frame(setup, stock):
    global LAST_FRAME
    model, rows = _setup_model_bounds(setup)
    DEEP_ROWS.extend(['', '=== DEEP FRAME DEBUG ==='] + rows)
    wcs = setup.workCoordinateSystem.copy()
    candidates = [_frame(wcs, 'WCS direct', 1.0),
                  _frame(wcs, 'WCS direct translation /10', 0.1)]
    inv = wcs.copy()
    try:
        if inv.invert():
            candidates += [_frame(inv, 'WCS inverse', 1.0),
                           _frame(inv, 'WCS inverse translation /10', 0.1)]
    except Exception as exc:
        DEEP_ROWS.append(f'WCS inverse failed: {exc}')
    for f in candidates:
        score, dist, violation, sb = _score(f, stock, model)
        f.update(score=score, dist=dist, violation=violation, stock_bbox=sb)
    candidates.sort(key=lambda f:f['score'])
    for f in candidates:
        o=f['origin']; b=f['stock_bbox']
        DEEP_ROWS.append(
            f'Frame {f["label"]}: origin=({o.x:.6f},{o.y:.6f},{o.z:.6f}) '
            f'stockAABB=({b[0]:.6f},{b[1]:.6f},{b[2]:.6f})..({b[3]:.6f},{b[4]:.6f},{b[5]:.6f}) '
            f'centerDist={f["dist"]*10:.3f}mm containmentViolation={f["violation"]*10:.3f}mm score={f["score"]:.6f}')
    LAST_FRAME = candidates[0]
    DEEP_ROWS.append(f'CHOSEN FRAME: {LAST_FRAME["label"]}')
    if LAST_FRAME['scale'] == 0.1:
        DEEP_ROWS.append('IMPORTANT: raw Setup WCS translation only matches the actual Setup model after /10 scaling.')
    return LAST_FRAME


def patched_place_vise(vise, info, setup, stock, grip, axis, fixed_sign):
    frame = resolve_frame(setup, stock)
    origin=frame['origin']; sx=frame['x'].copy(); sy=frame['y'].copy(); sz=frame['z'].copy()
    src_across=info['across'].copy(); src_clamp=info['clamp'].copy(); src_up=adsk.core.Vector3D.create(0,0,1)
    if axis == 'Y':
        target_axis=sy.copy(); stock_face=stock['max_y'] if fixed_sign=='+' else stock['min_y']; inward=sy.copy()
        if fixed_sign=='+': inward.scaleBy(-1)
        anchor=_pt(origin,_v(sx,(stock['min_x']+stock['max_x'])/2),_v(sy,stock_face),_v(sz,stock['min_z']+grip))
    else:
        target_axis=sx.copy(); stock_face=stock['max_x'] if fixed_sign=='+' else stock['min_x']; inward=sx.copy()
        if fixed_sign=='+': inward.scaleBy(-1)
        anchor=_pt(origin,_v(sx,stock_face),_v(sy,(stock['min_y']+stock['max_y'])/2),_v(sz,stock['min_z']+grip))
    target_up=sz.copy(); target_across=inward.crossProduct(target_up); target_across.normalize()
    dest=_pt(anchor,_v(target_across,-info['across_center']),_v(inward,-info['fixed_inner']),_v(target_up,-info['jaw_top']))
    m=adsk.core.Matrix3D.create()
    if not m.setToAlignCoordinateSystems(adsk.core.Point3D.create(0,0,0),src_across,src_clamp,src_up,
                                         dest,target_across,inward,target_up):
        raise RuntimeError('Failed to calculate vise-to-stock transform.')
    vise.transform2=m; adsk.doEvents()
    actual=info['clamp'].copy(); actual.transformBy(m); actual.normalize(); target_axis.normalize()
    align=abs(actual.dotProduct(target_axis))
    DEEP_ROWS.append(f'Placed anchor=({anchor.x:.6f},{anchor.y:.6f},{anchor.z:.6f}) using {frame["label"]}')
    DEEP_ROWS.append(f'Placed vise transform={", ".join(f"{x:.6f}" for x in m.asArray())}')
    try:
        bb=vise.preciseBoundingBox
        DEEP_ROWS.append(f'Placed vise bbox cm=({bb.minPoint.x:.6f},{bb.minPoint.y:.6f},{bb.minPoint.z:.6f})..({bb.maxPoint.x:.6f},{bb.maxPoint.y:.6f},{bb.maxPoint.z:.6f})')
    except Exception: pass
    DEEP_ROWS.append(f'Clamp alignment Setup {axis}={align:.6f}; actual=({actual.x:.6f},{actual.y:.6f},{actual.z:.6f})')
    if align < .999:
        raise RuntimeError(f'Vise orientation verification failed for Setup {axis}.')
    return m,anchor,actual,align


def deterministic_set_jaw_gap(design, vise, required_gap, lines):
    sliders = g._slider_candidates(vise)
    lines.append(f'V0.9 slider candidates: {len(sliders)}')
    if sliders:
        item=sliders[0]
        score,kind,name,motion=item[:4]
        try: current=float(motion.slideValue)
        except Exception: current=0.0
        lines.append(f'Using {kind} "{name}" score={score}; starting slideValue={current*10:.3f} mm')
        trials=[]
        for target in (required_gap,-required_gap):
            try:
                motion.slideValue=target; adsk.doEvents()
                info=g._vise_info(vise); err=abs(info['gap']-required_gap)
                trials.append((err,float(motion.slideValue),info))
                lines.append(f'  ABS target {target*10:+.3f} -> actualSlide {motion.slideValue*10:.3f}, geometricGap {info["gap"]*10:.3f}, error {err*10:.3f} mm')
            except Exception as exc:
                lines.append(f'  ABS target {target*10:+.3f} failed: {exc}')
        if not trials:
            raise RuntimeError(f'Could not drive slider joint "{name}".')
        trials.sort(key=lambda x:x[0]); best=trials[0]
        motion.slideValue=best[1]; adsk.doEvents(); final=g._vise_info(vise)
        if abs(final['gap']-required_gap)>.005:
            raise RuntimeError(f'Slider "{name}" final gap {final["gap"]*10:.3f} mm, target {required_gap*10:.3f} mm.')
        return final,f'slider joint {name} absolute'
    return _ORIG_SET_JAW(design,vise,required_gap,lines)


def _all_managed(root):
    out=[]
    for i in range(root.occurrences.count):
        o=root.occurrences.item(i)
        try:
            a=o.attributes.itemByName(s.ATTR,'managed')
            if (a and a.value=='1') or (o.name or '').startswith('AUTO_VISE:'): out.append(o)
        except Exception: pass
    return out


def clean_insert(design, df):
    try:
        cam=adsk.cam.CAM.cast(adsk.core.Application.get().activeDocument.products.itemByProductType('CAMProductType'))
        if cam:
            for si in range(cam.setups.count):
                setup=cam.setups.item(si); kept=adsk.core.ObjectCollection.create(); removed=0
                try:
                    old=setup.fixtures
                    for i in range(old.count):
                        item=old.item(i); occ=adsk.fusion.Occurrence.cast(item)
                        managed=False
                        if occ:
                            try:
                                a=occ.attributes.itemByName(s.ATTR,'managed'); managed=(a and a.value=='1') or (occ.name or '').startswith('AUTO_VISE:')
                            except Exception: pass
                        if managed: removed+=1
                        elif getattr(item,'isValid',True): kept.add(item)
                    setup.fixtures=kept
                    if removed: DEEP_ROWS.append(f'Cleanup Setup {setup.name}: removed {removed} stale Auto Vise fixture ref(s).')
                except Exception as exc:
                    DEEP_ROWS.append(f'Cleanup Setup {setup.name} fixture refs warning: {exc}')
    except Exception as exc:
        DEEP_ROWS.append(f'Global fixture cleanup warning: {exc}')
    root=design.rootComponent; old=_all_managed(root)
    DEEP_ROWS.append(f'Managed vise occurrences before insert: {len(old)}')
    for o in old:
        try:
            DEEP_ROWS.append(f'  deleting {o.name} token={o.entityToken}')
            o.deleteMe()
        except Exception as exc: DEEP_ROWS.append(f'  delete failed: {exc}')
    adsk.doEvents()
    remaining=_all_managed(root); DEEP_ROWS.append(f'Managed occurrences after delete/doEvents: {len(remaining)}')
    if remaining: raise RuntimeError(f'{len(remaining)} stale Auto Vise occurrence(s) remained after cleanup.')
    occ=root.occurrences.addByInsert(df,adsk.core.Matrix3D.create(),True)
    if not occ: raise RuntimeError('Failed to insert linked vise.')
    try: occ.name='AUTO_VISE: '+df.name
    except Exception: pass
    occ.attributes.add(s.ATTR,'managed','1'); occ.attributes.add(s.ATTR,'data_file_id',df.id); adsk.doEvents()
    try: DEEP_ROWS.append(f'Inserted fresh vise token={occ.entityToken} bbox={s._bb(occ.preciseBoundingBox)}')
    except Exception: pass
    return occ


def debug_stock_resolved(design, setup, stock):
    frame=LAST_FRAME or resolve_frame(setup,stock)
    s._clear_debug(); group=design.rootComponent.customGraphicsGroups.add(); s._debug_groups.append(group)
    o=frame['origin']; x=frame['x']; y=frame['y']; z=frame['z']
    center=_pt(o,_v(x,(stock['min_x']+stock['max_x'])/2),_v(y,(stock['min_y']+stock['max_y'])/2),_v(z,(stock['min_z']+stock['max_z'])/2))
    box=adsk.core.OrientedBoundingBox3D.create(center,x,y,stock['size_x'],stock['size_y'],stock['size_z'])
    body=group.addBRepBody(adsk.fusion.TemporaryBRepManager.get().createBox(box))
    try: body.setOpacity(.18,True)
    except Exception: pass


def write_with_deep(lines):
    global DEEP_ROWS
    if DEEP_ROWS:
        lines = list(lines) + [''] + DEEP_ROWS
    path = _ORIG_WRITE(lines)
    DEEP_ROWS=[]
    return path


_ORIG_SET_JAW = g._set_jaw_gap

def apply_patches():
    global DEEP_ROWS, LAST_FRAME
    DEEP_ROWS=[]; LAST_FRAME=None
    g._set_jaw_gap=deterministic_set_jaw_gap
    g._place_vise=patched_place_vise
    s._insert_fresh_vise=clean_insert
    s._debug_stock=debug_stock_resolved
    s._write=write_with_deep
