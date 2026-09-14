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


def _param(setup, name):
    p = setup.parameters.itemByName(name)
    if not p:
        raise RuntimeError(f'Missing CAM parameter: {name}')
    return p


def _p(setup, name):
    p = _param(setup, name)
    if not p.value:
        raise RuntimeError(f'CAM parameter has no value: {name}')
    return p.value.value


def _stock(setup):
    if setup.stockMode not in (
        adsk.cam.SetupStockModes.FixedBoxStock,
        adsk.cam.SetupStockModes.RelativeBoxStock,
    ):
        raise RuntimeError('Only Fixed Size Box and Relative Size Box stock are supported.')

    x0, x1 = float(_p(setup, 'stockXLow')), float(_p(setup, 'stockXHigh'))
    y0, y1 = float(_p(setup, 'stockYLow')), float(_p(setup, 'stockYHigh'))
    z0, z1 = float(_p(setup, 'stockZLow')), float(_p(setup, 'stockZHigh'))

    s = {
        'min_x': min(x0, x1), 'max_x': max(x0, x1),
        'min_y': min(y0, y1), 'max_y': max(y0, y1),
        'min_z': min(z0, z1), 'max_z': max(z0, z1),
    }
    s['size_x'] = s['max_x'] - s['min_x']
    s['size_y'] = s['max_y'] - s['min_y']
    s['size_z'] = s['max_z'] - s['min_z']
    return s


def _v(v, k):
    return adsk.core.Vector3D.create(v.x * k, v.y * k, v.z * k)


def _pt(p, *vectors):
    q = adsk.core.Point3D.create(p.x, p.y, p.z)
    for v in vectors:
        q.translateBy(v)
    return q


def _center(bb):
    return adsk.core.Point3D.create(
        (bb.minPoint.x + bb.maxPoint.x) / 2,
        (bb.minPoint.y + bb.maxPoint.y) / 2,
        (bb.minPoint.z + bb.maxPoint.z) / 2,
    )


def _children(occ):
    out = []
    for i in range(occ.childOccurrences.count):
        child = occ.childOccurrences.item(i)
        out.append(child)
        out.extend(_children(child))
    return out


def _named(occ, text):
    wanted = text.lower().strip()
    for child in _children(occ):
        for name in (child.name or '', child.component.name if child.component else ''):
            if name.lower().split(':')[0].strip() == wanted:
                return child
    return None


def _axis_range(bb, axis):
    if abs(axis.x) > .5:
        a, b = axis.x * bb.minPoint.x, axis.x * bb.maxPoint.x
    elif abs(axis.y) > .5:
        a, b = axis.y * bb.minPoint.y, axis.y * bb.maxPoint.y
    else:
        a, b = axis.z * bb.minPoint.z, axis.z * bb.maxPoint.z
    return min(a, b), max(a, b)


def _overlap(a, b, axis):
    a0, a1 = _axis_range(a, axis)
    b0, b1 = _axis_range(b, axis)
    return max(0.0, min(a1, b1) - max(a0, b0))


def _fixed_face(fixed, moving_bb, clamp, across):
    """Find the actual fixed gripping face. The reference fixed-jaw component also contains the vise base."""
    moving_inner, _ = _axis_range(moving_bb, clamp)
    across0, across1 = _axis_range(moving_bb, across)
    moving_width = max(across1 - across0, 1e-9)
    moving_height = max(moving_bb.maxPoint.z - moving_bb.minPoint.z, 1e-9)
    candidates = []

    for bi in range(fixed.bRepBodies.count):
        body = fixed.bRepBodies.item(bi)
        for fi in range(body.faces.count):
            face = body.faces.item(fi)
            try:
                bb = face.boundingBox
                c0, c1 = _axis_range(bb, clamp)
                if c1 - c0 > .001:
                    continue
                pos = (c0 + c1) / 2
                if pos >= moving_inner - .0001:
                    continue
                if _overlap(bb, moving_bb, across) < moving_width * .5:
                    continue
                z_overlap = max(
                    0,
                    min(bb.maxPoint.z, moving_bb.maxPoint.z) -
                    max(bb.minPoint.z, moving_bb.minPoint.z),
                )
                if z_overlap < moving_height * .35:
                    continue
                if bb.maxPoint.z < moving_bb.maxPoint.z - .75:
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

    root_bb = vise.preciseBoundingBox
    moving_bb = moving.preciseBoundingBox
    root_center = _center(root_bb)
    moving_center = _center(moving_bb)
    dx = moving_center.x - root_center.x
    dy = moving_center.y - root_center.y

    if abs(dx) >= abs(dy):
        clamp = adsk.core.Vector3D.create(1 if dx >= 0 else -1, 0, 0)
    else:
        clamp = adsk.core.Vector3D.create(0, 1 if dy >= 0 else -1, 0)

    up = adsk.core.Vector3D.create(0, 0, 1)
    across = clamp.crossProduct(up)
    across.normalize()

    chosen, candidates = _fixed_face(fixed, moving_bb, clamp, across)
    fixed_inner, _, contact_bb, body_index, face_index = chosen
    moving_inner, _ = _axis_range(moving_bb, clamp)
    across0, across1 = _axis_range(contact_bb, across)

    return {
        'fixed': fixed,
        'moving': moving,
        'clamp': clamp,
        'across': across,
        'fixed_inner': fixed_inner,
        'moving_inner': moving_inner,
        'gap': moving_inner - fixed_inner,
        'across_center': (across0 + across1) / 2,
        'jaw_top': contact_bb.maxPoint.z,
        'contact_bb': contact_bb,
        'contact_body': body_index,
        'contact_face': face_index,
        'candidates': candidates,
    }


