import adsk.core
import adsk.fusion
import adsk.cam
import traceback

APP_NAME = 'Auto Vise'
CMD_ID = 'JK_AutoVise_Command'
CMD_NAME = 'Auto Vise'
CMD_DESCRIPTION = 'Insert and place a linked vise from CAM setup stock.'
PANEL_ID = 'JK_AutoVise_Panel'
ATTRIBUTE_GROUP = 'JK_AutoVise'

_handlers = []


def _app_ui():
    app = adsk.core.Application.get()
    return app, app.userInterface if app else None


def _cam_product(doc):
    return adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType'))


def _design_product(doc):
    design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    if not design:
        design = adsk.fusion.Design.cast(doc.products.itemByProductType('WorkingModelProductType'))
    return design


def _param_value(setup, name):
    p = setup.parameters.itemByName(name)
    if not p:
        raise RuntimeError(f'Missing CAM parameter: {name}')
    value_obj = p.value
    if not value_obj:
        raise RuntimeError(f'CAM parameter has no value: {name}')
    return value_obj.value


def _choice_value(setup, name):
    value = _param_value(setup, name)
    return str(value).strip().strip("'")


def _float_value(setup, name):
    return float(_param_value(setup, name))


def _read_stock_box(setup):
    """Return box stock dimensions and extents in setup-WCS coordinates, in cm.

    V1 supports Fusion Fixed Size Box and Relative Size Box. For Relative Size
    Box, V1 supports the normal/simple side + top/bottom offset mode.
    """
    mode = setup.stockMode

    if mode == adsk.cam.SetupStockModes.FixedBoxStock:
        sx = _float_value(setup, 'job_stockFixedX')
        sy = _float_value(setup, 'job_stockFixedY')
        sz = _float_value(setup, 'job_stockFixedZ')

    elif mode == adsk.cam.SetupStockModes.RelativeBoxStock:
        base_x = _float_value(setup, 'job_stockFixedX')
        base_y = _float_value(setup, 'job_stockFixedY')
        base_z = _float_value(setup, 'job_stockFixedZ')

        offset_mode = _choice_value(setup, 'job_stockOffsetMode')
        if offset_mode != 'simple':
            raise RuntimeError(
                'V1 supports Relative Size Box with the normal side + top/bottom '
                f'offset mode only. Current job_stockOffsetMode: {offset_mode}'
            )

        side = _float_value(setup, 'job_stockOffsetSides')
        top = _float_value(setup, 'job_stockOffsetTop')
        bottom = _float_value(setup, 'job_stockOffsetBottom')
        sx = base_x + 2.0 * side
        sy = base_y + 2.0 * side
        sz = base_z + top + bottom

    else:
        raise RuntimeError(
            'V1 supports only Fixed Size Box and Relative Size Box stock.'
        )

    origin_mode = _choice_value(setup, 'wcs_origin_mode')
    if origin_mode not in ('stockPoint', 'modelPoint'):
        raise RuntimeError(
            'V1 expects the Setup WCS origin to be a stock/model box point. '
            f'Current wcs_origin_mode: {origin_mode}'
        )

    box_point = _choice_value(setup, 'wcs_origin_boxPoint')
    px, py, pz = _box_point_coordinates(box_point, sx, sy, sz)

    return {
        'size_x': sx,
        'size_y': sy,
        'size_z': sz,
        'min_x': -px,
        'max_x': sx - px,
        'min_y': -py,
        'max_y': sy - py,
        'min_z': -pz,
        'max_z': sz - pz,
        'box_point': box_point,
    }


