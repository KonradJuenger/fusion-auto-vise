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
    design = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    return design or adsk.fusion.Design.cast(doc.products.itemByProductType('WorkingModelProductType'))


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

    result = {
        'min_x': min(x0, x1), 'max_x': max(x0, x1),
        'min_y': min(y0, y1), 'max_y': max(y0, y1),
        'min_z': min(z0, z1), 'max_z': max(z0, z1),
    }
    result['size_x'] = result['max_x'] - result['min_x']
    result['size_y'] = result['max_y'] - result['min_y']
    result['size_z'] = result['max_z'] - result['min_z']
    return result


def _v(vector, scale):
    return adsk.core.Vector3D.create(
        vector.x * scale, vector.y * scale, vector.z * scale
    )


def _pt(point, *vectors):
    result = adsk.core.Point3D.create(point.x, point.y, point.z)
    for vector in vectors:
        result.translateBy(vector)
    return result


def _children(occurrence):
    result = []
    children = occurrence.childOccurrences
    for i in range(children.count):
        child = children.item(i)
        result.append(child)
        result.extend(_children(child))
    return result


def _named(occurrence, wanted_name):
    wanted = wanted_name.lower().strip()
    for child in _children(occurrence):
        names = (
            child.name or '',
            child.component.name if child.component else '',
        )
        for name in names:
            if name.lower().split(':')[0].strip() == wanted:
                return child
    return None


def _axis_range(bb, axis):
    if abs(axis.x) > 0.5:
        values = (axis.x * bb.minPoint.x, axis.x * bb.maxPoint.x)
    elif abs(axis.y) > 0.5:
        values = (axis.y * bb.minPoint.y, axis.y * bb.maxPoint.y)
    else:
        values = (axis.z * bb.minPoint.z, axis.z * bb.maxPoint.z)
    return min(values), max(values)


def _overlap(a, b, axis):
    a0, a1 = _axis_range(a, axis)
    b0, b1 = _axis_range(b, axis)
    return max(0.0, min(a1, b1) - max(a0, b0))


def _fixed_face_candidates(fixed, moving_bb, clamp, across):
    """Return plausible fixed gripping faces for one candidate native clamp direction."""
    moving_inner, _ = _axis_range(moving_bb, clamp)
    across0, across1 = _axis_range(moving_bb, across)
    moving_width = max(across1 - across0, 1e-9)
    moving_height = max(moving_bb.maxPoint.z - moving_bb.minPoint.z, 1e-9)

    candidates = []
    for body_index in range(fixed.bRepBodies.count):
        body = fixed.bRepBodies.item(body_index)
        for face_index in range(body.faces.count):
            face = body.faces.item(face_index)
            try:
                bb = face.boundingBox
                c0, c1 = _axis_range(bb, clamp)

                if c1 - c0 > 0.001:
                    continue

                position = (c0 + c1) / 2.0

                if position >= moving_inner - 0.0001:
                    continue

                across_overlap = _overlap(bb, moving_bb, across)
                if across_overlap < moving_width * 0.5:
                    continue

                z_overlap = max(
                    0.0,
                    min(bb.maxPoint.z, moving_bb.maxPoint.z)
                    - max(bb.minPoint.z, moving_bb.minPoint.z),
                )
                if z_overlap < moving_height * 0.35:
                    continue

                if bb.maxPoint.z < moving_bb.maxPoint.z - 0.75:
                    continue

                candidates.append(
                    (
                        position,
                        getattr(face, 'area', 0.0),
                        bb,
                        body_index,
                        face_index,
                        across_overlap,
                        z_overlap,
                    )
                )
            except Exception:
                pass

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates


