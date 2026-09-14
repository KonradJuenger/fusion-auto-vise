"""V0.17: generate parallels entirely by script in the vise native frame.

This removes the fragile world-space reconstruction used by earlier parallel
versions. AUTO_VISE_PARALLEL_SEAT contributes only the seat height in the vise
root coordinate system. X/Y placement comes from the detected gripping face,
across centre and slider gap.

The two parallel boxes are built as TemporaryBRep bodies in native vise
coordinates, transformed with the exact final vise placement matrix, and shown
as Custom Graphics. They are intentionally not persisted as Design bodies and
are not added to CAM fixtures, so they cannot change Fusion Part Position or
fixture-box references.
"""
import adsk.core
import adsk.fusion

import autovise_geometry as g
import autovise_parallels as p

_ORIG_CALCULATED_GRIP = None
_ORIG_GENERATE = None
_ORIG_CLEANUP = None
_graphics_groups = []


def _fmt_pt(pt):
    return f'({pt.x:.6f}, {pt.y:.6f}, {pt.z:.6f})'


def _fmt_vec(v):
    return f'({v.x:.6f}, {v.y:.6f}, {v.z:.6f})'


def _v(v, k):
    return adsk.core.Vector3D.create(v.x * k, v.y * k, v.z * k)


def _pt(p0, *vectors):
    q = adsk.core.Point3D.create(p0.x, p0.y, p0.z)
    for vec in vectors:
        q.translateBy(vec)
    return q


def _clear_graphics(lines=None):
    global _graphics_groups
    lines = lines if lines is not None else []
    for group in _graphics_groups:
        try:
            group.deleteMe()
        except Exception:
            pass
    if _graphics_groups:
        lines.append(f'V0.17: removed {len(_graphics_groups)} previous parallel graphics group(s).')
    _graphics_groups = []


def cleanup_native_graphics(design, cam=None, lines=None):
    lines = lines if lines is not None else []
    _clear_graphics(lines)
    return _ORIG_CLEANUP(design, cam, lines)


def _seat_in_vise_coordinates(vise, lines=None):
    """Return AUTO_VISE_PARALLEL_SEAT expressed in the vise root coordinate system."""
    lines = lines if lines is not None else []
    fixed = g._named(vise, 'fixed jaw')
    if not fixed:
        raise RuntimeError('Could not find child occurrence "fixed jaw".')

    cp = fixed.component.constructionPoints.itemByName(p.SEAT_POINT_NAME)
    if not cp:
        raise RuntimeError(
            f'Vise is missing construction point "{p.SEAT_POINT_NAME}" in the fixed jaw component.'
        )

    component_point = cp.geometry
    root_point = adsk.core.Point3D.create(
        component_point.x, component_point.y, component_point.z
    )
    root_point.transformBy(fixed.transform2)

    inverse_vise = vise.transform2.copy()
    if not inverse_vise.invert():
        raise RuntimeError('Could not invert vise transform while resolving parallel seat height.')
    local_point = adsk.core.Point3D.create(root_point.x, root_point.y, root_point.z)
    local_point.transformBy(inverse_vise)

    lines.append('=== V0.17 NATIVE PARALLEL DATUM ===')
    lines.append(f'Construction point in fixed-jaw component={_fmt_pt(component_point)} cm')
    lines.append(f'Construction point in design root={_fmt_pt(root_point)} cm')
    lines.append(f'Construction point in VISE LOCAL={_fmt_pt(local_point)} cm')
    lines.append(
        f'Only local Z={local_point.z * 10.0:.3f} mm is used; local X/Y are ignored.'
    )
    return local_point


def calculated_grip_native(vise, info, parallel, stock_height, lines=None):
    lines = lines if lines is not None else []
    seat_local = _seat_in_vise_coordinates(vise, lines)
    height = parallel['height_mm'] / 10.0
    grip = info['jaw_top'] - (seat_local.z + height)

    lines.append(
        f'Parallel {parallel["name"]}: native seatZ={seat_local.z * 10.0:.3f} mm, '
        f'height={parallel["height_mm"]:.3f} mm, '
        f'native jawTop={info["jaw_top"] * 10.0:.3f} mm, '
        f'calculated grip={grip * 10.0:.3f} mm.'
    )

    if grip <= 0.0:
        raise RuntimeError(
            f'{parallel["name"]} are too tall for this vise setup. '
            f'Calculated clamping depth is {grip * 10.0:.3f} mm.'
        )
    if grip >= stock_height:
        raise RuntimeError(
            f'{parallel["name"]} place the jaw top at or above the stock top. '
            f'Calculated clamping depth {grip * 10.0:.3f} mm, '
            f'stock thickness {stock_height * 10.0:.3f} mm.'
        )
    return grip