def _box_point_coordinates(name, sx, sy, sz):
    """Coordinates of Fusion's stock/model box point measured from box minimum."""
    x0, xc, x1 = 0.0, sx / 2.0, sx
    y0, yc, y1 = 0.0, sy / 2.0, sy
    z0, zc, z1 = 0.0, sz / 2.0, sz

    points = {
        'top center': (xc, yc, z1),
        'top 1': (x0, y0, z1),
        'top 2': (x1, y0, z1),
        'top 3': (x0, y1, z1),
        'top 4': (x1, y1, z1),
        'top side 1': (xc, y0, z1),
        'top side 2': (x1, yc, z1),
        'top side 3': (xc, y1, z1),
        'top side 4': (x0, yc, z1),
        'center': (xc, yc, zc),
        'middle 1': (x0, y0, zc),
        'middle 2': (x1, y0, zc),
        'middle 3': (x0, y1, zc),
        'middle 4': (x1, y1, zc),
        'middle side 1': (xc, y0, zc),
        'middle side 2': (x1, yc, zc),
        'middle side 3': (xc, y1, zc),
        'middle side 4': (x0, yc, zc),
        'bottom center': (xc, yc, z0),
        'bottom 1': (x0, y0, z0),
        'bottom 2': (x1, y0, z0),
        'bottom 3': (x0, y1, z0),
        'bottom 4': (x1, y1, z0),
        'bottom side 1': (xc, y0, z0),
        'bottom side 2': (x1, yc, z0),
        'bottom side 3': (xc, y1, z0),
        'bottom side 4': (x0, yc, z0),
    }
    if name not in points:
        raise RuntimeError(f'Unsupported WCS box point in V1: {name}')
    return points[name]


def _center(bb):
    return adsk.core.Point3D.create(
        (bb.minPoint.x + bb.maxPoint.x) / 2.0,
        (bb.minPoint.y + bb.maxPoint.y) / 2.0,
        (bb.minPoint.z + bb.maxPoint.z) / 2.0,
    )


def _child_occurrences_recursive(parent_occ):
    result = []
    children = parent_occ.childOccurrences
    for i in range(children.count):
        child = children.item(i)
        result.append(child)
        result.extend(_child_occurrences_recursive(child))
    return result


def _find_child_by_name(parent_occ, wanted):
    wanted = wanted.lower().strip()
    exact = None
    partial = None
    for occ in _child_occurrences_recursive(parent_occ):
        names = [occ.name or '', occ.component.name if occ.component else '']
        for name in names:
            lowered = name.lower().strip()
            if lowered == wanted or lowered.startswith(wanted + ':'):
                exact = occ
                break
            if wanted in lowered and partial is None:
                partial = occ
        if exact:
            break
    return exact or partial


def _project_range(bb, axis):
    if abs(axis.x) > 0.5:
        vals = [axis.x * bb.minPoint.x, axis.x * bb.maxPoint.x]
    elif abs(axis.y) > 0.5:
        vals = [axis.y * bb.minPoint.y, axis.y * bb.maxPoint.y]
    else:
        vals = [axis.z * bb.minPoint.z, axis.z * bb.maxPoint.z]
    return min(vals), max(vals)


def _vise_geometry(vise_occ):
    fixed = _find_child_by_name(vise_occ, 'fixed jaw')
    moving = _find_child_by_name(vise_occ, 'movable jaw')
    if not fixed or not moving:
        names = [o.name for o in _child_occurrences_recursive(vise_occ)]
        raise RuntimeError(
            'Could not find child components named "fixed jaw" and "movable jaw". '
            f'Found: {", ".join(names)}'
        )

    fbb = fixed.preciseBoundingBox
    mbb = moving.preciseBoundingBox
    fc = _center(fbb)
    mc = _center(mbb)
    dx = mc.x - fc.x
    dy = mc.y - fc.y

    if abs(dx) >= abs(dy):
        sign = 1.0 if dx >= 0 else -1.0
        clamp = adsk.core.Vector3D.create(sign, 0, 0)
    else:
        sign = 1.0 if dy >= 0 else -1.0
        clamp = adsk.core.Vector3D.create(0, sign, 0)

    zaxis = adsk.core.Vector3D.create(0, 0, 1)
    transverse = clamp.crossProduct(zaxis)
    transverse.normalize()

    fmin, fmax = _project_range(fbb, clamp)
    mmin, mmax = _project_range(mbb, clamp)
    fixed_inner = fmax
    moving_inner = mmin
    gap = moving_inner - fixed_inner

    vbb = vise_occ.preciseBoundingBox
    tx0, tx1 = _project_range(vbb, transverse)
    transverse_center = (tx0 + tx1) / 2.0
    jaw_top = max(fbb.maxPoint.z, mbb.maxPoint.z)

    return {
        'fixed': fixed,
        'moving': moving,
        'clamp': clamp,
        'transverse': transverse,
        'fixed_inner': fixed_inner,
        'moving_inner': moving_inner,
        'gap': gap,
        'transverse_center': transverse_center,
        'jaw_top': jaw_top,
    }


