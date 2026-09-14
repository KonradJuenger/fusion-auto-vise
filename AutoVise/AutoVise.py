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
    return adsk.core.Vector3D.create(vector.x * scale, vector.y * scale, vector.z * scale)


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
        for name in (child.name or '', child.component.name if child.component else ''):
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
                candidates.append((
                    position,
                    getattr(face, 'area', 0.0),
                    bb,
                    body_index,
                    face_index,
                    across_overlap,
                    z_overlap,
                ))
            except Exception:
                pass

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates


def _vise_info(vise):
    fixed = _named(vise, 'fixed jaw')
    moving = _named(vise, 'movable jaw')
    if not fixed or not moving:
        raise RuntimeError('Vise needs child components named "fixed jaw" and "movable jaw".')

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
        info = {
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
        score = candidates[0][5] * max(candidates[0][6], 1e-6)
        solutions.append((score, info))
        attempts.append(f'{label}: valid gap={gap * 10.0:.3f} mm body={body_index} face={face_index}')

    if not solutions:
        raise RuntimeError(
            'Could not identify the fixed jaw gripping face in any native vise direction. '
            'Detector attempts: ' + ' | '.join(attempts)
        )

    solutions.sort(key=lambda item: item[0], reverse=True)
    result = solutions[0][1]
    result['detector_attempts'] = attempts
    return result


def _slider_candidates(vise, moving):
    candidates = []
    component = vise.component

    def score_joint(joint):
        score = 0
        try:
            name = (joint.name or '').lower()
            if 'jaw' in name or 'slide' in name or 'vise' in name:
                score += 2
        except Exception:
            pass
        for attr in ('occurrenceOne', 'occurrenceTwo'):
            try:
                occ = getattr(joint, attr)
                if occ and 'movable jaw' in (occ.name or '').lower():
                    score += 10
            except Exception:
                pass
        return score

    try:
        for joint in component.allJoints:
            try:
                motion = adsk.fusion.SliderJointMotion.cast(joint.jointMotion)
                if motion:
                    candidates.append((score_joint(joint), 'Joint', joint.name, motion))
            except Exception:
                pass
    except Exception:
        pass

    try:
        for joint in component.allAsBuiltJoints:
            try:
                motion = adsk.fusion.SliderJointMotion.cast(joint.jointMotion)
                if motion:
                    candidates.append((score_joint(joint), 'AsBuiltJoint', joint.name, motion))
            except Exception:
                pass
    except Exception:
        pass

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates


def _translate_moving_occurrence(design, moving, clamp, delta):
    transform = moving.transform2.copy()
    t = transform.translation
    t.x += clamp.x * delta
    t.y += clamp.y * delta
    t.z += clamp.z * delta
    transform.translation = t
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


def _set_jaw_gap(design, vise, required_gap, lines):
    info0 = _vise_info(vise)
    gap0 = info0['gap']
    delta = required_gap - gap0
    lines.append(
        f'Jaw gap before: {gap0 * 10.0:.3f} mm; target {required_gap * 10.0:.3f} mm; '
        f'delta {delta * 10.0:.3f} mm'
    )

    if abs(delta) <= 0.002:
        return info0, 'already correct'

    sliders = _slider_candidates(vise, info0['moving'])
    if sliders:
        _, kind, name, motion = sliders[0]
        current = float(motion.slideValue)
        lines.append(f'Using slider {kind} "{name}", current slideValue={current * 10.0:.3f} mm')

        best = None
        errors = []
        for sign in (1.0, -1.0):
            try:
                motion.slideValue = current
                adsk.doEvents()
                motion.slideValue = current + sign * delta
                adsk.doEvents()
                trial = _vise_info(vise)
                error = abs(trial['gap'] - required_gap)
                lines.append(
                    f'  trial sign {sign:+.0f}: slideValue={motion.slideValue * 10.0:.3f} mm, '
                    f'gap={trial["gap"] * 10.0:.3f} mm, error={error * 10.0:.3f} mm'
                )
                if best is None or error < best[0]:
                    best = (error, float(motion.slideValue), trial)
            except Exception as exc:
                errors.append(str(exc))

        if best:
            motion.slideValue = best[1]
            adsk.doEvents()
            final = _vise_info(vise)
            final_error = abs(final['gap'] - required_gap)
            if final_error <= 0.01:
                return final, f'slider joint {name}'
            lines.append(f'Slider verification failed: final gap {final["gap"] * 10.0:.3f} mm')
        elif errors:
            lines.append('Slider drive errors: ' + ' | '.join(errors))

        try:
            motion.slideValue = current
            adsk.doEvents()
        except Exception:
            pass

    lines.append('No usable slider result; falling back to direct movable-jaw transform.')
    info = _vise_info(vise)
    delta = required_gap - info['gap']
    if not _translate_moving_occurrence(design, info['moving'], info['clamp'], delta):
        raise RuntimeError('Fusion rejected both slider-joint drive and movable-jaw transform.')
    adsk.doEvents()
    final = _vise_info(vise)
    error = abs(final['gap'] - required_gap)
    lines.append(f'Fallback gap after transform: {final["gap"] * 10.0:.3f} mm')
    if error > 0.01:
        raise RuntimeError(
            f'Movable jaw did not reach stock width. Target {required_gap * 10.0:.3f} mm, '
            f'actual {final["gap"] * 10.0:.3f} mm.'
        )
    return final, 'direct occurrence transform fallback'


def _place_vise(vise, info, setup, stock, grip, axis, fixed_sign):
    origin, setup_x, setup_y, setup_z = setup.workCoordinateSystem.getAsCoordinateSystem()
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
    return transform, anchor


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

    settings['vise_data_file_id'] = df.id
    settings['vise_name'] = df.name
    _save_settings(settings)
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


def _table_attach_status(setup):
    p = setup.parameters.itemByName('job_positionAttach')
    if not p or not p.value:
        return None, 'job_positionAttach not exposed by this Setup'
    try:
        value = adsk.cam.CadObjectParameterValue.cast(p.value)
        objects = list(value.value) if value else []
        if objects:
            names = []
            for obj in objects:
                names.append(getattr(obj, 'name', getattr(obj, 'objectType', '<entity>')))
            return True, ', '.join(names)
        return False, 'no machine/table attachment point selected'
    except Exception as exc:
        return None, f'could not inspect job_positionAttach: {exc}'


def _set_part_position(setup, xyz_mm):
    for name, mm in zip(PART_POS_PARAMS, xyz_mm):
        p = setup.parameters.itemByName(name)
        if not p:
            raise RuntimeError(
                f'This Setup does not expose {name}. Assign a machine and enable Part Position.'
            )
        p.expression = f'{mm:.6f} mm'


def _position_param_debug(setup):
    rows = []
    try:
        params = setup.parameters
        for i in range(params.count):
            p = params.item(i)
            name = p.name
            low = name.lower()
            if 'position' not in low and 'attach' not in low:
                continue
            try:
                val = p.value
                typ = val.objectType if val else '<none>'
                cad = adsk.cam.CadObjectParameterValue.cast(val) if val else None
                if cad:
                    content = f'{len(cad.value)} object(s)'
                else:
                    raw = getattr(val, 'value', '<n/a>') if val else '<none>'
                    content = str(raw)
                rows.append(f'{name}: {typ} = {content}; expr={p.expression}')
            except Exception as exc:
                rows.append(f'{name}: <read failed {exc}>')
    except Exception as exc:
        rows.append(f'<parameter enumeration failed: {exc}>')
    return rows


def _saved_machine_position_mm():
    value = _load_settings().get('machine_part_position_mm')
    if isinstance(value, list) and len(value) == 3:
        try:
            return tuple(float(x) for x in value)
        except Exception:
            pass
    return None


def _setup_by_name(cam, name):
    for i in range(cam.setups.count):
        setup = cam.setups.item(i)
        if setup.name == name:
            return setup
    return None


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
        _v(x, (stock['min_x'] + stock['max_x']) / 2.0),
        _v(y, (stock['min_y'] + stock['max_y']) / 2.0),
        _v(z, (stock['min_z'] + stock['max_z']) / 2.0),
    )
    box = adsk.core.OrientedBoundingBox3D.create(
        center, x, y, stock['size_x'], stock['size_y'], stock['size_z']
    )
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


