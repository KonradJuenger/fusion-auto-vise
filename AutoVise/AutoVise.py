import adsk.core
import adsk.fusion
import adsk.cam
import os
import traceback
from datetime import datetime

APP_NAME = 'Auto Vise'
CMD_ID = 'JK_AutoVise_Command'
CMD_NAME = 'Auto Vise'
CMD_DESCRIPTION = 'Place a linked vise from evaluated CAM setup stock.'
PANEL_ID = 'JK_AutoVise_Panel'
ATTRIBUTE_GROUP = 'JK_AutoVise'
RESOURCE_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Resources', 'AutoVise')

_handlers = []
_debug_groups = []


def _app_ui():
    app = adsk.core.Application.get()
    return app, app.userInterface if app else None


def _cam(doc):
    return adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType'))


def _design(doc):
    result = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    if not result:
        result = adsk.fusion.Design.cast(doc.products.itemByProductType('WorkingModelProductType'))
    return result


def _parameter(setup, name):
    p = setup.parameters.itemByName(name)
    if not p or not p.value:
        raise RuntimeError(f'Missing CAM parameter: {name}')
    return p


def _float(setup, name):
    return float(_parameter(setup, name).value.value)


def _choice(setup, name):
    return str(_parameter(setup, name).value.value).strip().strip("'")


def _stock_box(setup):
    """Read Fusion's evaluated box-stock bounds, in setup-WCS coordinates, cm."""
    if setup.stockMode not in (
        adsk.cam.SetupStockModes.FixedBoxStock,
        adsk.cam.SetupStockModes.RelativeBoxStock,
    ):
        raise RuntimeError('Auto Vise currently supports Fixed Size Box and Relative Size Box stock only.')

    try:
        x0, x1 = _float(setup, 'stockXLow'), _float(setup, 'stockXHigh')
        y0, y1 = _float(setup, 'stockYLow'), _float(setup, 'stockYHigh')
        z0, z1 = _float(setup, 'stockZLow'), _float(setup, 'stockZHigh')
    except Exception as exc:
        raise RuntimeError(
            'Fusion did not expose the evaluated stockX/Y/Z bounds for this Setup. '
            'These values are required so Auto Vise does not guess stock placement. '
            f'Details: {exc}'
        )

    result = {
        'min_x': min(x0, x1), 'max_x': max(x0, x1),
        'min_y': min(y0, y1), 'max_y': max(y0, y1),
        'min_z': min(z0, z1), 'max_z': max(z0, z1),
        'source': 'Fusion evaluated stockX/Y/Z bounds',
    }
    result['size_x'] = result['max_x'] - result['min_x']
    result['size_y'] = result['max_y'] - result['min_y']
    result['size_z'] = result['max_z'] - result['min_z']
    try:
        result['box_point'] = _choice(setup, 'wcs_origin_boxPoint')
    except Exception:
        result['box_point'] = ''
    return result


def _vec(v, scale):
    return adsk.core.Vector3D.create(v.x * scale, v.y * scale, v.z * scale)


def _point(p, *vectors):
    x, y, z = p.x, p.y, p.z
    for v in vectors:
        x += v.x
        y += v.y
        z += v.z
    return adsk.core.Point3D.create(x, y, z)


def _center(bb):
    return adsk.core.Point3D.create(
        (bb.minPoint.x + bb.maxPoint.x) / 2,
        (bb.minPoint.y + bb.maxPoint.y) / 2,
        (bb.minPoint.z + bb.maxPoint.z) / 2,
    )


def _children(parent):
    out = []
    children = parent.childOccurrences
    for i in range(children.count):
        child = children.item(i)
        out.append(child)
        out.extend(_children(child))
    return out


def _child_named(parent, wanted):
    wanted = wanted.lower().strip()
    partial = None
    for occ in _children(parent):
        for name in (occ.name or '', occ.component.name if occ.component else ''):
            low = name.lower().strip()
            if low == wanted or low.startswith(wanted + ':'):
                return occ
            if wanted in low and partial is None:
                partial = occ
    return partial