def _place_vise(vise, info, setup, stock, grip, axis, side):
    """Place the vise around CAM stock in design/setup space. Machine placement is handled separately by Part Position."""
    origin, sx, sy, sz = setup.workCoordinateSystem.getAsCoordinateSystem()
    sx.normalize()
    sy.normalize()
    sz.normalize()

    src_x = info['across'].copy()
    src_y = info['clamp'].copy()
    src_z = adsk.core.Vector3D.create(0, 0, 1)

    if axis == 'Y':
        inward = sy.copy()
        stock_face = stock['max_y'] if side == 'Y+' else stock['min_y']
        if side == 'Y+':
            inward.scaleBy(-1)
        anchor = _pt(
            origin,
            _v(sx, (stock['min_x'] + stock['max_x']) / 2),
            _v(sy, stock_face),
            _v(sz, stock['min_z'] + grip),
        )
    else:
        inward = sx.copy()
        stock_face = stock['max_x'] if side == 'X+' else stock['min_x']
        if side == 'X+':
            inward.scaleBy(-1)
        anchor = _pt(
            origin,
            _v(sx, stock_face),
            _v(sy, (stock['min_y'] + stock['max_y']) / 2),
            _v(sz, stock['min_z'] + grip),
        )

    target_z = sz.copy()
    target_x = inward.crossProduct(target_z)
    target_x.normalize()

    destination_origin = _pt(
        anchor,
        _v(target_x, -info['across_center']),
        _v(inward, -info['fixed_inner']),
        _v(target_z, -info['jaw_top']),
    )

    transform = adsk.core.Matrix3D.create()
    if not transform.setToAlignCoordinateSystems(
        adsk.core.Point3D.create(0, 0, 0),
        src_x, src_y, src_z,
        destination_origin,
        target_x, inward, target_z,
    ):
        raise RuntimeError('Failed to calculate the vise-to-stock transform.')

    vise.transform2 = transform

    world_clamp = info['clamp'].copy()
    world_clamp.transformBy(transform)
    world_clamp.normalize()
    return transform, anchor, world_clamp


def _move_jaw(design, moving, world_clamp, delta):
    m = moving.transform2.copy()
    t = m.translation
    t.x += world_clamp.x * delta
    t.y += world_clamp.y * delta
    t.z += world_clamp.z * delta
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
    settings = _load_settings()
    settings['vise_data_file_id'] = df.id
    settings['vise_name'] = df.name
    _save_settings(settings)


def _master(doc, choose=False):
    app, ui = _app_ui()
    settings = _load_settings()

    if not choose and settings.get('vise_data_file_id'):
        try:
            df = app.data.findFileById(settings['vise_data_file_id'])
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
        occ = root.occurrences.item(i)
        attr = occ.attributes.itemByName(ATTR, 'managed')
        if attr and attr.value == '1':
            return occ
    return None


def _insert_fresh_vise(design, df):
    root = design.rootComponent
    old = _managed_vise(root)
    if old:
        try:
            old.deleteMe()
        except Exception:
            pass

    occ = root.occurrences.addByInsert(df, adsk.core.Matrix3D.create(), True)
    if not occ:
        raise RuntimeError('Failed to insert linked vise.')

    try:
        occ.name = 'AUTO_VISE: ' + df.name
    except Exception:
        pass

    occ.attributes.add(ATTR, 'managed', '1')
    occ.attributes.add(ATTR, 'data_file_id', df.id)
    return occ