def _vise_info(vise):
    """Detect native vise clamp direction by testing +/-X and +/-Y."""
    fixed = _named(vise, 'fixed jaw')
    moving = _named(vise, 'movable jaw')
    if not fixed or not moving:
        raise RuntimeError(
            'Vise needs child components named "fixed jaw" and "movable jaw".'
        )

    moving_bb = moving.preciseBoundingBox
    up = adsk.core.Vector3D.create(0, 0, 1)

    directions = (
        ('+X', adsk.core.Vector3D.create(1, 0, 0)),
        ('-X', adsk.core.Vector3D.create(-1, 0, 0)),
        ('+Y', adsk.core.Vector3D.create(0, 1, 0)),
        ('-Y', adsk.core.Vector3D.create(0, -1, 0)),
    )

    solutions = []
    attempts = []

    for label, clamp in directions:
        across = clamp.crossProduct(up)
        if across.length < 1e-9:
            continue
        across.normalize()

        candidates = _fixed_face_candidates(fixed, moving_bb, clamp, across)
        if not candidates:
            attempts.append(f'{label}: no gripping-face candidates')
            continue

        chosen = candidates[0]
        fixed_inner, _, contact_bb, body_index, face_index, _, _ = chosen
        moving_inner, _ = _axis_range(moving_bb, clamp)
        gap = moving_inner - fixed_inner

        if gap <= 0.001:
            attempts.append(f'{label}: candidate found but gap={gap * 10.0:.3f} mm')
            continue

        across0, across1 = _axis_range(contact_bb, across)
        solution = {
            'fixed': fixed,
            'moving': moving,
            'clamp': clamp,
            'across': across,
            'native_clamp_label': label,
            'fixed_inner': fixed_inner,
            'moving_inner': moving_inner,
            'gap': gap,
            'across_center': (across0 + across1) / 2.0,
            'jaw_top': contact_bb.maxPoint.z,
            'contact_bb': contact_bb,
            'contact_body': body_index,
            'contact_face': face_index,
            'candidates': candidates,
        }

        best = candidates[0]
        score = best[5] * max(best[6], 1e-6)
        solutions.append((score, solution))
        attempts.append(
            f'{label}: valid gap={gap * 10.0:.3f} mm '
            f'body={body_index} face={face_index}'
        )

    if not solutions:
        raise RuntimeError(
            'Could not identify the fixed jaw gripping face in any native '
            'vise direction. Detector attempts: ' + ' | '.join(attempts)
        )

    solutions.sort(key=lambda item: item[0], reverse=True)
    result = solutions[0][1]
    result['detector_attempts'] = attempts
    return result


def _place_vise(vise, info, setup, stock, grip, axis, fixed_sign):
    """Place vise around CAM stock locally; Part Position handles the machine."""
    origin, setup_x, setup_y, setup_z = (
        setup.workCoordinateSystem.getAsCoordinateSystem()
    )
    setup_x.normalize()
    setup_y.normalize()
    setup_z.normalize()

    source_across = info['across'].copy()
    source_clamp = info['clamp'].copy()
    source_up = adsk.core.Vector3D.create(0, 0, 1)

    if axis == 'Y':
        inward = setup_y.copy()
        stock_face = stock['max_y'] if fixed_sign == '+' else stock['min_y']
        if fixed_sign == '+':
            inward.scaleBy(-1)
        anchor = _pt(
            origin,
            _v(setup_x, (stock['min_x'] + stock['max_x']) / 2.0),
            _v(setup_y, stock_face),
            _v(setup_z, stock['min_z'] + grip),
        )
    else:
        inward = setup_x.copy()
        stock_face = stock['max_x'] if fixed_sign == '+' else stock['min_x']
        if fixed_sign == '+':
            inward.scaleBy(-1)
        anchor = _pt(
            origin,
            _v(setup_x, stock_face),
            _v(setup_y, (stock['min_y'] + stock['max_y']) / 2.0),
            _v(setup_z, stock['min_z'] + grip),
        )

    target_up = setup_z.copy()
    target_across = inward.crossProduct(target_up)
    target_across.normalize()

    destination_origin = _pt(
        anchor,
        _v(target_across, -info['across_center']),
        _v(inward, -info['fixed_inner']),
        _v(target_up, -info['jaw_top']),
    )

    transform = adsk.core.Matrix3D.create()
    if not transform.setToAlignCoordinateSystems(
        adsk.core.Point3D.create(0, 0, 0),
        source_across,
        source_clamp,
        source_up,
        destination_origin,
        target_across,
        inward,
        target_up,
    ):
        raise RuntimeError('Failed to calculate the vise-to-stock transform.')

    vise.transform2 = transform

    world_clamp = info['clamp'].copy()
    world_clamp.transformBy(transform)
    world_clamp.normalize()

    return transform, anchor, world_clamp


def _move_jaw(design, moving, world_clamp, delta):
    transform = moving.transform2.copy()
    translation = transform.translation
    translation.x += world_clamp.x * delta
    translation.y += world_clamp.y * delta
    translation.z += world_clamp.z * delta
    transform.translation = translation

    try:
        if design.rootComponent.transformOccurrences([moving], [transform], True):
            return True
    except Exception:
        pass

    try:
        moving.transform2 = transform
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

    dialog = ui.createCloudFileDialog()
    dialog.title = 'Select vise master (remembered after this)'
    dialog.isMultiSelectEnabled = False
    dialog.filter = '*'

    try:
        if doc.dataFile:
            dialog.dataFolder = doc.dataFile.parentFolder
    except Exception:
        pass

    if dialog.showOpen() != adsk.core.DialogResults.DialogOK or not dialog.dataFile:
        return None, False

    df = dialog.dataFile

    if not doc.dataFile:
        raise RuntimeError('Save the machining document to Fusion cloud first.')

    if df.parentProject.id != doc.dataFile.parentProject.id:
        raise RuntimeError('Vise and machining file must be in the same Fusion project.')

    _save_master(df)
    return df, False