def _axis_range(bb, axis):
    # The master vise is expected to be axis aligned before insertion.
    if abs(axis.x) > 0.5:
        vals = (axis.x * bb.minPoint.x, axis.x * bb.maxPoint.x)
    elif abs(axis.y) > 0.5:
        vals = (axis.y * bb.minPoint.y, axis.y * bb.maxPoint.y)
    else:
        vals = (axis.z * bb.minPoint.z, axis.z * bb.maxPoint.z)
    return min(vals), max(vals)


def _vise_info(vise):
    fixed = _child_named(vise, 'fixed jaw')
    moving = _child_named(vise, 'movable jaw')
    if not fixed or not moving:
        names = ', '.join(o.name for o in _children(vise))
        raise RuntimeError(
            'Vise master needs child components named "fixed jaw" and "movable jaw". '
            f'Found: {names}'
        )

    fbb, mbb = fixed.preciseBoundingBox, moving.preciseBoundingBox
    fc, mc = _center(fbb), _center(mbb)
    dx, dy = mc.x - fc.x, mc.y - fc.y
    if abs(dx) >= abs(dy):
        clamp = adsk.core.Vector3D.create(1 if dx >= 0 else -1, 0, 0)
    else:
        clamp = adsk.core.Vector3D.create(0, 1 if dy >= 0 else -1, 0)

    up = adsk.core.Vector3D.create(0, 0, 1)
    across = clamp.crossProduct(up)
    across.normalize()

    _, fixed_inner = _axis_range(fbb, clamp)
    moving_inner, _ = _axis_range(mbb, clamp)
    across_min, across_max = _axis_range(fbb, across)
    return {
        'fixed': fixed,
        'moving': moving,
        'clamp': clamp,
        'across': across,
        'fixed_inner': fixed_inner,
        'moving_inner': moving_inner,
        'gap': moving_inner - fixed_inner,
        'across_center': (across_min + across_max) / 2,
        'jaw_top': max(fbb.maxPoint.z, mbb.maxPoint.z),
    }


def _place_root(vise, info, setup, stock, grip_depth, clamp_axis, fixed_side):
    origin, sx, sy, sz = setup.workCoordinateSystem.getAsCoordinateSystem()
    sx.normalize(); sy.normalize(); sz.normalize()

    src_x = info['across'].copy()
    src_y = info['clamp'].copy()
    src_z = adsk.core.Vector3D.create(0, 0, 1)

    if clamp_axis == 'Y':
        positive = fixed_side == 'Y-'
        target_y = sy.copy()
        if not positive:
            target_y.scaleBy(-1)
        stock_face = stock['min_y'] if positive else stock['max_y']
        anchor = _point(
            origin,
            _vec(sx, (stock['min_x'] + stock['max_x']) / 2),
            _vec(sy, stock_face),
            _vec(sz, stock['min_z'] + grip_depth),
        )
    else:
        positive = fixed_side == 'X-'
        target_y = sx.copy()
        if not positive:
            target_y.scaleBy(-1)
        stock_face = stock['min_x'] if positive else stock['max_x']
        anchor = _point(
            origin,
            _vec(sx, stock_face),
            _vec(sy, (stock['min_y'] + stock['max_y']) / 2),
            _vec(sz, stock['min_z'] + grip_depth),
        )

    target_z = sz.copy()
    target_x = target_y.crossProduct(target_z)
    target_x.normalize()

    destination_origin = _point(
        anchor,
        _vec(target_x, -info['across_center']),
        _vec(target_y, -info['fixed_inner']),
        _vec(target_z, -info['jaw_top']),
    )
    m = adsk.core.Matrix3D.create()
    if not m.setToAlignCoordinateSystems(
        adsk.core.Point3D.create(0, 0, 0), src_x, src_y, src_z,
        destination_origin, target_x, target_y, target_z,
    ):
        raise RuntimeError('Failed to calculate vise root transform.')
    vise.transform2 = m
    return m, anchor


