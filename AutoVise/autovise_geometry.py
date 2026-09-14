import adsk.core
import adsk.fusion
import adsk.cam

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
    for vec in vectors:
        q.translateBy(vec)
    return q


def _children(occ):
    out = []
    for i in range(occ.childOccurrences.count):
        child = occ.childOccurrences.item(i)
        out.append(child)
        out.extend(_children(child))
    return out


def _named(occ, wanted):
    wanted = wanted.lower().strip()
    for child in _children(occ):
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
    for bi in range(fixed.bRepBodies.count):
        body = fixed.bRepBodies.item(bi)
        for fi in range(body.faces.count):
            face = body.faces.item(fi)
            try:
                bb = face.boundingBox
                c0, c1 = _axis_range(bb, clamp)
                if c1 - c0 > 0.001:
                    continue
                pos = (c0 + c1) / 2.0
                if pos >= moving_inner - 0.0001:
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
                    pos, getattr(face, 'area', 0.0), bb, bi, fi,
                    across_overlap, z_overlap,
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
        fixed_inner, _, contact_bb, bi, fi, _, _ = chosen
        moving_inner, _ = _axis_range(moving_bb, clamp)
        gap = moving_inner - fixed_inner
        if gap <= 0.001:
            attempts.append(f'{label}: candidate gap={gap * 10.0:.3f} mm')
            continue
        a0, a1 = _axis_range(contact_bb, across)
        solution = {
            'fixed': fixed,
            'moving': moving,
            'clamp': clamp,
            'across': across,
            'native_clamp_label': label,
            'fixed_inner': fixed_inner,
            'moving_inner': moving_inner,
            'gap': gap,
            'across_center': (a0 + a1) / 2.0,
            'jaw_top': contact_bb.maxPoint.z,
            'contact_bb': contact_bb,
            'contact_body': bi,
            'contact_face': fi,
            'candidates': candidates,
        }
        best = candidates[0]
        score = best[5] * max(best[6], 1e-6)
        solutions.append((score, solution))
        attempts.append(f'{label}: valid gap={gap * 10.0:.3f} mm body={bi} face={fi}')

    if not solutions:
        raise RuntimeError('Could not identify fixed jaw gripping face. ' + ' | '.join(attempts))
    solutions.sort(key=lambda item: item[0], reverse=True)
    result = solutions[0][1]
    result['detector_attempts'] = attempts
    return result


def _slider_candidates(vise):
    candidates = []
    component = vise.component

    def score_joint(joint):
        score = 0
        name = (joint.name or '').lower()
        if 'slider' in name:
            score += 4
        if 'jaw' in name:
            score += 4
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
    """Set the physical jaw gap. Slider slideValue is absolute, not a delta."""
    initial = _vise_info(vise)
    lines.append(
        f'Jaw gap before: {initial["gap"] * 10.0:.3f} mm; '
        f'target {required_gap * 10.0:.3f} mm'
    )
    if abs(initial['gap'] - required_gap) <= 0.005:
        return initial, 'already correct'

    sliders = _slider_candidates(vise)
    if sliders:
        _, kind, name, motion = sliders[0]
        current = float(motion.slideValue)
        lines.append(
            f'Using slider {kind} "{name}", current slideValue={current * 10.0:.3f} mm. '
            'Driving absolute target gap.'
        )
        trials = []
        errors = []
        # In the reference vise the joint zero is the closed jaw position, so slideValue equals gap.
        # Try both signs to also support an oppositely-oriented slider joint.
        for target in (required_gap, -required_gap):
            try:
                motion.slideValue = target
                adsk.doEvents()
                trial = _vise_info(vise)
                err = abs(trial['gap'] - required_gap)
                trials.append((err, float(motion.slideValue), trial))
                lines.append(
                    f'  absolute trial {target * 10.0:+.3f} mm -> '
                    f'slideValue={motion.slideValue * 10.0:.3f} mm, '
                    f'gap={trial["gap"] * 10.0:.3f} mm, error={err * 10.0:.3f} mm'
                )
            except Exception as exc:
                errors.append(str(exc))

        if trials:
            trials.sort(key=lambda item: item[0])
            best_error, best_value, _ = trials[0]
            motion.slideValue = best_value
            adsk.doEvents()
            final = _vise_info(vise)
            final_error = abs(final['gap'] - required_gap)
            lines.append(
                f'Slider final: slideValue={motion.slideValue * 10.0:.3f} mm, '
                f'gap={final["gap"] * 10.0:.3f} mm'
            )
            if final_error <= 0.005:
                return final, f'slider joint {name}'
            raise RuntimeError(
                f'Slider joint "{name}" did not reach stock width. '
                f'Target {required_gap * 10.0:.3f} mm, actual {final["gap"] * 10.0:.3f} mm.'
            )
        raise RuntimeError(
            f'Could not drive slider joint "{name}". ' + (' | '.join(errors) if errors else '')
        )

    # Legacy fallback only for vise masters without a slider joint.
    info = _vise_info(vise)
    delta = required_gap - info['gap']
    lines.append(
        f'No slider joint found. Direct movable-jaw fallback delta={delta * 10.0:.3f} mm.'
    )
    if not _translate_moving_occurrence(design, info['moving'], info['clamp'], delta):
        raise RuntimeError('No slider joint found and Fusion rejected movable-jaw transform.')
    adsk.doEvents()
    final = _vise_info(vise)
    if abs(final['gap'] - required_gap) > 0.005:
        raise RuntimeError(
            f'Movable jaw did not reach stock width. Target {required_gap * 10.0:.3f} mm, '
            f'actual {final["gap"] * 10.0:.3f} mm.'
        )
    return final, 'direct occurrence transform fallback'


def _place_vise(vise, info, setup, stock, grip, axis, fixed_sign):
    origin, setup_x, setup_y, setup_z = setup.workCoordinateSystem.getAsCoordinateSystem()
    setup_x.normalize(); setup_y.normalize(); setup_z.normalize()

    source_across = info['across'].copy()
    source_clamp = info['clamp'].copy()
    source_up = adsk.core.Vector3D.create(0, 0, 1)

    if axis == 'Y':
        target_axis = setup_y.copy()
        stock_face = stock['max_y'] if fixed_sign == '+' else stock['min_y']
        inward = setup_y.copy()
        if fixed_sign == '+':
            inward.scaleBy(-1)
        anchor = _pt(
            origin,
            _v(setup_x, (stock['min_x'] + stock['max_x']) / 2.0),
            _v(setup_y, stock_face),
            _v(setup_z, stock['min_z'] + grip),
        )
    else:
        target_axis = setup_x.copy()
        stock_face = stock['max_x'] if fixed_sign == '+' else stock['min_x']
        inward = setup_x.copy()
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
        source_across, source_clamp, source_up,
        destination_origin, target_across, inward, target_up,
    ):
        raise RuntimeError('Failed to calculate vise-to-stock transform.')
    vise.transform2 = transform
    adsk.doEvents()

    actual_clamp = info['clamp'].copy()
    actual_clamp.transformBy(transform)
    actual_clamp.normalize()
    target_axis.normalize()
    alignment = abs(actual_clamp.dotProduct(target_axis))
    if alignment < 0.999:
        raise RuntimeError(
            f'Vise orientation verification failed. Requested Setup {axis}, '
            f'actual clamp vector ({actual_clamp.x:.3f}, {actual_clamp.y:.3f}, {actual_clamp.z:.3f}).'
        )
    return transform, anchor, actual_clamp, alignment


