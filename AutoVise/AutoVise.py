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
    s['size_x'] = s['max_x'] - s['min_x']
    s['size_y'] = s['max_y'] - s['min_y']
    s['size_z'] = s['max_z'] - s['min_z']
    return s


def _v(v, k):
    return adsk.core.Vector3D.create(v.x * k, v.y * k, v.z * k)


def _pt(p, *vs):
    q = adsk.core.Point3D.create(p.x, p.y, p.z)
    for v in vs:
        q.translateBy(v)
    return q


def _origin():
    return adsk.core.Point3D.create(0, 0, 0)


def _center(bb):
    return adsk.core.Point3D.create(
        (bb.minPoint.x + bb.maxPoint.x) / 2,
        (bb.minPoint.y + bb.maxPoint.y) / 2,
        (bb.minPoint.z + bb.maxPoint.z) / 2,
    )


def _children(occ):
    out = []
    for i in range(occ.childOccurrences.count):
        c = occ.childOccurrences.item(i)
        out.append(c)
        out.extend(_children(c))
    return out


def _named(occ, text):
    text = text.lower()
    for c in _children(occ):
        for n in (c.name or '', c.component.name if c.component else ''):
            if n.lower().split(':')[0].strip() == text:
                return c
    return None


def _range(bb, axis):
    if abs(axis.x) > .5:
        a, b = axis.x * bb.minPoint.x, axis.x * bb.maxPoint.x
    elif abs(axis.y) > .5:
        a, b = axis.y * bb.minPoint.y, axis.y * bb.maxPoint.y
    else:
        a, b = axis.z * bb.minPoint.z, axis.z * bb.maxPoint.z
    return min(a, b), max(a, b)


def _overlap(a, b, axis):
    a0, a1 = _range(a, axis)
    b0, b1 = _range(b, axis)
    return max(0.0, min(a1, b1) - max(a0, b0))


def _fixed_face(fixed, moving_bb, clamp, across):
    moving_inner, _ = _range(moving_bb, clamp)
    a0, a1 = _range(moving_bb, across)
    aw = max(a1 - a0, 1e-9)
    zh = max(moving_bb.maxPoint.z - moving_bb.minPoint.z, 1e-9)
    candidates = []
    for bi in range(fixed.bRepBodies.count):
        body = fixed.bRepBodies.item(bi)
        for fi in range(body.faces.count):
            face = body.faces.item(fi)
            try:
                bb = face.boundingBox
                c0, c1 = _range(bb, clamp)
                if c1 - c0 > .001:
                    continue
                pos = (c0 + c1) / 2
                if pos >= moving_inner - .0001:
                    continue
                if _overlap(bb, moving_bb, across) < aw * .5:
                    continue
                zov = max(0, min(bb.maxPoint.z, moving_bb.maxPoint.z) - max(bb.minPoint.z, moving_bb.minPoint.z))
                if zov < zh * .35 or bb.maxPoint.z < moving_bb.maxPoint.z - .75:
                    continue
                candidates.append((pos, getattr(face, 'area', 0.0), bb, bi, fi))
            except Exception:
                pass
    if not candidates:
        raise RuntimeError('Could not identify the fixed jaw gripping face. Send last_debug.txt.')
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return candidates[0], candidates


def _vise_info(vise):
    fixed = _named(vise, 'fixed jaw')
    moving = _named(vise, 'movable jaw')
    if not fixed or not moving:
        raise RuntimeError('Vise needs child components named "fixed jaw" and "movable jaw".')
    rb = vise.preciseBoundingBox
    mb = moving.preciseBoundingBox
    rc = _center(rb)
    mc = _center(mb)
    dx, dy = mc.x - rc.x, mc.y - rc.y
    if abs(dx) >= abs(dy):
        clamp = adsk.core.Vector3D.create(1 if dx >= 0 else -1, 0, 0)
    else:
        clamp = adsk.core.Vector3D.create(0, 1 if dy >= 0 else -1, 0)
    across = clamp.crossProduct(adsk.core.Vector3D.create(0, 0, 1))
    across.normalize()
    chosen, cands = _fixed_face(fixed, mb, clamp, across)
    fixed_inner, _, fbb, bi, fi = chosen
    moving_inner, _ = _range(mb, clamp)
    c0, c1 = _range(fbb, across)
    return dict(
        fixed=fixed,
        moving=moving,
        clamp=clamp,
        across=across,
        fixed_inner=fixed_inner,
        moving_inner=moving_inner,
        gap=moving_inner - fixed_inner,
        across_center=(c0 + c1) / 2,
        jaw_top=fbb.maxPoint.z,
        contact_bb=fbb,
        contact_body=bi,
        contact_face=fi,
        candidates=cands,
    )