def _fixture(setup, vise):
    setup.fixtureEnabled = True
    items = adsk.core.ObjectCollection.create()

    try:
        old = setup.fixtures
        for i in range(old.count):
            item = old.item(i)
            occ = adsk.fusion.Occurrence.cast(item)
            if occ:
                attr = occ.attributes.itemByName(ATTR, 'managed')
                if attr and attr.value == '1':
                    continue
            items.add(item)
    except Exception:
        pass

    items.add(vise)
    setup.fixtures = items


PART_POS_PARAMS = (
    'job_positionXOffset',
    'job_positionYOffset',
    'job_positionZOffset',
)


def _part_position_values_mm(setup):
    """Return current Fusion Part Position XYZ in mm, or None if this Setup does not expose them."""
    values = []
    for name in PART_POS_PARAMS:
        p = setup.parameters.itemByName(name)
        if not p or not p.value:
            return None
        try:
            values.append(float(p.value.value) * 10.0)
        except Exception:
            return None
    return tuple(values)


def _set_part_position(setup, xyz_mm):
    """Write Fusion's native Setup > Part Position XYZ offsets."""
    for name, mm in zip(PART_POS_PARAMS, xyz_mm):
        p = setup.parameters.itemByName(name)
        if not p:
            raise RuntimeError(
                f'This Setup does not expose {name}. Assign a machine to the Setup and make sure '
                'Part Position is available before using machine placement.'
            )
        try:
            p.expression = f'{mm:.6f} mm'
        except Exception:
            try:
                p.value.value = mm / 10.0
            except Exception as exc:
                raise RuntimeError(f'Could not set {name}: {exc}')


def _saved_machine_position_mm():
    value = _load_settings().get('machine_part_position_mm')
    if isinstance(value, list) and len(value) == 3:
        try:
            return tuple(float(x) for x in value)
        except Exception:
            pass
    return None


def _save_machine_position_mm(xyz):
    settings = _load_settings()
    settings['machine_part_position_mm'] = [float(x) for x in xyz]
    _save_settings(settings)


def _xyz(v):
    return f'({v.x:.6f}, {v.y:.6f}, {v.z:.6f})'


def _bb(bb):
    return f'min={_xyz(bb.minPoint)} max={_xyz(bb.maxPoint)}'


def _mat(m):
    try:
        return '[' + ', '.join(f'{x:.6f}' for x in m.asArray()) + ']'
    except Exception:
        return '<unavailable>'


def _clear_debug():
    global _debug_groups
    for group in _debug_groups:
        try:
            group.deleteMe()
        except Exception:
            pass
    _debug_groups = []


def _debug_stock(design, setup, stock):
    _clear_debug()
    group = design.rootComponent.customGraphicsGroups.add()
    _debug_groups.append(group)

    origin, x, y, z = setup.workCoordinateSystem.getAsCoordinateSystem()
    x.normalize()
    y.normalize()
    z.normalize()

    center = _pt(
        origin,
        _v(x, (stock['min_x'] + stock['max_x']) / 2),
        _v(y, (stock['min_y'] + stock['max_y']) / 2),
        _v(z, (stock['min_z'] + stock['max_z']) / 2),
    )
    box = adsk.core.OrientedBoundingBox3D.create(
        center, x, y, stock['size_x'], stock['size_y'], stock['size_z']
    )
    body = group.addBRepBody(adsk.fusion.TemporaryBRepManager.get().createBox(box))
    try:
        body.setOpacity(.18, True)
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
        f'  fixed contact={info["fixed_inner"]:.6f} moving contact={info["moving_inner"]:.6f} '
        f'gap={info["gap"] * 10:.3f} mm',
        f'  chosen contact body={info["contact_body"]} face={info["contact_face"]} '
        f'{_bb(info["contact_bb"])}',
        f'  candidates={len(info["candidates"])}',
    ]
    for pos, area, bb, bi, fi in info['candidates'][:8]:
        lines.append(f'    body={bi} face={fi} pos={pos:.6f} area={area:.4f} {_bb(bb)}')


def _setup_by_name(cam, name):
    for i in range(cam.setups.count):
        setup = cam.setups.item(i)
        if setup.name == name:
            return setup
    return None


