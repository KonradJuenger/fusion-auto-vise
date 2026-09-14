"""V0.11 fixes for Auto Vise.

Key fixes:
- stable fixed-jaw gripping-face detection, including a zero-gap/closed-jaw state;
- drive the slider through an occurrence-specific joint proxy where possible;
- force a real reset before target drive to avoid stale cached joint values;
- run CAM/design frame diagnostics before jaw adjustment so failures still produce useful logs.
"""
import adsk.core
import adsk.fusion

import autovise_geometry as g
import autovise_v09 as v09

_ORIG_STOCK = None
_ORIG_SET_JAW = None
_ORIG_VISE_INFO = None


def _safe_name(obj):
    try:
        return obj.name or '<unnamed>'
    except Exception:
        return '<unnamed>'


def _context_name(obj):
    try:
        ctx = obj.assemblyContext
        return _safe_name(ctx) if ctx else '<native/no context>'
    except Exception:
        return '<unavailable>'


def _fixed_face_candidates_stable(fixed, moving_bb, clamp, across):
    """Find plausible fixed gripping faces, allowing a closed (zero-gap) state."""
    moving_inner, _ = g._axis_range(moving_bb, clamp)
    across0, across1 = g._axis_range(moving_bb, across)
    moving_width = max(across1 - across0, 1e-9)
    moving_height = max(moving_bb.maxPoint.z - moving_bb.minPoint.z, 1e-9)

    candidates = []
    for bi in range(fixed.bRepBodies.count):
        body = fixed.bRepBodies.item(bi)
        for fi in range(body.faces.count):
            face = body.faces.item(fi)
            try:
                bb = face.boundingBox
                c0, c1 = g._axis_range(bb, clamp)
                if c1 - c0 > 0.001:
                    continue

                position = (c0 + c1) / 2.0
                if position > moving_inner + 0.001:
                    continue

                across_overlap = g._overlap(bb, moving_bb, across)
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
                    bi,
                    fi,
                    across_overlap,
                    z_overlap,
                ))
            except Exception:
                pass

    candidates.sort(key=lambda item: (item[0], item[5] * max(item[6], 1e-9)), reverse=True)
    return candidates


def stable_vise_info(vise):
    fixed = g._named(vise, 'fixed jaw')
    moving = g._named(vise, 'movable jaw')
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
        if across.length < 1e-9:
            continue
        across.normalize()

        candidates = _fixed_face_candidates_stable(fixed, moving_bb, clamp, across)
        if not candidates:
            attempts.append(f'{label}: no gripping-face candidates')
            continue

        chosen = candidates[0]
        fixed_inner, _, contact_bb, bi, fi, across_overlap, z_overlap = chosen
        moving_inner, _ = g._axis_range(moving_bb, clamp)
        gap = moving_inner - fixed_inner

        if gap < -0.001:
            attempts.append(f'{label}: candidate gives negative gap={gap * 10.0:.3f} mm')
            continue

        a0, a1 = g._axis_range(contact_bb, across)
        solution = {
            'fixed': fixed,
            'moving': moving,
            'clamp': clamp,
            'across': across,
            'native_clamp_label': label,
            'fixed_inner': fixed_inner,
            'moving_inner': moving_inner,
            'gap': max(0.0, gap),
            'across_center': (a0 + a1) / 2.0,
            'jaw_top': contact_bb.maxPoint.z,
            'contact_bb': contact_bb,
            'contact_body': bi,
            'contact_face': fi,
            'candidates': candidates,
        }

        score = across_overlap * max(z_overlap, 1e-6) * 1000.0 - abs(gap)
        solutions.append((score, solution))
        attempts.append(
            f'{label}: valid gap={gap * 10.0:.3f} mm body={bi} face={fi} '
            f'fixed={fixed_inner * 10.0:.3f} moving={moving_inner * 10.0:.3f}'
        )

    if not solutions:
        raise RuntimeError('Could not identify fixed jaw gripping face. ' + ' | '.join(attempts))

    solutions.sort(key=lambda item: item[0], reverse=True)
    result = solutions[0][1]
    result['detector_attempts'] = attempts
    return result


def _joint_endpoints(joint):
    parts = []
    for attr in ('occurrenceOne', 'occurrenceTwo'):
        try:
            occ = getattr(joint, attr)
            parts.append(f'{attr}={_safe_name(occ) if occ else "<none>"}')
        except Exception:
            parts.append(f'{attr}=<unavailable>')
    return ', '.join(parts)