def _move_jaw_after_root(design, info, required_gap):
    """Move jaw in final assembly context, after the linked root has been placed."""
    delta = required_gap - info['gap']
    if abs(delta) < 1e-7:
        return True, 'Jaw already matches stock.', delta

    moving, clamp = info['moving'], info['clamp']
    m = moving.transform2.copy()
    t = m.translation
    t.x += clamp.x * delta
    t.y += clamp.y * delta
    t.z += clamp.z * delta
    m.translation = t

    errors = []
    try:
        if design.rootComponent.transformOccurrences([moving], [m], True):
            return True, f'Moved jaw by {delta * 10:.3f} mm.', delta
        errors.append('root.transformOccurrences returned False')
    except Exception as exc:
        errors.append(f'transformOccurrences: {exc}')

    try:
        moving.transform2 = m
        return True, f'Moved jaw by {delta * 10:.3f} mm using transform2 fallback.', delta
    except Exception as exc:
        errors.append(f'transform2: {exc}')
    return False, 'Could not move movable jaw. ' + ' | '.join(errors), delta


def _choose_vise_file(doc):
    _, ui = _app_ui()
    dlg = ui.createCloudFileDialog()
    dlg.title = 'Select vise master Fusion file'
    dlg.isMultiSelectEnabled = False
    dlg.filter = '*'
    try:
        if doc.dataFile:
            dlg.dataFolder = doc.dataFile.parentFolder
    except Exception:
        pass
    if dlg.showOpen() != adsk.core.DialogResults.DialogOK or not dlg.dataFile:
        return None

    data_file = dlg.dataFile
    if not doc.dataFile:
        raise RuntimeError('Save the machining document to Fusion cloud first.')
    if data_file.parentProject.id != doc.dataFile.parentProject.id:
        raise RuntimeError('The machining file and linked vise master must be in the same Fusion project.')
    return data_file


def _managed_vise(root):
    for i in range(root.occurrences.count):
        occ = root.occurrences.item(i)
        attr = occ.attributes.itemByName(ATTRIBUTE_GROUP, 'managed')
        if attr and attr.value == '1':
            return occ
    return None


def _insert_vise(design, data_file):
    root = design.rootComponent
    old = _managed_vise(root)
    if old:
        try:
            old.deleteMe()
        except Exception:
            pass
    occ = root.occurrences.addByInsert(data_file, adsk.core.Matrix3D.create(), True)
    if not occ:
        raise RuntimeError('Fusion failed to insert the vise as a linked component.')
    try:
        occ.name = f'AUTO_VISE: {data_file.name}'
    except Exception:
        pass
    occ.attributes.add(ATTRIBUTE_GROUP, 'managed', '1')
    return occ


def _fixture(setup, vise):
    setup.fixtureEnabled = True
    items = adsk.core.ObjectCollection.create()
    try:
        old = setup.fixtures
        for i in range(old.count):
            if old.item(i) != vise:
                items.add(old.item(i))
    except Exception:
        pass
    items.add(vise)
    setup.fixtures = items


def _fmt_xyz(v):
    return f'({v.x:.6f}, {v.y:.6f}, {v.z:.6f})'


def _fmt_bb(bb):
    return f'min={_fmt_xyz(bb.minPoint)} max={_fmt_xyz(bb.maxPoint)}' if bb else '<none>'


def _fmt_matrix(m):
    try:
        return '[' + ', '.join(f'{x:.6f}' for x in m.asArray()) + ']'
    except Exception:
        return '<unavailable>'