def _move_jaw(design, moving, world, delta):
    m = moving.transform2.copy()
    t = m.translation
    t.x += world.x * delta
    t.y += world.y * delta
    t.z += world.z * delta
    m.translation = t
    try:
        if design.rootComponent.transformOccurrences([moving], [m], True):
            return True
    except Exception:
        pass
    try:
        moving.transform2 = m
        return True
    except Exception:
        return False


def _load_settings():
    try:
        with open(SETTINGS, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings(settings):
    try:
        with open(SETTINGS, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception:
        return False


def _save_master(df):
    s = _load_settings()
    s['vise_data_file_id'] = df.id
    s['vise_name'] = df.name
    _save_settings(s)


def _save_machine_transform(m):
    s = _load_settings()
    s['machine_vise_transform'] = list(m.asArray())
    _save_settings(s)


def _machine_transform():
    a = _load_settings().get('machine_vise_transform')
    if not a or len(a) != 16:
        return None
    m = adsk.core.Matrix3D.create()
    if not m.setWithArray(a):
        return None
    return m


def _master(doc, choose=False):
    app, ui = _app_ui()
    st = _load_settings()
    if not choose and st.get('vise_data_file_id'):
        try:
            df = app.data.findFileById(st['vise_data_file_id'])
            if df and (not doc.dataFile or df.parentProject.id == doc.dataFile.parentProject.id):
                return df, True
        except Exception:
            pass
    dlg = ui.createCloudFileDialog()
    dlg.title = 'Select vise master (remembered after this)'
    dlg.isMultiSelectEnabled = False
    dlg.filter = '*'
    try:
        if doc.dataFile:
            dlg.dataFolder = doc.dataFile.parentFolder
    except Exception:
        pass
    if dlg.showOpen() != adsk.core.DialogResults.DialogOK or not dlg.dataFile:
        return None, False
    df = dlg.dataFile
    if not doc.dataFile:
        raise RuntimeError('Save the machining document to Fusion cloud first.')
    if df.parentProject.id != doc.dataFile.parentProject.id:
        raise RuntimeError('Vise and machining file must be in the same Fusion project.')
    _save_master(df)
    return df, False


def _managed_vise(root):
    for i in range(root.occurrences.count):
        o = root.occurrences.item(i)
        a = o.attributes.itemByName(ATTR, 'managed')
        if a and a.value == '1':
            return o
    return None


def _ensure_vise(design, df, fresh=False):
    root = design.rootComponent
    o = _managed_vise(root)
    if o and not fresh:
        try:
            stored = o.attributes.itemByName(ATTR, 'data_file_id')
            if stored and stored.value == df.id:
                return o, True
        except Exception:
            pass
    if o:
        try:
            o.deleteMe()
        except Exception:
            pass
    o = root.occurrences.addByInsert(df, adsk.core.Matrix3D.create(), True)
    if not o:
        raise RuntimeError('Failed to insert linked vise.')
    try:
        o.name = 'AUTO_VISE: ' + df.name
    except Exception:
        pass
    o.attributes.add(ATTR, 'managed', '1')
    o.attributes.add(ATTR, 'data_file_id', df.id)
    return o, False


def _fixture(setup, vise):
    setup.fixtureEnabled = True
    c = adsk.core.ObjectCollection.create()
    c.add(vise)
    setup.fixtures = c


def _mat_mul(a, b):
    aa = a.asArray()
    bb = b.asArray()
    cc = [0.0] * 16
    for r in range(4):
        for c in range(4):
            cc[r * 4 + c] = sum(aa[r * 4 + k] * bb[k * 4 + c] for k in range(4))
    m = adsk.core.Matrix3D.create()
    if not m.setWithArray(cc):
        raise RuntimeError('Failed to compose transform matrices.')
    return m


def _matrix_near_identity(m, tol=1e-7):
    a = m.asArray()
    ident = [1.0, 0.0, 0.0, 0.0,
             0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0,
             0.0, 0.0, 0.0, 1.0]
    return all(abs(x - y) <= tol for x, y in zip(a, ident))


def _setup_model_targets(setup, design):
    root = design.rootComponent
    occurrences = []
    root_bodies = []
    seen_occ = set()
    seen_body = set()
    models = setup.models
    for i in range(models.count):
        obj = models.item(i)
        occ = adsk.fusion.Occurrence.cast(obj)
        if occ:
            key = occ.entityToken
            if key not in seen_occ:
                seen_occ.add(key)
                occurrences.append(occ)
            continue
        body = adsk.fusion.BRepBody.cast(obj)
        if not body:
            raise RuntimeError(f'Unsupported Setup model type: {getattr(obj, "objectType", type(obj).__name__)}')
        ctx = body.assemblyContext
        if ctx:
            key = ctx.entityToken
            if key not in seen_occ:
                seen_occ.add(key)
                occurrences.append(ctx)
            continue
        if body.parentComponent == root:
            native = body.nativeObject or body
            key = native.entityToken
            if key not in seen_body:
                seen_body.add(key)
                root_bodies.append(native)
            continue
        occs = root.occurrencesByComponent(body.parentComponent)
        if occs.count == 1:
            ctx = occs.item(0)
            key = ctx.entityToken
            if key not in seen_occ:
                seen_occ.add(key)
                occurrences.append(ctx)
        else:
            raise RuntimeError(
                f'Setup model {body.name} belongs to component {body.parentComponent.name}, '
                'but Auto Vise cannot identify one unique root occurrence to move.'
            )
    return occurrences, root_bodies


def _move_setup_models(setup, design, delta, lines):
    if _matrix_near_identity(delta):
        lines.append('Setup model already matches the calibrated vise position; no part move required.')
        return
    occurrences, bodies = _setup_model_targets(setup, design)
    if not occurrences and not bodies:
        raise RuntimeError('The selected Setup has no movable model geometry.')
    if occurrences:
        transforms = []
        for occ in occurrences:
            transforms.append(_mat_mul(delta, occ.transform2))
        if not design.rootComponent.transformOccurrences(occurrences, transforms, True):
            raise RuntimeError('Fusion rejected moving the Setup component occurrence(s).')
        lines.append(f'Moved {len(occurrences)} Setup occurrence(s).')
    if bodies:
        coll = adsk.core.ObjectCollection.create()
        for body in bodies:
            coll.add(body)
        move_feats = design.rootComponent.features.moveFeatures
        move_input = move_feats.createInput2(coll)
        if not move_input.defineAsFreeMove(delta):
            raise RuntimeError('Fusion rejected the root-body move definition.')
        feat = move_feats.add(move_input)
        if design.designType == adsk.fusion.DesignTypes.ParametricDesignType and not feat:
            raise RuntimeError('Fusion failed to create the root-body Move feature.')
        try:
            if feat:
                feat.name = 'AUTO_VISE part position'
        except Exception:
            pass
        lines.append(f'Moved {len(bodies)} root Setup body/bodies with a Move feature.')
    try:
        design.computeAll()
    except Exception:
        pass
    adsk.doEvents()


def _target_part_transform(setup, stock, info, grip, axis, side):
    o, sx, sy, sz = setup.workCoordinateSystem.getAsCoordinateSystem()
    sx.normalize(); sy.normalize(); sz.normalize()
    inward = info['clamp'].copy()
    inward.normalize()
    up = adsk.core.Vector3D.create(0, 0, 1)
    if axis == 'Y':
        ty = inward.copy()
        if side == 'Y+':
            ty.scaleBy(-1)
        tz = up
        tx = ty.crossProduct(tz)
        tx.normalize()
        local_anchor = (
            (stock['min_x'] + stock['max_x']) / 2,
            stock['max_y'] if side == 'Y+' else stock['min_y'],
            stock['min_z'] + grip,
        )
    else:
        tx = inward.copy()
        if side == 'X+':
            tx.scaleBy(-1)
        tz = up
        ty = tz.crossProduct(tx)
        ty.normalize()
        local_anchor = (
            stock['max_x'] if side == 'X+' else stock['min_x'],
            (stock['min_y'] + stock['max_y']) / 2,
            stock['min_z'] + grip,
        )
    target_anchor = _pt(
        _origin(),
        _v(info['clamp'], info['fixed_inner']),
        _v(info['across'], info['across_center']),
        _v(up, info['jaw_top']),
    )
    target_origin = _pt(
        target_anchor,
        _v(tx, -local_anchor[0]),
        _v(ty, -local_anchor[1]),
        _v(tz, -local_anchor[2]),
    )
    delta = adsk.core.Matrix3D.create()
    if not delta.setToAlignCoordinateSystems(o, sx, sy, sz, target_origin, tx, ty, tz):
        raise RuntimeError('Failed to calculate the workpiece-to-vise transform.')
    return delta, target_anchor, target_origin


def _xyz(v):
    return f'({v.x:.6f}, {v.y:.6f}, {v.z:.6f})'


def _bb(bb):
    return f'min={_xyz(bb.minPoint)} max={_xyz(bb.maxPoint)}'


def _mat(m):
    try:
        return '[' + ', '.join(f'{x:.6f}' for x in m.asArray()) + ']'
    except Exception:
        return '<unavailable>'


def _debug_stock(design, setup, s):
    global _debug_groups
    for g in _debug_groups:
        try:
            g.deleteMe()
        except Exception:
            pass
    _debug_groups = []
    g = design.rootComponent.customGraphicsGroups.add()
    _debug_groups.append(g)
    o, x, y, z = setup.workCoordinateSystem.getAsCoordinateSystem()
    x.normalize(); y.normalize(); z.normalize()
    c = _pt(o, _v(x, (s['min_x'] + s['max_x']) / 2), _v(y, (s['min_y'] + s['max_y']) / 2), _v(z, (s['min_z'] + s['max_z']) / 2))
    box = adsk.core.OrientedBoundingBox3D.create(c, x, y, s['size_x'], s['size_y'], s['size_z'])
    b = g.addBRepBody(adsk.fusion.TemporaryBRepManager.get().createBox(box))
    try:
        b.setOpacity(.18, True)
    except Exception:
        pass


def _write(lines):
    path = os.path.join(HERE, 'last_debug.txt')
    text = '\n'.join(lines) + '\n'
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
    except Exception:
        path = '<write failed>'
    try:
        adsk.core.Application.get().log(text)
    except Exception:
        pass
    return path


def _log_info(lines, info, label):
    lines += [
        label,
        f'  fixed bbox: {_bb(info["fixed"].preciseBoundingBox)}',
        f'  moving bbox: {_bb(info["moving"].preciseBoundingBox)}',
        f'  clamp={_xyz(info["clamp"])} across={_xyz(info["across"])}',
        f'  fixed contact={info["fixed_inner"]:.6f} moving contact={info["moving_inner"]:.6f} gap={info["gap"] * 10:.3f} mm',
        f'  chosen contact body={info["contact_body"]} face={info["contact_face"]} {_bb(info["contact_bb"])}',
        f'  candidates={len(info["candidates"])}',
    ]
    for pos, area, bb, bi, fi in info['candidates'][:8]:
        lines.append(f'    body={bi} face={fi} pos={pos:.6f} area={area:.4f} {_bb(bb)}')


class Created(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        app, ui = _app_ui()
        try:
            cam = _cam(app.activeDocument)
            if not cam or not cam.setups.count:
                raise RuntimeError('Create a Manufacture Setup first.')
            i = args.command.commandInputs
            dd = i.addDropDownCommandInput('setup', 'Setup', adsk.core.DropDownStyles.TextListDropDownStyle)
            for n in range(cam.setups.count):
                dd.listItems.add(cam.setups.item(n).name, n == 0, '')
            ax = i.addDropDownCommandInput('axis', 'Clamp along setup axis', adsk.core.DropDownStyles.TextListDropDownStyle)
            ax.listItems.add('Y', True, '')
            ax.listItems.add('X', False, '')
            sd = i.addDropDownCommandInput('side', 'Stock side at fixed jaw', adsk.core.DropDownStyles.TextListDropDownStyle)
            for n in ('Y+', 'Y-', 'X+', 'X-'):
                sd.listItems.add(n, n == 'Y+', '')
            i.addValueInput('grip', 'Grip depth', 'mm', adsk.core.ValueInput.createByString('4 mm'))
            i.addBoolValueInput('fixture', 'Add vise to Setup fixtures', True, '', True)
            i.addBoolValueInput('choose', 'Choose/change vise master', True, '', False)
            i.addBoolValueInput('capture', 'Capture current vise as machine position', True, '', False)
            i.addBoolValueInput('debug', 'Show final stock + write log', True, '', True)
            st = _load_settings()
            master = st.get('vise_name', 'none yet')
            calibrated = 'yes' if st.get('machine_vise_transform') else 'NO'
            i.addTextBoxCommandInput(
                'info', '',
                f'Remembered vise: {master}\nMachine position calibrated: {calibrated}\n'
                'Normal mode keeps the vise fixed and moves the Setup model/stock into it.',
                4, True,
            )
            h = Execute()
            args.command.execute.add(h)
            _handlers.append(h)
        except Exception:
            ui.messageBox(traceback.format_exc(), APP_NAME)


class Execute(adsk.core.CommandEventHandler):
    def notify(self, args):
        app, ui = _app_ui()
        lines = []
        try:
            doc = app.activeDocument
            cam = _cam(doc)
            design = _design(doc)
            i = args.command.commandInputs
            name = i.itemById('setup').selectedItem.name
            setup = next((cam.setups.item(n) for n in range(cam.setups.count) if cam.setups.item(n).name == name), None)
            if not setup:
                raise RuntimeError(f'Setup not found: {name}')
            axis = i.itemById('axis').selectedItem.name
            side = i.itemById('side').selectedItem.name
            if not side.startswith(axis):
                raise RuntimeError(f'{side} does not match clamp axis {axis}.')
            lines = [
                f'Auto Vise debug {datetime.now().isoformat(timespec="seconds")}',
                f'Document: {doc.name}',
                f'Setup: {setup.name}',
                f'Clamp axis {axis}, stock fixed side {side}',
            ]
            df, remembered = _master(doc, i.itemById('choose').value)
            if not df:
                return
            lines.append(f'Vise master {df.name}, remembered={remembered}, id={df.id}')

            if i.itemById('capture').value:
                vise = _managed_vise(design.rootComponent)
                if not vise:
                    vise, _ = _ensure_vise(design, df, fresh=True)
                    path = _write(lines + ['Inserted vise at identity for machine-position calibration.'])
                    ui.messageBox(
                        'I inserted the vise at the design origin.\n\n'
                        'Move the AUTO_VISE occurrence to its real fixed position on the machine bed, '
                        'then run Auto Vise again with "Capture current vise as machine position" enabled.\n\n'
                        f'Debug log: {path}',
                        APP_NAME,
                    )
                    return
                _save_machine_transform(vise.transform2)
                lines.append(f'Captured machine vise transform {_mat(vise.transform2)}')
                path = _write(lines)
                ui.messageBox(
                    'Saved the current vise position as the fixed machine position.\n'
                    'Normal Auto Vise runs will keep the vise here and move the part/stock into it.\n\n'
                    f'Debug log: {path}',
                    APP_NAME,
                )
                return

            machine = _machine_transform()
            if not machine:
                vise = _managed_vise(design.rootComponent)
                if not vise:
                    vise, _ = _ensure_vise(design, df, fresh=True)
                path = _write(lines + ['No machine vise transform is calibrated.'])
                ui.messageBox(
                    'No fixed machine vise position is saved yet.\n\n'
                    'Move the AUTO_VISE occurrence to where the vise is physically mounted on the machine, '
                    'then run Auto Vise again with "Capture current vise as machine position" enabled.\n\n'
                    f'Debug log: {path}',
                    APP_NAME,
                )
                return

            vise, reused = _ensure_vise(design, df, fresh=True)
            lines.append(f'Fresh vise occurrence inserted; previous occurrence reused={reused}')
            vise.transform2 = machine.copy()
            lines.append(f'Fixed machine vise transform {_mat(machine)}')

            s = _stock(setup)
            o0, _, _, _ = setup.workCoordinateSystem.getAsCoordinateSystem()
            lines += [
                f'Before move stock X {s["min_x"]:.6f}..{s["max_x"]:.6f} Y {s["min_y"]:.6f}..{s["max_y"]:.6f} Z {s["min_z"]:.6f}..{s["max_z"]:.6f}',
                f'Before move stock size {s["size_x"] * 10:.3f} x {s["size_y"] * 10:.3f} x {s["size_z"] * 10:.3f} mm',
                f'Before move WCS origin {_xyz(o0)}',
                f'Before move WCS matrix {_mat(setup.workCoordinateSystem)}',
            ]
            info = _vise_info(vise)
            _log_info(lines, info, 'Fixed vise before jaw adjustment:')
            required = s['size_y'] if axis == 'Y' else s['size_x']
            delta_jaw = required - info['gap']
            lines.append(f'Required jaw gap {required * 10:.3f} mm; jaw delta {delta_jaw * 10:.3f} mm')
            if not _move_jaw(design, info['moving'], info['clamp'], delta_jaw):
                raise RuntimeError('Fusion rejected the movable-jaw transform.')
            lines.append(f'  moving bbox after adjustment: {_bb(info["moving"].preciseBoundingBox)}')

            grip = i.itemById('grip').value
            part_delta, target_anchor, target_origin = _target_part_transform(setup, s, info, grip, axis, side)
            lines += [
                f'Target jaw anchor {_xyz(target_anchor)}',
                f'Target Setup origin {_xyz(target_origin)}',
                f'Part delta transform {_mat(part_delta)}',
            ]
            _move_setup_models(setup, design, part_delta, lines)

            s2 = _stock(setup)
            o2, _, _, _ = setup.workCoordinateSystem.getAsCoordinateSystem()
            lines += [
                f'After move stock X {s2["min_x"]:.6f}..{s2["max_x"]:.6f} Y {s2["min_y"]:.6f}..{s2["max_y"]:.6f} Z {s2["min_z"]:.6f}..{s2["max_z"]:.6f}',
                f'After move WCS origin {_xyz(o2)}',
                f'After move WCS matrix {_mat(setup.workCoordinateSystem)}',
            ]
            if i.itemById('fixture').value:
                _fixture(setup, vise)
                lines.append('Fixture assigned')
            if i.itemById('debug').value:
                _debug_stock(design, setup, s2)
                lines.append('Final cyan stock debug geometry created')
            path = _write(lines)
            ui.messageBox(
                f'Auto Vise positioned the Setup model in the fixed machine vise for {setup.name}.\n'
                f'Jaw adjusted by {delta_jaw * 10:.3f} mm.\n'
                f'Debug log: {path}',
                APP_NAME,
            )
        except Exception as e:
            lines += ['', f'EXCEPTION: {e}', traceback.format_exc()]
            _write(lines)
            ui.messageBox(f'{e}\n\n{traceback.format_exc()}', APP_NAME)


def run(context):
    _, ui = _app_ui()
    try:
        ws = ui.workspaces.itemById('CAMEnvironment')
        cmd = ui.commandDefinitions.itemById(CMD_ID)
        if not cmd:
            cmd = ui.commandDefinitions.addButtonDefinition(
                CMD_ID, 'Auto Vise', 'Position CAM stock in a fixed machine-mounted vise.', RES
            )
        h = Created()
        cmd.commandCreated.add(h)
        _handlers.append(h)
        panel = ws.toolbarPanels.itemById(PANEL_ID) or ws.toolbarPanels.add(PANEL_ID, 'Auto Vise')
        if not panel.controls.itemById(CMD_ID):
            c = panel.controls.addCommand(cmd)
            c.isPromotedByDefault = True
            c.isPromoted = True
    except Exception:
        ui.messageBox(traceback.format_exc(), APP_NAME)


def stop(context):
    _, ui = _app_ui()
    try:
        ws = ui.workspaces.itemById('CAMEnvironment')
        panel = ws.toolbarPanels.itemById(PANEL_ID) if ws else None
        if panel:
            c = panel.controls.itemById(CMD_ID)
            if c:
                c.deleteMe()
            panel.deleteMe()
        cmd = ui.commandDefinitions.itemById(CMD_ID)
        if cmd:
            cmd.deleteMe()
        for g in _debug_groups:
            try:
                g.deleteMe()
            except Exception:
                pass
    except Exception:
        pass