def _managed_vise(root):
    for i in range(root.occurrences.count):
        occurrence = root.occurrences.item(i)
        attr = occurrence.attributes.itemByName(ATTR, 'managed')
        if attr and attr.value == '1':
            return occurrence
    return None


def _insert_fresh_vise(design, df):
    root = design.rootComponent
    old = _managed_vise(root)

    if old:
        try:
            old.deleteMe()
        except Exception:
            pass

    occurrence = root.occurrences.addByInsert(df, adsk.core.Matrix3D.create(), True)
    if not occurrence:
        raise RuntimeError('Failed to insert linked vise.')

    try:
        occurrence.name = 'AUTO_VISE: ' + df.name
    except Exception:
        pass

    occurrence.attributes.add(ATTR, 'managed', '1')
    occurrence.attributes.add(ATTR, 'data_file_id', df.id)
    return occurrence


def _fixture(setup, vise):
    setup.fixtureEnabled = True
    items = adsk.core.ObjectCollection.create()

    try:
        old = setup.fixtures
        for i in range(old.count):
            item = old.item(i)
            occurrence = adsk.fusion.Occurrence.cast(item)
            if occurrence:
                attr = occurrence.attributes.itemByName(ATTR, 'managed')
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
    for name, mm in zip(PART_POS_PARAMS, xyz_mm):
        p = setup.parameters.itemByName(name)
        if not p:
            raise RuntimeError(
                f'This Setup does not expose {name}. Assign a machine to the Setup and make sure Part Position is available.'
            )

        try:
            p.expression = f'{mm:.6f} mm'
            continue
        except Exception:
            pass

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


def _save_workholding(axis, fixed_sign, grip_mm):
    settings = _load_settings()
    settings['vise_clamp_axis'] = axis
    settings['fixed_side_sign'] = fixed_sign
    settings['grip_depth_mm'] = float(grip_mm)
    _save_settings(settings)


def _xyz(value):
    return f'({value.x:.6f}, {value.y:.6f}, {value.z:.6f})'


def _bb(bb):
    return f'min={_xyz(bb.minPoint)} max={_xyz(bb.maxPoint)}'