def _debug_vise(lines, label, info):
    lines += [
        label,
        f'  fixed: {info["fixed"].name} | {_fmt_bb(info["fixed"].preciseBoundingBox)}',
        f'  fixed transform2: {_fmt_matrix(info["fixed"].transform2)}',
        f'  moving: {info["moving"].name} | {_fmt_bb(info["moving"].preciseBoundingBox)}',
        f'  moving transform2: {_fmt_matrix(info["moving"].transform2)}',
        f'  clamp={_fmt_xyz(info["clamp"])} across={_fmt_xyz(info["across"])}',
        f'  fixed_inner={info["fixed_inner"]:.6f} moving_inner={info["moving_inner"]:.6f}',
        f'  gap={info["gap"]:.6f} cm ({info["gap"] * 10:.3f} mm)',
        f'  across_center={info["across_center"]:.6f} jaw_top={info["jaw_top"]:.6f}',
    ]


def _debug_models(lines, setup):
    lines.append('Setup models:')
    try:
        models = setup.models
        for i in range(models.count):
            obj = models.item(i)
            name = getattr(obj, 'name', getattr(obj, 'objectType', '<unknown>'))
            bb = None
            for prop in ('preciseBoundingBox', 'boundingBox'):
                try:
                    bb = getattr(obj, prop)
                    break
                except Exception:
                    pass
            lines.append(f'  [{i}] {name}: {_fmt_bb(bb)}')
    except Exception as exc:
        lines.append(f'  <failed: {exc}>')


def _clear_debug():
    global _debug_groups
    for group in _debug_groups:
        try:
            group.deleteMe()
        except Exception:
            pass
    _debug_groups = []


def _draw_debug_stock(design, setup, stock):
    """Cyan translucent box is exactly the stock geometry interpreted by Auto Vise."""
    _clear_debug()
    root = design.rootComponent
    group = root.customGraphicsGroups.add()
    _debug_groups.append(group)

    origin, x, y, z = setup.workCoordinateSystem.getAsCoordinateSystem()
    x.normalize(); y.normalize(); z.normalize()
    center = _point(
        origin,
        _vec(x, (stock['min_x'] + stock['max_x']) / 2),
        _vec(y, (stock['min_y'] + stock['max_y']) / 2),
        _vec(z, (stock['min_z'] + stock['max_z']) / 2),
    )
    obb = adsk.core.OrientedBoundingBox3D.create(
        center, x, y, stock['size_x'], stock['size_y'], stock['size_z']
    )
    temp = adsk.fusion.TemporaryBRepManager.get().createBox(obb)
    body = group.addBRepBody(temp)
    if body:
        body.name = 'AUTO VISE DEBUG STOCK'
        try:
            color = adsk.core.Color.create(0, 190, 255, 255)
            body.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(color)
            body.setOpacity(0.18, True)
        except Exception:
            pass

    length = max(stock['size_x'], stock['size_y'], stock['size_z'], 2.0) * 0.45
    for axis, rgb, name in (
        (x, (255, 60, 60), 'WCS X'),
        (y, (60, 220, 90), 'WCS Y'),
        (z, (60, 120, 255), 'WCS Z'),
    ):
        line = group.addCurve(adsk.core.Line3D.create(origin, _point(origin, _vec(axis, length))))
        if line:
            line.name = name
            line.weight = 3
            try:
                line.color = adsk.fusion.CustomGraphicsSolidColorEffect.create(
                    adsk.core.Color.create(*rgb, 255)
                )
            except Exception:
                pass


def _write_debug(lines):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'last_debug.txt')
    text = '\n'.join(lines) + '\n'
    try:
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
    except Exception:
        path = '<could not write last_debug.txt>'
    app, _ = _app_ui()
    try:
        app.log('\n' + text)
    except Exception:
        pass
    return path