def _initial_machine_position_mm(setup):
    saved = _saved_machine_position_mm()
    if saved is not None:
        return saved, 'saved Auto Vise position'

    current = _part_position_values_mm(setup)
    if current is not None:
        return current, 'current Setup Part Position'

    return (0.0, 0.0, 0.0), 'default 0 / 0 / 0'


class Created(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        app, ui = _app_ui()
        try:
            cam = _cam(app.activeDocument)
            if not cam or not cam.setups.count:
                raise RuntimeError('Create a Manufacture Setup first.')

            inputs = args.command.commandInputs

            dd = inputs.addDropDownCommandInput(
                'setup', 'Setup', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            for n in range(cam.setups.count):
                dd.listItems.add(cam.setups.item(n).name, n == 0, '')

            first_setup = cam.setups.item(0)

            axis = inputs.addDropDownCommandInput(
                'axis', 'Clamp along setup axis', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            axis.listItems.add('Y', True, '')
            axis.listItems.add('X', False, '')

            side = inputs.addDropDownCommandInput(
                'side', 'Stock side at fixed jaw', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            for value in ('Y+', 'Y-', 'X+', 'X-'):
                side.listItems.add(value, value == 'Y+', '')

            inputs.addValueInput(
                'grip', 'Grip depth', 'mm', adsk.core.ValueInput.createByString('4 mm')
            )

            xyz, source = _initial_machine_position_mm(first_setup)
            inputs.addValueInput(
                'machine_x', 'Machine Part Position X', 'mm',
                adsk.core.ValueInput.createByString(f'{xyz[0]:.6f} mm')
            )
            inputs.addValueInput(
                'machine_y', 'Machine Part Position Y', 'mm',
                adsk.core.ValueInput.createByString(f'{xyz[1]:.6f} mm')
            )
            inputs.addValueInput(
                'machine_z', 'Machine Part Position Z', 'mm',
                adsk.core.ValueInput.createByString(f'{xyz[2]:.6f} mm')
            )

            inputs.addBoolValueInput('remember_xyz', 'Remember machine XYZ', True, '', True)
            inputs.addBoolValueInput('fixture', 'Add vise to Setup fixtures', True, '', True)
            inputs.addBoolValueInput('choose', 'Choose/change vise master', True, '', False)
            inputs.addBoolValueInput('debug', 'Show local stock + write log', True, '', True)

            settings = _load_settings()
            master = settings.get('vise_name', 'none yet')
            part_position_available = _part_position_values_mm(first_setup) is not None
            inputs.addTextBoxCommandInput(
                'info', '',
                f'Remembered vise: {master}\n'
                f'Machine XYZ source: {source}\n'
                f'Fusion Part Position available: {"yes" if part_position_available else "NO"}\n'
                'The vise is fitted around the stock locally. Fusion Part Position moves the complete '
                'part + fixture assembly relative to the machine model.',
                5, True,
            )

            handler = Execute()
            args.command.execute.add(handler)
            _handlers.append(handler)
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
            inputs = args.command.commandInputs

            setup_name = inputs.itemById('setup').selectedItem.name
            setup = _setup_by_name(cam, setup_name)
            if not setup:
                raise RuntimeError(f'Setup not found: {setup_name}')

            axis = inputs.itemById('axis').selectedItem.name
            side = inputs.itemById('side').selectedItem.name
            if not side.startswith(axis):
                raise RuntimeError(f'{side} does not match clamp axis {axis}.')

            stock = _stock(setup)
            wcs_origin, _, _, _ = setup.workCoordinateSystem.getAsCoordinateSystem()

            machine_xyz_mm = (
                inputs.itemById('machine_x').value * 10.0,
                inputs.itemById('machine_y').value * 10.0,
                inputs.itemById('machine_z').value * 10.0,
            )

            lines = [
                f'Auto Vise debug {datetime.now().isoformat(timespec="seconds")}',
                f'Document: {doc.name}',
                f'Setup: {setup.name}',
                f'Clamp axis {axis}, stock fixed side {side}',
                f'Stock size {stock["size_x"] * 10:.3f} x {stock["size_y"] * 10:.3f} '
                f'x {stock["size_z"] * 10:.3f} mm',
                f'Setup WCS origin {_xyz(wcs_origin)}',
                f'Setup WCS matrix {_mat(setup.workCoordinateSystem)}',
                f'Requested Fusion Part Position XYZ: '
                f'{machine_xyz_mm[0]:.3f}, {machine_xyz_mm[1]:.3f}, {machine_xyz_mm[2]:.3f} mm',
            ]

            current_part_position = _part_position_values_mm(setup)
            lines.append(
                'Part Position before: ' +
                (f'{current_part_position[0]:.3f}, {current_part_position[1]:.3f}, '
                 f'{current_part_position[2]:.3f} mm'
                 if current_part_position is not None else '<not exposed by Setup>')
            )

            df, remembered = _master(doc, inputs.itemById('choose').value)
            if not df:
                return
            lines.append(f'Vise master {df.name}, remembered={remembered}, id={df.id}')

            vise = _insert_fresh_vise(design, df)
            info = _vise_info(vise)
            _log_info(lines, info, 'Master vise:')

            grip = inputs.itemById('grip').value
            placement, anchor, world_clamp = _place_vise(
                vise, info, setup, stock, grip, axis, side
            )
            lines += [
                f'Local fixed-jaw anchor {_xyz(anchor)}',
                f'Local vise placement {_mat(placement)}',
                f'World clamp {_xyz(world_clamp)}',
            ]

            required_gap = stock['size_y'] if axis == 'Y' else stock['size_x']
            jaw_delta = required_gap - info['gap']
            lines.append(
                f'Required jaw gap {required_gap * 10:.3f} mm; '
                f'jaw delta {jaw_delta * 10:.3f} mm'
            )

            if not _move_jaw(design, info['moving'], world_clamp, jaw_delta):
                raise RuntimeError('Fusion rejected the movable-jaw transform.')
            lines.append(f'Moving jaw after adjustment: {_bb(info["moving"].preciseBoundingBox)}')

            if inputs.itemById('fixture').value:
                _fixture(setup, vise)
                lines.append('Fixture assigned')

            _set_part_position(setup, machine_xyz_mm)
            adsk.doEvents()
            applied = _part_position_values_mm(setup)
            lines.append(
                'Part Position after: ' +
                (f'{applied[0]:.3f}, {applied[1]:.3f}, {applied[2]:.3f} mm'
                 if applied is not None else '<could not read back>')
            )

            if inputs.itemById('remember_xyz').value:
                _save_machine_position_mm(machine_xyz_mm)
                lines.append('Machine Part Position XYZ saved in AutoVise/settings.json')

            if inputs.itemById('debug').value:
                _debug_stock(design, setup, stock)
                lines.append('Local cyan stock debug geometry created')

            path = _write(lines)
            ui.messageBox(
                f'Auto Vise updated {setup.name}.\n'
                f'Jaw adjusted by {jaw_delta * 10:.3f} mm.\n'
                f'Part Position: X {machine_xyz_mm[0]:.3f}, '
                f'Y {machine_xyz_mm[1]:.3f}, Z {machine_xyz_mm[2]:.3f} mm.\n\n'
                'The design geometry stays in local setup space. Fusion Part Position places '
                'the part + fixture assembly in the machine model.\n\n'
                f'Debug log: {path}',
                APP_NAME,
            )

        except Exception as exc:
            lines += ['', f'EXCEPTION: {exc}', traceback.format_exc()]
            _write(lines)
            ui.messageBox(f'{exc}\n\n{traceback.format_exc()}', APP_NAME)


def run(context):
    _, ui = _app_ui()
    try:
        workspace = ui.workspaces.itemById('CAMEnvironment')
        command = ui.commandDefinitions.itemById(CMD_ID)
        if not command:
            command = ui.commandDefinitions.addButtonDefinition(
                CMD_ID,
                'Auto Vise',
                'Fit a linked vise to CAM stock and set native Fusion Part Position.',
                RES,
            )

        handler = Created()
        command.commandCreated.add(handler)
        _handlers.append(handler)

        panel = workspace.toolbarPanels.itemById(PANEL_ID)
        if not panel:
            panel = workspace.toolbarPanels.add(PANEL_ID, 'Auto Vise')

        if not panel.controls.itemById(CMD_ID):
            control = panel.controls.addCommand(command)
            control.isPromotedByDefault = True
            control.isPromoted = True

    except Exception:
        ui.messageBox(traceback.format_exc(), APP_NAME)


def stop(context):
    _, ui = _app_ui()
    try:
        workspace = ui.workspaces.itemById('CAMEnvironment')
        panel = workspace.toolbarPanels.itemById(PANEL_ID) if workspace else None
        if panel:
            control = panel.controls.itemById(CMD_ID)
            if control:
                control.deleteMe()
            panel.deleteMe()

        command = ui.commandDefinitions.itemById(CMD_ID)
        if command:
            command.deleteMe()

        _clear_debug()

    except Exception:
        pass