def _move_moving_jaw(vise_info, required_gap):
    delta = required_gap - vise_info['gap']
    if abs(delta) < 1e-6:
        return True, 'Jaw already matches stock.'

    moving = vise_info['moving']
    clamp = vise_info['clamp']
    try:
        matrix = moving.transform2.copy()
        t = matrix.translation
        t.x += clamp.x * delta
        t.y += clamp.y * delta
        t.z += clamp.z * delta
        matrix.translation = t
        moving.transform2 = matrix
        return True, f'Moved jaw by {delta * 10.0:.3f} mm.'
    except Exception as exc:
        return False, (
            'The vise was inserted as an external reference, but Fusion did not allow '
            'V1 to move the linked child occurrence "movable jaw". The vise will still '
            f'be positioned from the fixed jaw. Fusion error: {exc}'
        )


def _vec_scaled(v, s):
    return adsk.core.Vector3D.create(v.x * s, v.y * s, v.z * s)


def _point_plus(origin, *vectors):
    x, y, z = origin.x, origin.y, origin.z
    for v in vectors:
        x += v.x
        y += v.y
        z += v.z
    return adsk.core.Point3D.create(x, y, z)


def _place_vise(vise_occ, vise_info, setup, stock, grip_depth_cm, clamp_axis, fixed_side):
    wcs = setup.workCoordinateSystem
    wcs_origin, setup_x, setup_y, setup_z = wcs.getAsCoordinateSystem()
    setup_x.normalize()
    setup_y.normalize()
    setup_z.normalize()

    src_x = vise_info['transverse'].copy()
    src_y = vise_info['clamp'].copy()
    src_z = adsk.core.Vector3D.create(0, 0, 1)

    if clamp_axis == 'Y':
        toward_positive = fixed_side == 'Y-'
        target_y = setup_y.copy()
        if not toward_positive:
            target_y.scaleBy(-1)
        stock_face = stock['min_y'] if toward_positive else stock['max_y']
        anchor = _point_plus(
            wcs_origin,
            _vec_scaled(setup_x, (stock['min_x'] + stock['max_x']) / 2.0),
            _vec_scaled(setup_y, stock_face),
            _vec_scaled(setup_z, stock['min_z'] + grip_depth_cm),
        )
    else:
        toward_positive = fixed_side == 'X-'
        target_y = setup_x.copy()
        if not toward_positive:
            target_y.scaleBy(-1)
        stock_face = stock['min_x'] if toward_positive else stock['max_x']
        anchor = _point_plus(
            wcs_origin,
            _vec_scaled(setup_x, stock_face),
            _vec_scaled(setup_y, (stock['min_y'] + stock['max_y']) / 2.0),
            _vec_scaled(setup_z, stock['min_z'] + grip_depth_cm),
        )

    target_z = setup_z.copy()
    target_x = target_y.crossProduct(target_z)
    target_x.normalize()

    to_origin = _point_plus(
        anchor,
        _vec_scaled(target_x, -vise_info['transverse_center']),
        _vec_scaled(target_y, -vise_info['fixed_inner']),
        _vec_scaled(target_z, -vise_info['jaw_top']),
    )

    transform = adsk.core.Matrix3D.create()
    ok = transform.setToAlignCoordinateSystems(
        adsk.core.Point3D.create(0, 0, 0),
        src_x, src_y, src_z,
        to_origin,
        target_x, target_y, target_z,
    )
    if not ok:
        raise RuntimeError('Failed to build vise placement transform.')
    vise_occ.transform2 = transform