class _CommandCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        app, ui = _app_ui()
        try:
            inputs = args.command.commandInputs
            cam = _cam(app.activeDocument)
            if not cam or not cam.setups.count:
                ui.messageBox('Create a Manufacture Setup first.', APP_NAME)
                return

            setup_input = inputs.addDropDownCommandInput(
                'setup', 'Setup', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            for i in range(cam.setups.count):
                setup_input.listItems.add(cam.setups.item(i).name, i == 0, '')

            axis = inputs.addDropDownCommandInput(
                'clamp_axis', 'Clamp along setup axis', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            axis.listItems.add('Y', True, '')
            axis.listItems.add('X', False, '')

            side = inputs.addDropDownCommandInput(
                'fixed_side', 'Fixed jaw side', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            for name in ('Y-', 'Y+', 'X-', 'X+'):
                side.listItems.add(name, name == 'Y-', '')

            inputs.addValueInput('grip_depth', 'Grip depth', 'mm', adsk.core.ValueInput.createByString('4 mm'))
            inputs.addBoolValueInput('add_fixture', 'Add vise to Setup fixtures', True, '', True)
            inputs.addBoolValueInput('debug', 'Show debug stock + write log', True, '', True)
            inputs.addTextBoxCommandInput(
                'info', '',
                'Debug mode draws a cyan box for the exact CAM stock bounds Auto Vise reads. '
                'The vise master must contain "fixed jaw" and "movable jaw" components.',
                4, True
            )

            handler = _Execute()
            args.command.execute.add(handler)
            _handlers.append(handler)
        except Exception:
            ui.messageBox('Command creation failed:\n' + traceback.format_exc(), APP_NAME)


class _Execute(adsk.core.CommandEventHandler):
    def notify(self, args):
        app, ui = _app_ui()
        debug = []
        try:
            doc = app.activeDocument
            cam, design = _cam(doc), _design(doc)
            if not cam or not design:
                raise RuntimeError('The active document must contain Design and Manufacture data.')

            inputs = args.command.commandInputs
            setup_name = inputs.itemById('setup').selectedItem.name
            setup = next((cam.setups.item(i) for i in range(cam.setups.count) if cam.setups.item(i).name == setup_name), None)
            if not setup:
                raise RuntimeError(f'Setup not found: {setup_name}')

            clamp_axis = inputs.itemById('clamp_axis').selectedItem.name
            fixed_side = inputs.itemById('fixed_side').selectedItem.name
            if not fixed_side.startswith(clamp_axis):
                raise RuntimeError(f'{fixed_side} is incompatible with clamp axis {clamp_axis}.')
            grip_depth = inputs.itemById('grip_depth').value
            add_fixture = inputs.itemById('add_fixture').value
            show_debug = inputs.itemById('debug').value

            stock = _stock_box(setup)
            origin, wx, wy, wz = setup.workCoordinateSystem.getAsCoordinateSystem()
            debug = [
                f'Auto Vise debug {datetime.now().isoformat(timespec="seconds")}',
                f'Document: {doc.name}',
                f'Setup: {setup.name}',
                f'Stock source: {stock["source"]}',
                f'Stock X: {stock["min_x"]:.6f} .. {stock["max_x"]:.6f} cm',
                f'Stock Y: {stock["min_y"]:.6f} .. {stock["max_y"]:.6f} cm',
                f'Stock Z: {stock["min_z"]:.6f} .. {stock["max_z"]:.6f} cm',
                f'Stock size: {stock["size_x"]*10:.3f} x {stock["size_y"]*10:.3f} x {stock["size_z"]*10:.3f} mm',
                f'WCS box point: {stock.get("box_point", "")}',
                f'WCS origin: {_fmt_xyz(origin)}',
                f'WCS X: {_fmt_xyz(wx)}', f'WCS Y: {_fmt_xyz(wy)}', f'WCS Z: {_fmt_xyz(wz)}',
                f'WCS matrix: {_fmt_matrix(setup.workCoordinateSystem)}',
            ]
            _debug_models(debug, setup)

            if show_debug:
                try:
                    _draw_debug_stock(design, setup, stock)
                    debug.append('Debug graphics: cyan stock box + WCS axes created')
                except Exception as exc:
                    debug.append(f'Debug graphics failed: {exc}')

            data_file = _choose_vise_file(doc)
            if not data_file:
                return
            vise = _insert_vise(design, data_file)
            info = _vise_info(vise)
            debug.append(f'Vise root initial bbox: {_fmt_bb(vise.preciseBoundingBox)}')
            debug.append(f'Vise root initial transform2: {_fmt_matrix(vise.transform2)}')
            _debug_vise(debug, 'Vise before root placement:', info)

            placement, anchor = _place_root(vise, info, setup, stock, grip_depth, clamp_axis, fixed_side)
            debug.append(f'Placement anchor: {_fmt_xyz(anchor)}')
            debug.append(f'Vise root placed transform2: {_fmt_matrix(vise.transform2)}')
            debug.append(f'Placement matrix: {_fmt_matrix(placement)}')

            # Re-read proxies only after the linked root has reached its final pose.
            placed = _vise_info(vise)
            _debug_vise(debug, 'Vise after root placement, before jaw move:', placed)
            required_gap = stock['size_y'] if clamp_axis == 'Y' else stock['size_x']
            jaw_ok, jaw_message, delta = _move_jaw_after_root(design, placed, required_gap)
            debug.append(f'Required gap: {required_gap:.6f} cm ({required_gap*10:.3f} mm)')
            debug.append(f'Jaw delta: {delta:.6f} cm ({delta*10:.3f} mm)')
            debug.append(f'Jaw result: {jaw_ok} | {jaw_message}')
            _debug_vise(debug, 'Vise final:', _vise_info(vise))

            if add_fixture:
                _fixture(setup, vise)
                debug.append('Fixture assignment: completed')

            log_path = _write_debug(debug)
            ui.messageBox(
                f'Auto Vise updated for {setup.name}.\n'
                f'Stock: {stock["size_x"]*10:.3f} x {stock["size_y"]*10:.3f} x {stock["size_z"]*10:.3f} mm\n'
                f'{jaw_message}\n\n'
                f'Debug log: {log_path}' +
                ('\nCyan box = stock geometry Auto Vise is using.' if show_debug else ''),
                APP_NAME
            )
        except Exception as exc:
            if debug:
                debug += ['', 'EXCEPTION:', str(exc), traceback.format_exc()]
                _write_debug(debug)
            ui.messageBox(f'{exc}\n\nDetails:\n{traceback.format_exc()}', APP_NAME)


def run(context):
    _, ui = _app_ui()
    try:
        workspace = ui.workspaces.itemById('CAMEnvironment')
        if not workspace:
            raise RuntimeError('Manufacture workspace not found.')

        cmd = ui.commandDefinitions.itemById(CMD_ID)
        if not cmd:
            cmd = ui.commandDefinitions.addButtonDefinition(
                CMD_ID, CMD_NAME, CMD_DESCRIPTION, RESOURCE_FOLDER
            )
        created = _CommandCreated()
        cmd.commandCreated.add(created)
        _handlers.append(created)

        panel = workspace.toolbarPanels.itemById(PANEL_ID)
        if not panel:
            panel = workspace.toolbarPanels.add(PANEL_ID, 'Auto Vise')
        control = panel.controls.itemById(CMD_ID)
        if not control:
            control = panel.controls.addCommand(cmd)
            control.isPromotedByDefault = True
            control.isPromoted = True
    except Exception:
        ui.messageBox('Failed to start Auto Vise:\n' + traceback.format_exc(), APP_NAME)


def stop(context):
    _, ui = _app_ui()
    try:
        _clear_debug()
        workspace = ui.workspaces.itemById('CAMEnvironment')
        if workspace:
            panel = workspace.toolbarPanels.itemById(PANEL_ID)
            if panel:
                control = panel.controls.itemById(CMD_ID)
                if control:
                    control.deleteMe()
                panel.deleteMe()
        cmd = ui.commandDefinitions.itemById(CMD_ID)
        if cmd:
            cmd.deleteMe()
    except Exception:
        ui.messageBox('Failed to stop Auto Vise:\n' + traceback.format_exc(), APP_NAME)
