"""V0.15: verify and correct vise orientation from the actual placed jaw geometry.

Earlier orientation verification transformed the same theoretical clamp vector with the
same placement matrix used to create the transform. That can report perfect alignment
even when the linked vise occurrence/joint geometry ends up in a different direction.

V0.15 measures the fixed-jaw -> movable-jaw direction after placement. If it is not
aligned with the requested Setup X/Y clamp direction, it applies a root-space correction
about the requested fixed-jaw anchor, recentres the physical gripping face on that anchor,
and verifies the measured geometry again.
"""
import adsk.core

import autovise_geometry as g
import autovise_v09 as v09

_ORIG_PLACE = None


def _fmt_vec(v):
    return f'({v.x:.6f},{v.y:.6f},{v.z:.6f})'


def _fmt_pt(p):
    return f'({p.x:.6f},{p.y:.6f},{p.z:.6f})'


def _center(bb):
    return adsk.core.Point3D.create(
        (bb.minPoint.x + bb.maxPoint.x) * 0.5,
        (bb.minPoint.y + bb.maxPoint.y) * 0.5,
        (bb.minPoint.z + bb.maxPoint.z) * 0.5,
    )


def _contact_face(vise, info):
    fixed = g._named(vise, 'fixed jaw')
    if not fixed:
        raise RuntimeError('Could not find fixed jaw after placement.')
    try:
        body = fixed.bRepBodies.item(info['contact_body'])
        return body.faces.item(info['contact_face'])
    except Exception as exc:
        raise RuntimeError(f'Could not resolve placed fixed gripping face: {exc}')


def _measured_clamp(vise, info, setup_z):
    """Measure jaw opening direction from placed B-Rep geometry in root coordinates."""
    fixed_face = _contact_face(vise, info)
    moving = g._named(vise, 'movable jaw')
    if not moving:
        raise RuntimeError('Could not find movable jaw after placement.')

    fbb = fixed_face.boundingBox
    mbb = moving.preciseBoundingBox
    fc = _center(fbb)
    mc = _center(mbb)

    vec = adsk.core.Vector3D.create(mc.x - fc.x, mc.y - fc.y, mc.z - fc.z)
    # Remove the vertical component so different jaw heights do not affect orientation.
    vertical = setup_z.copy()
    vertical.normalize()
    amount = vec.dotProduct(vertical)
    correction = vertical.copy()
    correction.scaleBy(amount)
    vec.subtract(correction)
    if vec.length < 1e-6:
        raise RuntimeError('Measured fixed-to-moving jaw vector is too small to determine orientation.')
    vec.normalize()
    return vec, fc, mc, fbb


def _target_frame(setup, stock, axis, fixed_sign):
    frame = v09.resolve_frame(setup, stock)
    sx = frame['x'].copy(); sx.normalize()
    sy = frame['y'].copy(); sy.normalize()
    sz = frame['z'].copy(); sz.normalize()
    target = sy if axis == 'Y' else sx
    target = target.copy()
    # The physical opening direction runs from fixed jaw into the stock / toward moving jaw.
    # For a fixed jaw on the + side this is the negative setup axis.
    if fixed_sign == '+':
        target.scaleBy(-1)
    target.normalize()
    across = target.crossProduct(sz)
    if across.length < 1e-9:
        raise RuntimeError('Could not derive target vise across axis.')
    across.normalize()
    return frame, target, across, sz


def _physical_anchor(vise, info, target_across, target_up):
    """Approximate the actual gripping-face top/centre point from placed face geometry."""
    face = _contact_face(vise, info)
    bb = face.boundingBox
    c = _center(bb)

    # For the current 3-axis workflow setup Z is vertical. Use the highest point of the
    # gripping-face bbox while keeping the lateral centre of the face.
    # This is only used to remove any translation introduced by a corrective rotation.
    return adsk.core.Point3D.create(c.x, c.y, bb.maxPoint.z)


def _apply_root_correction(vise, correction):
    """Apply a root-space correction to an occurrence transform without relying on matrix order."""
    current = vise.transform2.copy()
    o, x, y, z = current.getAsCoordinateSystem()
    o.transformBy(correction)
    x.transformBy(correction)
    y.transformBy(correction)
    z.transformBy(correction)
    result = adsk.core.Matrix3D.create()
    result.setWithCoordinateSystem(o, x, y, z)
    vise.transform2 = result
    adsk.doEvents()
    return result