def _add_as_fixture(setup, vise_occ):
    setup.fixtureEnabled = True
    fixtures = adsk.core.ObjectCollection.create()
    try:
        current = setup.fixtures
        for i in range(current.count):
            item = current.item(i)
            if item != vise_occ:
                fixtures.add(item)
    except Exception:
        pass
    fixtures.add(vise_occ)
    setup.fixtures = fixtures


def _choose_vise_file(doc):
    app, ui = _app_ui()
    dlg = ui.createCloudFileDialog()
    dlg.title = 'Select vise master Fusion file'
    dlg.isMultiSelectEnabled = False
    dlg.filter = '*'
    try:
        if doc.dataFile:
            dlg.dataFolder = doc.dataFile.parentFolder
    except Exception:
        pass

    if dlg.showOpen() != adsk.core.DialogResults.DialogOK:
        return None
    data_file = dlg.dataFile
    if not data_file:
        return None

    if not doc.dataFile:
        raise RuntimeError('Save the machining document to Fusion cloud first.')
    if data_file.parentProject.id != doc.dataFile.parentProject.id:
        raise RuntimeError(
            'Linked insert requires the vise file and machining file to be in the same Fusion project.'
        )
    return data_file


def _managed_vise(root):
    for i in range(root.occurrences.count):
        occ = root.occurrences.item(i)
        attr = occ.attributes.itemByName(ATTRIBUTE_GROUP, 'managed')
        if attr and attr.value == '1':
            return occ
    return None


def _get_or_insert_vise(design, data_file):
    root = design.rootComponent
    old = _managed_vise(root)
    if old:
        try:
            old.deleteMe()
        except Exception:
            pass

    occ = root.occurrences.addByInsert(data_file, adsk.core.Matrix3D.create(), True)
    if not occ:
        raise RuntimeError('Fusion failed to insert the vise file as a linked component.')
    try:
        occ.name = f'AUTO_VISE: {data_file.name}'
    except Exception:
        pass
    occ.attributes.add(ATTRIBUTE_GROUP, 'managed', '1')
    occ.attributes.add(ATTRIBUTE_GROUP, 'dataFileId', data_file.id)
    return occ