def generate_native_graphics(design, vise, info, placement, parallel, lines=None):
    """Create both parallels in native vise space, then apply the vise transform once."""
    global _graphics_groups
    lines = lines if lines is not None else []

    seat_local = _seat_in_vise_coordinates(vise, lines)

    clamp = info['clamp'].copy(); clamp.normalize()
    across = info['across'].copy(); across.normalize()
    up = adsk.core.Vector3D.create(0, 0, 1)

    height = parallel['height_mm'] / 10.0
    thickness = parallel['thickness_mm'] / 10.0
    length = parallel['length_mm'] / 10.0
    gap = info['gap']

    if gap <= thickness * 2.0:
        raise RuntimeError(
            f'Jaw gap {gap * 10.0:.3f} mm is too small for two '
            f'{parallel["thickness_mm"]:.3f} mm-thick parallels.'
        )

    # Native datum point: gripping plane + gripping-face lateral centre + seat Z.
    native_seat = adsk.core.Point3D.create(0, 0, 0)
    native_seat.translateBy(_v(clamp, info['fixed_inner']))
    native_seat.translateBy(_v(across, info['across_center']))
    native_seat.translateBy(_v(up, seat_local.z))

    fixed_center_native = _pt(
        native_seat,
        _v(clamp, thickness / 2.0),
        _v(up, height / 2.0),
    )
    moving_center_native = _pt(
        native_seat,
        _v(clamp, gap - thickness / 2.0),
        _v(up, height / 2.0),
    )

    lines.append('=== V0.17 SCRIPT-GENERATED PARALLELS ===')
    lines.append(
        f'Native axes length/across={_fmt_vec(across)}, '
        f'thickness/clamp={_fmt_vec(clamp)}, height/up={_fmt_vec(up)}'
    )
    lines.append(f'Native seat used={_fmt_pt(native_seat)} cm')
    lines.append(
        f'Native centers fixed={_fmt_pt(fixed_center_native)}, '
        f'moving={_fmt_pt(moving_center_native)} cm'
    )
    lines.append('Both boxes will receive the exact final vise.transform2 matrix once.')

    tbm = adsk.fusion.TemporaryBRepManager.get()
    fixed_obb = adsk.core.OrientedBoundingBox3D.create(
        fixed_center_native, across, clamp, length, thickness, height
    )
    moving_obb = adsk.core.OrientedBoundingBox3D.create(
        moving_center_native, across, clamp, length, thickness, height
    )
    fixed_body = tbm.createBox(fixed_obb)
    moving_body = tbm.createBox(moving_obb)
    if not fixed_body or not moving_body:
        raise RuntimeError('Could not create temporary parallel geometry.')

    final_transform = vise.transform2.copy()
    # Prefer the actual occurrence transform over the passed matrix. V0.15 may
    # have corrected the occurrence after the initial placement calculation.
    if not tbm.transform(fixed_body, final_transform):
        raise RuntimeError('Could not transform fixed parallel with final vise transform.')
    if not tbm.transform(moving_body, final_transform):
        raise RuntimeError('Could not transform moving parallel with final vise transform.')

    group = design.rootComponent.customGraphicsGroups.add()
    _graphics_groups.append(group)
    fixed_graphic = group.addBRepBody(fixed_body)
    moving_graphic = group.addBRepBody(moving_body)
    try:
        fixed_graphic.setOpacity(0.75, True)
        moving_graphic.setOpacity(0.75, True)
    except Exception:
        pass

    try:
        fbb = fixed_body.preciseBoundingBox
        mbb = moving_body.preciseBoundingBox
        lines.append(
            f'Final fixed parallel bbox={_fmt_pt(fbb.minPoint)}..{_fmt_pt(fbb.maxPoint)} cm'
        )
        lines.append(
            f'Final moving parallel bbox={_fmt_pt(mbb.minPoint)}..{_fmt_pt(mbb.maxPoint)} cm'
        )
    except Exception as exc:
        lines.append(f'Final parallel bbox debug failed: {exc}')

    lines.append(
        f'Generated script-only parallel graphics: {parallel["length_mm"]:.1f} x '
        f'{parallel["thickness_mm"]:.1f} x {parallel["height_mm"]:.1f} mm. '
        'No Design bodies and no CAM fixture references were created.'
    )

    # Returning an empty list makes autovise_impl keep the vise as the only CAM fixture.
    return []


def apply_patches():
    global _ORIG_CALCULATED_GRIP, _ORIG_GENERATE, _ORIG_CLEANUP
    _ORIG_CALCULATED_GRIP = p.calculated_grip
    _ORIG_GENERATE = p.generate
    _ORIG_CLEANUP = p.cleanup
    p.calculated_grip = calculated_grip_native
    p.generate = generate_native_graphics
    p.cleanup = cleanup_native_graphics