def _slider_proxies(vise, moving, lines):
    found = []
    seen = set()

    def add(joint, source, bonus):
        if not joint:
            return
        try:
            motion = adsk.fusion.SliderJointMotion.cast(joint.jointMotion)
            if not motion:
                return
            try:
                token = joint.entityToken
            except Exception:
                token = str(id(joint))
            key = (token, _context_name(joint), source)
            if key in seen:
                return
            seen.add(key)
            score = bonus
            name = _safe_name(joint).lower()
            ends = _joint_endpoints(joint).lower()
            if 'slider' in name:
                score += 5
            if 'jaw' in name:
                score += 5
            if 'movable jaw' in ends:
                score += 20
            if _context_name(joint) != '<native/no context>':
                score += 30
            found.append((score, source, joint, motion))
        except Exception as exc:
            lines.append(f'  slider candidate from {source} failed: {exc}')

    try:
        joints = moving.joints
        lines.append(f'Movable-jaw occurrence.joints count={joints.count}')
        for i in range(joints.count):
            add(joints.item(i), 'moving.joints', 100)
    except Exception as exc:
        lines.append(f'Movable-jaw occurrence.joints failed: {exc}')

    component = vise.component
    for source_name, attr in (('component.joints', 'joints'), ('component.allJoints', 'allJoints')):
        try:
            joints = getattr(component, attr)
            for i in range(joints.count):
                native = joints.item(i)
                try:
                    native_motion = adsk.fusion.SliderJointMotion.cast(native.jointMotion)
                except Exception:
                    native_motion = None
                if not native_motion:
                    continue
                lines.append(
                    f'Native slider {_safe_name(native)} from {source_name}: '
                    f'context={_context_name(native)}, slide={float(native_motion.slideValue)*10.0:.3f} mm, '
                    f'{_joint_endpoints(native)}'
                )
                try:
                    proxy = native.createForAssemblyContext(vise)
                    if proxy:
                        add(proxy, 'createForAssemblyContext(vise)', 80)
                    else:
                        lines.append('  createForAssemblyContext returned null')
                except Exception as exc:
                    lines.append(f'  createForAssemblyContext failed: {exc}')
        except Exception as exc:
            lines.append(f'{source_name} enumeration failed: {exc}')

    found.sort(key=lambda item: item[0], reverse=True)
    lines.append(f'Assembly-context slider candidates={len(found)}')
    for score, source, joint, motion in found:
        try:
            sv = float(motion.slideValue) * 10.0
        except Exception:
            sv = float('nan')
        lines.append(
            f'  score={score} source={source} joint={_safe_name(joint)} '
            f'context={_context_name(joint)} slide={sv:.3f} mm {_joint_endpoints(joint)}'
        )
    return found


def drive_proxy_gap(design, vise, required_gap, lines):
    initial = stable_vise_info(vise)
    moving = initial['moving']

    lines.append('=== V0.11 JOINT DEBUG ===')
    lines.append(
        f'Initial geometric gap={initial["gap"]*10.0:.3f} mm; '
        f'target={required_gap*10.0:.3f} mm; '
        f'fixed={initial["fixed_inner"]*10.0:.3f} mm; '
        f'moving={initial["moving_inner"]*10.0:.3f} mm'
    )

    candidates = _slider_proxies(vise, moving, lines)
    if not candidates:
        lines.append('No slider proxy found; delegating to previous driver.')
        return _ORIG_SET_JAW(design, vise, required_gap, lines)

    failures = []
    for score, source, joint, motion in candidates:
        lines.append(
            f'TRY {source}: joint={_safe_name(joint)} context={_context_name(joint)}'
        )
        try:
            motion.slideValue = 0.0
            adsk.doEvents()
            closed = stable_vise_info(vise)
            lines.append(
                f'  reset 0.000 mm -> geometricGap={closed["gap"]*10.0:.3f} mm; '
                f'fixed={closed["fixed_inner"]*10.0:.3f}; moving={closed["moving_inner"]*10.0:.3f}'
            )

            trials = []
            for target in (required_gap, -required_gap):
                try:
                    pre = required_gap * 0.25
                    if abs(pre - target) < 1e-6:
                        pre = 0.0
                    motion.slideValue = pre
                    adsk.doEvents()
                    motion.slideValue = target
                    adsk.doEvents()

                    info = stable_vise_info(vise)
                    actual = float(motion.slideValue)
                    err = abs(info['gap'] - required_gap)
                    lines.append(
                        f'  target={target*10.0:+.3f} -> actualSlide={actual*10.0:.3f} mm, '
                        f'gap={info["gap"]*10.0:.3f} mm, error={err*10.0:.3f} mm, '
                        f'fixed={info["fixed_inner"]*10.0:.3f}, moving={info["moving_inner"]*10.0:.3f}'
                    )
                    trials.append((err, actual))
                except Exception as exc:
                    lines.append(f'  target {target*10.0:+.3f} failed: {exc}')

            if not trials:
                failures.append(f'{source}: no target trial succeeded')
                continue

            trials.sort(key=lambda item: item[0])
            best_err, best_value = trials[0]

            motion.slideValue = 0.0
            adsk.doEvents()
            motion.slideValue = best_value
            adsk.doEvents()

            final = stable_vise_info(vise)
            final_err = abs(final['gap'] - required_gap)
            lines.append(
                f'  FINAL slide={float(motion.slideValue)*10.0:.3f} mm; '
                f'gap={final["gap"]*10.0:.3f} mm; error={final_err*10.0:.3f} mm'
            )

            if final_err <= 0.005:
                return final, f'assembly-context slider {_safe_name(joint)} via {source}'

            failures.append(
                f'{source}/{_safe_name(joint)} final gap={final["gap"]*10.0:.3f} mm'
            )
        except Exception as exc:
            failures.append(f'{source}/{_safe_name(joint)}: {exc}')

    raise RuntimeError(
        'No slider proxy produced the requested gap. ' + ' | '.join(failures)
    )


def stock_with_early_frame(setup):
    stock = _ORIG_STOCK(setup)
    try:
        v09.resolve_frame(setup, stock)
    except Exception as exc:
        v09.DEEP_ROWS.append(f'Early frame resolution failed: {exc}')
    return stock


def apply_patches():
    global _ORIG_STOCK, _ORIG_SET_JAW, _ORIG_VISE_INFO
    _ORIG_STOCK = g._stock
    _ORIG_SET_JAW = g._set_jaw_gap
    _ORIG_VISE_INFO = g._vise_info

    g._vise_info = stable_vise_info
    g._stock = stock_with_early_frame
    g._set_jaw_gap = drive_proxy_gap