def _translate_to_anchor(vise, info, anchor, target_across, target_up):
    actual_anchor = _physical_anchor(vise, info, target_across, target_up)
    delta = adsk.core.Vector3D.create(
        anchor.x - actual_anchor.x,
        anchor.y - actual_anchor.y,
        anchor.z - actual_anchor.z,
    )
    m = vise.transform2.copy()
    t = m.translation
    t.x += delta.x; t.y += delta.y; t.z += delta.z
    m.translation = t
    vise.transform2 = m
    adsk.doEvents()
    return m, actual_anchor, delta


def measured_place_vise(vise, info, setup, stock, grip, axis, fixed_sign):
    # First use the existing resolved-frame placement. This gives us the correct stock anchor.
    placement, anchor, theoretical_clamp, _ = _ORIG_PLACE(
        vise, info, setup, stock, grip, axis, fixed_sign
    )

    frame, target_clamp, target_across, target_up = _target_frame(
        setup, stock, axis, fixed_sign
    )
    measured, fixed_center, moving_center, _ = _measured_clamp(vise, info, target_up)
    signed = measured.dotProduct(target_clamp)
    alignment = abs(signed)

    v09.DEEP_ROWS.append('')
    v09.DEEP_ROWS.append('=== V0.15 MEASURED ORIENTATION DEBUG ===')
    v09.DEEP_ROWS.append(f'Requested clamp Setup {axis}, fixed side {fixed_sign}')
    v09.DEEP_ROWS.append(f'Target physical clamp={_fmt_vec(target_clamp)}')
    v09.DEEP_ROWS.append(f'Theoretical transformed clamp={_fmt_vec(theoretical_clamp)}')
    v09.DEEP_ROWS.append(f'Measured fixed->moving clamp={_fmt_vec(measured)}')
    v09.DEEP_ROWS.append(f'Fixed face center={_fmt_pt(fixed_center)} moving jaw center={_fmt_pt(moving_center)}')
    v09.DEEP_ROWS.append(f'Measured signed alignment={signed:.6f}, abs={alignment:.6f}')

    # A sign error means the moving jaw is on the wrong side of the fixed jaw, which is also
    # incorrect even if the axis is parallel.
    if signed < 0.995:
        measured_across = measured.crossProduct(target_up)
        if measured_across.length < 1e-9:
            raise RuntimeError('Could not derive measured vise across axis for correction.')
        measured_across.normalize()

        correction = adsk.core.Matrix3D.create()
        ok = correction.setToAlignCoordinateSystems(
            anchor,
            measured_across, measured, target_up,
            anchor,
            target_across, target_clamp, target_up,
        )
        if not ok:
            raise RuntimeError('Failed to calculate measured vise orientation correction.')

        placement = _apply_root_correction(vise, correction)
        placement, actual_anchor, delta = _translate_to_anchor(
            vise, info, anchor, target_across, target_up
        )
        v09.DEEP_ROWS.append('Applied measured-geometry orientation correction.')
        v09.DEEP_ROWS.append(
            f'Anchor before recenter={_fmt_pt(actual_anchor)}, target={_fmt_pt(anchor)}, '
            f'translation correction={_fmt_vec(delta)}'
        )

        measured, fixed_center, moving_center, _ = _measured_clamp(vise, info, target_up)
        signed = measured.dotProduct(target_clamp)
        alignment = abs(signed)
        v09.DEEP_ROWS.append(f'Measured clamp AFTER correction={_fmt_vec(measured)}')
        v09.DEEP_ROWS.append(f'Alignment AFTER correction signed={signed:.6f}, abs={alignment:.6f}')

    if signed < 0.995:
        raise RuntimeError(
            f'Physical vise orientation is still wrong after correction. Requested Setup {axis}; '
            f'measured clamp {_fmt_vec(measured)}.'
        )

    # Return the actual final occurrence transform so parallel generation inherits the corrected frame.
    final = vise.transform2.copy()
    return final, anchor, measured, alignment


def apply_patches():
    global _ORIG_PLACE
    _ORIG_PLACE = g._place_vise
    g._place_vise = measured_place_vise