def _mat(matrix):
    try:
        return '[' + ', '.join(f'{x:.6f}' for x in matrix.asArray()) + ']'
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
        _v(x, (stock['min_x'] + stock['max_x']) / 2.0),
        _v(y, (stock['min_y'] + stock['max_y']) / 2.0),
        _v(z, (stock['min_z'] + stock['max_z']) / 2.0),
    )

    box = adsk.core.OrientedBoundingBox3D.create(center, x, y, stock['size_x'], stock['size_y'], stock['size_z'])
    body = group.addBRepBody(adsk.fusion.TemporaryBRepManager.get().createBox(box))
    try:
        body.setOpacity(0.18, True)
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
        f'  native clamp={info["native_clamp_label"]} vector={_xyz(info["clamp"])} across={_xyz(info["across"])}',
        f'  fixed bbox: {_bb(info["fixed"].preciseBoundingBox)}',
        f'  moving bbox: {_bb(info["moving"].preciseBoundingBox)}',
        f'  fixed contact={info["fixed_inner"]:.6f} moving contact={info["moving_inner"]:.6f} gap={info["gap"] * 10.0:.3f} mm',
        f'  chosen contact body={info["contact_body"]} face={info["contact_face"]} {_bb(info["contact_bb"])}',
        f'  candidates={len(info["candidates"])}',
    ]

    for attempt in info.get('detector_attempts', []):
        lines.append(f'  detector: {attempt}')

    for candidate in info['candidates'][:8]:
        position, area, bb, body_index, face_index, across_overlap, z_overlap = candidate
        lines.append(
            f'    body={body_index} face={face_index} pos={position:.6f} area={area:.4f} '
            f'across_overlap={across_overlap:.4f} z_overlap={z_overlap:.4f} {_bb(bb)}'
        )


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

            settings = _load_settings()
            inputs = args.command.commandInputs

            setup_input = inputs.addDropDownCommandInput('setup', 'Setup', adsk.core.DropDownStyles.TextListDropDownStyle)
            for i in range(cam.setups.count):
                setup_input.listItems.add(cam.setups.item(i).name, i == 0, '')

            first_setup = cam.setups.item(0)

            saved_axis = settings.get('vise_clamp_axis', 'X')
            if saved_axis not in ('X', 'Y'):
                saved_axis = 'X'

            orientation = inputs.addDropDownCommandInput(
                'orientation', 'Vise clamping direction', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            orientation.listItems.add('Setup X', saved_axis == 'X', '')
            orientation.listItems.add('Setup Y', saved_axis == 'Y', '')

            saved_sign = settings.get('fixed_side_sign', '+')
            if saved_sign not in ('+', '-'):
                saved_sign = '+'

            fixed_side = inputs.addDropDownCommandInput(
                'fixed_sign', 'Fixed jaw side', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            fixed_side.listItems.add('+ side', saved_sign == '+', '')
            fixed_side.listItems.add('- side', saved_sign == '-', '')

            grip_mm = float(settings.get('grip_depth_mm', 4.0))
            inputs.addValueInput('grip', 'Grip depth', 'mm', adsk.core.ValueInput.createByString(f'{grip_mm:.3f} mm'))

            xyz, xyz_source = _initial_machine_position_mm(first_setup)
            inputs.addValueInput('machine_x', 'Machine Part Position X', 'mm', adsk.core.ValueInput.createByString(f'{xyz[0]:.6f} mm'))
            inputs.addValueInput('machine_y', 'Machine Part Position Y', 'mm', adsk.core.ValueInput.createByString(f'{xyz[1]:.6f} mm'))
            inputs.addValueInput('machine_z', 'Machine Part Position Z', 'mm', adsk.core.ValueInput.createByString(f'{xyz[2]:.6f} mm'))

            inputs.addBoolValueInput('remember_xyz', 'Remember machine XYZ', True, '', True)
            inputs.addBoolValueInput('fixture', 'Add vise to Setup fixtures', True, '', True)
            inputs.addBoolValueInput('choose', 'Choose/change vise master', True, '', False)
            inputs.addBoolValueInput('debug', 'Show local stock + write log', True, '', True)

            master_name = settings.get('vise_name', 'none yet')
            part_position_available = _part_position_values_mm(first_setup) is not None

            inputs.addTextBoxCommandInput(
                'info', '',
                f'Remembered vise: {master_name}\n'
                f'Machine XYZ source: {xyz_source}\n'
                f'Fusion Part Position available: {"yes" if part_position_available else "NO"}\n'
                'Vise orientation is local to the Setup. Machine X/Y/Z then places the complete part + fixture assembly in the machine model.',
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

            orientation_name = inputs.itemById('orientation').selectedItem.name
            axis = 'X' if orientation_name == 'Setup X' else 'Y'

            fixed_name = inputs.itemById('fixed_sign').selectedItem.name
            fixed_sign = '+' if fixed_name.startswith('+') else '-'

            stock = _stock(setup)
            wcs_origin, _, _, _ = setup.workCoordinateSystem.getAsCoordinateSystem()

            grip_cm = inputs.itemById('grip').value
            grip_mm = grip_cm * 10.0

            machine_xyz_mm = (
                inputs.itemById('machine_x').value * 10.0,
                inputs.itemById('machine_y').value * 10.0,
                inputs.itemById('machine_z').value * 10.0,
            )

            lines = [
                f'Auto Vise debug {datetime.now().isoformat(timespec="seconds")}',
                f'Document: {doc.name}',
                f'Setup: {setup.name}',
                f'Vise clamping direction Setup {axis}, fixed jaw {fixed_sign} side',
                f'Grip depth {grip_mm:.3f} mm',
                f'Stock size {stock["size_x"] * 10.0:.3f} x {stock["size_y"] * 10.0:.3f} x {stock["size_z"] * 10.0:.3f} mm',
                f'Setup WCS origin {_xyz(wcs_origin)}',
                f'Setup WCS matrix {_mat(setup.workCoordinateSystem)}',
                f'Requested Fusion Part Position XYZ: {machine_xyz_mm[0]:.3f}, {machine_xyz_mm[1]:.3f}, {machine_xyz_mm[2]:.3f} mm',
            ]

            before = _part_position_values_mm(setup)
            if before is None:
                lines.append('Part Position before: <not exposed by Setup>')
            else:
                lines.append(f'Part Position before: {before[0]:.3f}, {before[1]:.3f}, {before[2]:.3f} mm')

            df, remembered = _master(doc, inputs.itemById('choose').value)
            if not df:
                return

            lines.append(f'Vise master {df.name}, remembered={remembered}, id={df.id}')

            vise = _insert_fresh_vise(design, df)
            info = _vise_info(vise)
            _log_info(lines, info, 'Master vise:')

            placement, anchor, world_clamp = _place_vise(
                vise, info, setup, stock, grip_cm, axis, fixed_sign
            )

            lines += [
                f'Local fixed-jaw anchor {_xyz(anchor)}',
                f'Local vise placement {_mat(placement)}',
                f'World clamp {_xyz(world_clamp)}',
            ]

            required_gap = stock['size_x'] if axis == 'X' else stock['size_y']
            jaw_delta = required_gap - info['gap']

            lines.append(f'Required jaw gap {required_gap * 10.0:.3f} mm; jaw delta {jaw_delta * 10.0:.3f} mm')

            if not _move_jaw(design, info['moving'], world_clamp, jaw_delta):
                raise RuntimeError('Fusion rejected the movable-jaw transform.')

            lines.append(f'Moving jaw after adjustment: {_bb(info["moving"].preciseBoundingBox)}')

            if inputs.itemById('fixture').value:
                _fixture(setup, vise)
                lines.append('Fixture assigned')

            _set_part_position(setup, machine_xyz_mm)
            adsk.doEvents()

            after = _part_position_values_mm(setup)
            if after is None:
                lines.append('Part Position after: <could not read back>')
            else:
                lines.append(f'Part Position after: {after[0]:.3f}, {after[1]:.3f}, {after[2]:.3f} mm')

            if inputs.itemById('remember_xyz').value:
                _save_machine_position_mm(machine_xyz_mm)
                lines.append('Machine Part Position XYZ saved in settings.json')

            _save_workholding(axis, fixed_sign, grip_mm)
            lines.append('Vise orientation / fixed side / grip depth saved in settings.json')

            if inputs.itemById('debug').value:
                _debug_stock(design, setup, stock)
                lines.append('Local cyan stock debug geometry created')

            path = _write(lines)
            ui.messageBox(
                f'Auto Vise updated {setup.name}.\n'
                f'Vise clamps along Setup {axis}, fixed jaw {fixed_sign} side.\n'
                f'Jaw adjusted by {jaw_delta * 10.0:.3f} mm.\n'
                f'Part Position: X {machine_xyz_mm[0]:.3f}, Y {machine_xyz_mm[1]:.3f}, Z {machine_xyz_mm[2]:.3f} mm.\n\n'
                f'Debug log: {path}',
                APP_NAME,
            )

        except Exception as exc:
            lines += ['', f'EXCEPTION: {exc}', traceback.format_exc()]
            path = _write(lines)
            ui.messageBox(f'{exc}\n\nDebug log: {path}\n\n{traceback.format_exc()}', APP_NAME)


def _destroy_ui(ui):
    """Remove stale Fusion UI objects so re-running hot-reloads the dialog."""
    try:
        workspace = ui.workspaces.itemById('CAMEnvironment')
    except Exception:
        workspace = None

    if workspace:
        try:
            panel = workspace.toolbarPanels.itemById(PANEL_ID)
            if panel:
                try:
                    control = panel.controls.itemById(CMD_ID)
                    if control:
                        control.deleteMe()
                except Exception:
                    pass
                try:
                    panel.deleteMe()
                except Exception:
                    pass
        except Exception:
            pass

    try:
        command = ui.commandDefinitions.itemById(CMD_ID)
        if command:
            command.deleteMe()
    except Exception:
        pass


def run(context):
    _, ui = _app_ui()

    try:
        _destroy_ui(ui)
        _handlers.clear()

        workspace = ui.workspaces.itemById('CAMEnvironment')
        if not workspace:
            raise RuntimeError('Manufacture workspace not found.')

        command = ui.commandDefinitions.addButtonDefinition(
            CMD_ID,
            'Auto Vise',
            'Fit a linked vise to CAM stock and set native Fusion Part Position.',
            RES,
        )

        handler = Created()
        command.commandCreated.add(handler)
        _handlers.append(handler)

        panel = workspace.toolbarPanels.add(PANEL_ID, 'Auto Vise')
        control = panel.controls.addCommand(command)
        control.isPromotedByDefault = True
        control.isPromoted = True

    except Exception:
        ui.messageBox(traceback.format_exc(), APP_NAME)


def stop(context):
    _, ui = _app_ui()
    try:
        _destroy_ui(ui)
        _clear_debug()
        _handlers.clear()
    except Exception:
        pass