def _log_vise(lines, info, label):
    lines += [
        label,
        f'  native clamp={info["native_clamp_label"]} vector={_xyz(info["clamp"])} across={_xyz(info["across"])}',
        f'  fixed bbox: {_bb(info["fixed"].preciseBoundingBox)}',
        f'  moving bbox: {_bb(info["moving"].preciseBoundingBox)}',
        f'  fixed contact={info["fixed_inner"]:.6f} moving contact={info["moving_inner"]:.6f} '
        f'gap={info["gap"] * 10.0:.3f} mm',
        f'  chosen contact body={info["contact_body"]} face={info["contact_face"]} {_bb(info["contact_bb"])}',
    ]
    for attempt in info.get('detector_attempts', []):
        lines.append('  detector: ' + attempt)


class Created(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        app, ui = _app_ui()
        try:
            cam = _cam(app.activeDocument)
            if not cam or not cam.setups.count:
                raise RuntimeError('Create a Manufacture Setup first.')

            inputs = args.command.commandInputs
            settings = _load_settings()

            setup_input = inputs.addDropDownCommandInput(
                'setup', 'Setup', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            for n in range(cam.setups.count):
                setup_input.listItems.add(cam.setups.item(n).name, n == 0, '')
            first_setup = cam.setups.item(0)

            saved_axis = settings.get('vise_setup_axis', 'X')
            axis = inputs.addDropDownCommandInput(
                'axis', 'Vise clamping direction', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            axis.listItems.add('Setup X', saved_axis == 'X', '')
            axis.listItems.add('Setup Y', saved_axis == 'Y', '')

            saved_sign = settings.get('fixed_jaw_sign', '+')
            side = inputs.addDropDownCommandInput(
                'side', 'Fixed jaw side', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            side.listItems.add('+ side', saved_sign == '+', '')
            side.listItems.add('- side', saved_sign == '-', '')

            grip_mm = float(settings.get('grip_depth_mm', 4.0))
            inputs.addValueInput(
                'grip', 'Grip depth', 'mm',
                adsk.core.ValueInput.createByString(f'{grip_mm:.3f} mm')
            )

            xyz = _saved_machine_position_mm() or _part_position_values_mm(first_setup) or (0.0, 0.0, 0.0)
            inputs.addValueInput(
                'machine_x', 'Part Position X offset', 'mm',
                adsk.core.ValueInput.createByString(f'{xyz[0]:.3f} mm')
            )
            inputs.addValueInput(
                'machine_y', 'Part Position Y offset', 'mm',
                adsk.core.ValueInput.createByString(f'{xyz[1]:.3f} mm')
            )
            inputs.addValueInput(
                'machine_z', 'Part Position Z offset', 'mm',
                adsk.core.ValueInput.createByString(f'{xyz[2]:.3f} mm')
            )

            inputs.addBoolValueInput('remember_xyz', 'Remember XYZ offsets', True, '', True)
            inputs.addBoolValueInput('fixture', 'Add vise to Setup fixtures', True, '', True)
            inputs.addBoolValueInput('choose', 'Choose/change vise master', True, '', False)
            inputs.addBoolValueInput('debug', 'Show local stock + write log', True, '', True)

            attach_ok, attach_text = _table_attach_status(first_setup)
            master = settings.get('vise_name', 'none yet')
            attach_state = 'SET' if attach_ok is True else ('MISSING' if attach_ok is False else 'UNKNOWN')
            inputs.addTextBoxCommandInput(
                'info', '',
                f'Remembered vise: {master}\n'
                f'Machine table attach point: {attach_state} ({attach_text})\n'
                'Important: X/Y/Z are offsets from Fusion\'s Table Attach Point, not absolute machine coordinates. '
                'Set Table Attach Point once in Setup > Part Position.',
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

            axis = 'X' if inputs.itemById('axis').selectedItem.name.endswith('X') else 'Y'
            fixed_sign = '+' if inputs.itemById('side').selectedItem.name.startswith('+') else '-'
            grip = inputs.itemById('grip').value
            stock = _stock(setup)
            required_gap = stock['size_x'] if axis == 'X' else stock['size_y']
            origin, _, _, _ = setup.workCoordinateSystem.getAsCoordinateSystem()

            machine_xyz_mm = (
                inputs.itemById('machine_x').value * 10.0,
                inputs.itemById('machine_y').value * 10.0,
                inputs.itemById('machine_z').value * 10.0,
            )

            attach_ok, attach_text = _table_attach_status(setup)
            lines = [
                f'Auto Vise debug {datetime.now().isoformat(timespec="seconds")}',
                f'Document: {doc.name}',
                f'Setup: {setup.name}',
                f'Vise clamping direction Setup {axis}, fixed jaw {fixed_sign} side',
                f'Grip depth {grip * 10.0:.3f} mm',
                f'Stock size {stock["size_x"] * 10.0:.3f} x {stock["size_y"] * 10.0:.3f} '
                f'x {stock["size_z"] * 10.0:.3f} mm',
                f'Setup WCS origin {_xyz(origin)}',
                f'Setup WCS matrix {_mat(setup.workCoordinateSystem)}',
                f'Requested Part Position offsets: '
                f'{machine_xyz_mm[0]:.3f}, {machine_xyz_mm[1]:.3f}, {machine_xyz_mm[2]:.3f} mm',
                f'Table attach status: {attach_ok} | {attach_text}',
            ]
            lines += ['Part Position parameters:'] + ['  ' + row for row in _position_param_debug(setup)]

            if attach_ok is False:
                path = _write(lines)
                raise RuntimeError(
                    'Fusion Part Position has no Table Attach Point selected.\n\n'
                    'Open Setup > Part Position and select the machine Table Attach Point once. '
                    'The X/Y/Z fields are only offsets from that point, so without it they cannot place the part in the machine.\n\n'
                    f'Debug log: {path}'
                )

            df, remembered = _master(doc, inputs.itemById('choose').value)
            if not df:
                return
            lines.append(f'Vise master {df.name}, remembered={remembered}, id={df.id}')

            vise = _insert_fresh_vise(design, df)
            before = _vise_info(vise)
            _log_vise(lines, before, 'Master vise before jaw adjustment:')

            fitted_info, jaw_method = _set_jaw_gap(design, vise, required_gap, lines)
            _log_vise(lines, fitted_info, f'Master vise after jaw adjustment ({jaw_method}):')

            placement, anchor = _place_vise(
                vise, fitted_info, setup, stock, grip, axis, fixed_sign
            )
            lines += [
                f'Local fixed-jaw anchor {_xyz(anchor)}',
                f'Local vise placement {_mat(placement)}',
            ]

            if inputs.itemById('fixture').value:
                _fixture(setup, vise)
                lines.append('Fixture assigned')

            _set_part_position(setup, machine_xyz_mm)
            adsk.doEvents()
            applied = _part_position_values_mm(setup)
            lines.append('Part Position after: ' + (
                f'{applied[0]:.3f}, {applied[1]:.3f}, {applied[2]:.3f} mm'
                if applied is not None else '<could not read back>'
            ))

            settings = _load_settings()
            settings['vise_setup_axis'] = axis
            settings['fixed_jaw_sign'] = fixed_sign
            settings['grip_depth_mm'] = grip * 10.0
            if inputs.itemById('remember_xyz').value:
                settings['machine_part_position_mm'] = list(machine_xyz_mm)
            _save_settings(settings)
            lines.append('Orientation / grip / machine offsets saved in settings.json')

            if inputs.itemById('debug').value:
                _debug_stock(design, setup, stock)
                lines.append('Local cyan stock debug geometry created')

            path = _write(lines)
            ui.messageBox(
                f'Auto Vise updated {setup.name}.\n'
                f'Jaw gap verified at {fitted_info["gap"] * 10.0:.3f} mm using {jaw_method}.\n'
                f'Part Position offsets: X {machine_xyz_mm[0]:.3f}, '
                f'Y {machine_xyz_mm[1]:.3f}, Z {machine_xyz_mm[2]:.3f} mm.\n\n'
                f'Debug log: {path}',
                APP_NAME,
            )

        except Exception as exc:
            lines += ['', f'EXCEPTION: {exc}', traceback.format_exc()]
            _write(lines)
            ui.messageBox(f'{exc}\n\n{traceback.format_exc()}', APP_NAME)


def _remove_ui(ui):
    try:
        workspace = ui.workspaces.itemById('CAMEnvironment')
        panel = workspace.toolbarPanels.itemById(PANEL_ID) if workspace else None
        if panel:
            control = panel.controls.itemById(CMD_ID)
            if control:
                control.deleteMe()
            panel.deleteMe()
    except Exception:
        pass
    try:
        command = ui.commandDefinitions.itemById(CMD_ID)
        if command:
            command.deleteMe()
    except Exception:
        pass


def run(context):
    global _handlers
    _, ui = _app_ui()
    try:
        _remove_ui(ui)
        _handlers = []
        workspace = ui.workspaces.itemById('CAMEnvironment')
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
    global _handlers
    _, ui = _app_ui()
    try:
        _remove_ui(ui)
        _clear_debug()
        _handlers = []
    except Exception:
        pass