class _CommandCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            command = args.command
            inputs = command.commandInputs
            app, ui = _app_ui()
            doc = app.activeDocument
            cam = _cam_product(doc)
            if not cam or cam.setups.count == 0:
                ui.messageBox('Create at least one Manufacture Setup first.', APP_NAME)
                return

            setup_input = inputs.addDropDownCommandInput(
                'setup', 'Setup', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            for i in range(cam.setups.count):
                setup = cam.setups.item(i)
                setup_input.listItems.add(setup.name, i == 0, '')

            axis = inputs.addDropDownCommandInput(
                'clamp_axis', 'Clamp along setup axis', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            axis.listItems.add('Y', True, '')
            axis.listItems.add('X', False, '')

            side = inputs.addDropDownCommandInput(
                'fixed_side', 'Fixed jaw side', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            side.listItems.add('Y-', True, '')
            side.listItems.add('Y+', False, '')
            side.listItems.add('X-', False, '')
            side.listItems.add('X+', False, '')

            inputs.addValueInput(
                'grip_depth', 'Grip depth', 'mm', adsk.core.ValueInput.createByString('4 mm')
            )
            inputs.addBoolValueInput('add_fixture', 'Add vise to Setup fixtures', True, '', True)
            inputs.addTextBoxCommandInput(
                'info', '',
                'V1 reads box stock directly from the selected CAM Setup. On OK, select the vise master from Fusion cloud. '
                'The vise master must contain components named "fixed jaw" and "movable jaw".',
                4, True
            )

            execute_handler = _CommandExecute()
            command.execute.add(execute_handler)
            _handlers.append(execute_handler)
        except Exception:
            app, ui = _app_ui()
            if ui:
                ui.messageBox('Command creation failed:\n' + traceback.format_exc(), APP_NAME)


class _CommandExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        app, ui = _app_ui()
        try:
            doc = app.activeDocument
            cam = _cam_product(doc)
            design = _design_product(doc)
            if not cam or not design:
                raise RuntimeError('Active document must contain both Design and Manufacture products.')

            inputs = args.command.commandInputs
            setup_name = inputs.itemById('setup').selectedItem.name
            setup = None
            for i in range(cam.setups.count):
                candidate = cam.setups.item(i)
                if candidate.name == setup_name:
                    setup = candidate
                    break
            if not setup:
                raise RuntimeError(f'Setup not found: {setup_name}')

            clamp_axis = inputs.itemById('clamp_axis').selectedItem.name
            fixed_side = inputs.itemById('fixed_side').selectedItem.name
            if not fixed_side.startswith(clamp_axis):
                raise RuntimeError(
                    f'Fixed jaw side {fixed_side} does not match clamp axis {clamp_axis}.'
                )

            grip_depth_cm = inputs.itemById('grip_depth').value
            add_fixture = inputs.itemById('add_fixture').value

            stock = _read_stock_box(setup)
            data_file = _choose_vise_file(doc)
            if not data_file:
                return

            vise_occ = _get_or_insert_vise(design, data_file)
            vise_info = _vise_geometry(vise_occ)
            required_gap = stock['size_y'] if clamp_axis == 'Y' else stock['size_x']
            jaw_ok, jaw_message = _move_moving_jaw(vise_info, required_gap)

            vise_info = _vise_geometry(vise_occ)
            _place_vise(
                vise_occ, vise_info, setup, stock, grip_depth_cm,
                clamp_axis, fixed_side
            )

            if add_fixture:
                _add_as_fixture(setup, vise_occ)

            stock_mm = (
                stock['size_x'] * 10.0,
                stock['size_y'] * 10.0,
                stock['size_z'] * 10.0,
            )
            msg = (
                f'Placed linked vise for {setup.name}.\n'
                f'Stock: {stock_mm[0]:.3f} x {stock_mm[1]:.3f} x {stock_mm[2]:.3f} mm\n'
                f'WCS box point: {stock["box_point"]}\n'
                f'{jaw_message}'
            )
            if not jaw_ok:
                msg += (
                    '\n\nThis is the main V1 question to test: whether Fusion permits moving '
                    'a child occurrence inside the external reference. If not, the master '
                    'vise needs a slightly different architecture for the movable jaw.'
                )
            ui.messageBox(msg, APP_NAME)

        except Exception as exc:
            ui.messageBox(
                f'{exc}\n\nDetails:\n{traceback.format_exc()}',
                APP_NAME
            )


def run(context):
    app, ui = _app_ui()
    try:
        cam_ws = ui.workspaces.itemById('CAMEnvironment')
        if not cam_ws:
            ui.messageBox('Manufacture workspace not found.', APP_NAME)
            return

        cmd_def = ui.commandDefinitions.itemById(CMD_ID)
        if not cmd_def:
            cmd_def = ui.commandDefinitions.addButtonDefinition(
                CMD_ID, CMD_NAME, CMD_DESCRIPTION
            )

        created_handler = _CommandCreated()
        cmd_def.commandCreated.add(created_handler)
        _handlers.append(created_handler)

        panel = cam_ws.toolbarPanels.itemById(PANEL_ID)
        if not panel:
            panel = cam_ws.toolbarPanels.add(PANEL_ID, 'Auto Vise')

        control = panel.controls.itemById(CMD_ID)
        if not control:
            control = panel.controls.addCommand(cmd_def)
            control.isPromotedByDefault = True
            control.isPromoted = True
    except Exception:
        if ui:
            ui.messageBox('Failed to start Auto Vise:\n' + traceback.format_exc(), APP_NAME)


def stop(context):
    app, ui = _app_ui()
    try:
        cam_ws = ui.workspaces.itemById('CAMEnvironment')
        if cam_ws:
            panel = cam_ws.toolbarPanels.itemById(PANEL_ID)
            if panel:
                control = panel.controls.itemById(CMD_ID)
                if control:
                    control.deleteMe()
                panel.deleteMe()

        cmd_def = ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()
    except Exception:
        if ui:
            ui.messageBox('Failed to stop Auto Vise:\n' + traceback.format_exc(), APP_NAME)
