"""V0.13 coordinate-space fix for generated parallels.

V0.12 mixed a native/component-space construction point with root/world-space
clamp vectors after the vise had been transformed. This patch resolves
AUTO_VISE_PARALLEL_SEAT by transforming its native geometry through the actual
fixed-jaw occurrence transform, then generates both parallel bodies entirely in
the final placed vise frame.
"""
import adsk.core
import adsk.fusion

import autovise_geometry as g
import autovise_parallels as p

_ORIG_SEAT_POINT = None
_ORIG_GENERATE = None


def _fmt_point(pt):
    return f'({pt.x:.6f}, {pt.y:.6f}, {pt.z:.6f})'


def _fmt_vec(v):
    return f'({v.x:.6f}, {v.y:.6f}, {v.z:.6f})'


def _fmt_mat(m):
    try:
        return '[' + ', '.join(f'{x:.6f}' for x in m.asArray()) + ']'
    except Exception:
        return '<unavailable>'


def world_seat_point(vise, lines=None):
    """Return AUTO_VISE_PARALLEL_SEAT in root/design coordinates."""
    lines = lines if lines is not None else []

    fixed = g._named(vise, 'fixed jaw')
    if not fixed:
        raise RuntimeError('Could not find child occurrence "fixed jaw".')

    native = fixed.component.constructionPoints.itemByName(p.SEAT_POINT_NAME)
    if not native:
        names = []
        try:
            cps = fixed.component.constructionPoints
            for i in range(cps.count):
                names.append(cps.item(i).name)
        except Exception:
            pass
        raise RuntimeError(
            f'Vise is missing construction point "{p.SEAT_POINT_NAME}" in the fixed jaw component. '
            f'Found: {", ".join(names) if names else "<none>"}'
        )

    native_point = native.geometry
    world_point = adsk.core.Point3D.create(
        native_point.x, native_point.y, native_point.z
    )
    fixed_transform = fixed.transform2.copy()
    world_point.transformBy(fixed_transform)

    proxy_point = None
    try:
        proxy = native.createForAssemblyContext(fixed)
        if proxy:
            proxy_point = proxy.geometry
    except Exception:
        proxy = None

    lines.append(
        f'Parallel seat native {p.SEAT_POINT_NAME}: {_fmt_point(native_point)} cm'
    )
    if proxy_point:
        lines.append(
            f'Parallel seat assembly-proxy geometry: {_fmt_point(proxy_point)} cm '
            f'(diagnostic only)'
        )
    lines.append(f'Fixed jaw transform2: {_fmt_mat(fixed_transform)}')
    lines.append(
        f'Parallel seat ROOT/WORLD: {_fmt_point(world_point)} cm '
        f'(native transformed by fixed-jaw occurrence)'
    )

    return world_point, native


def generate_world_aligned(design, vise, info, placement, parallel, lines=None):
    """Generate parallel bodies entirely in the final root/world vise frame."""
    lines = lines if lines is not None else []

    seat, _ = world_seat_point(vise, lines)

    clamp = info['clamp'].copy()
    clamp.transformBy(placement)
    clamp.normalize()

    up = adsk.core.Vector3D.create(0, 0, 1)
    up.transformBy(placement)
    up.normalize()

    across = clamp.crossProduct(up)
    if across.length < 1e-9:
        raise RuntimeError('Could not derive parallel length direction from vise clamp/up axes.')
    across.normalize()

    height = parallel['height_mm'] / 10.0
    thickness = parallel['thickness_mm'] / 10.0
    length = parallel['length_mm'] / 10.0
    gap = info['gap']

    if gap <= thickness * 2.0:
        raise RuntimeError(
            f'Jaw gap {gap*10.0:.3f} mm is too small for two '
            f'{parallel["thickness_mm"]:.3f} mm-thick parallels.'
        )

    fixed_center = p._pt(
        seat,
        p._v(clamp, thickness / 2.0),
        p._v(up, height / 2.0),
    )
    moving_center = p._pt(
        seat,
        p._v(clamp, gap - thickness / 2.0),
        p._v(up, height / 2.0),
    )

    lines.append('=== V0.13 PARALLEL FRAME DEBUG ===')
    lines.append(f'Parallel seat world={_fmt_point(seat)} cm')
    lines.append(
        f'Parallel axes: length/across={_fmt_vec(across)}, '
        f'thickness/clamp={_fmt_vec(clamp)}, height/up={_fmt_vec(up)}'
    )
    lines.append(
        f'Parallel centers: fixed={_fmt_point(fixed_center)}, '
        f'moving={_fmt_point(moving_center)} cm'
    )

    tbm = adsk.fusion.TemporaryBRepManager.get()
    fixed_box = adsk.core.OrientedBoundingBox3D.create(
        fixed_center, across, clamp, length, thickness, height
    )
    moving_box = adsk.core.OrientedBoundingBox3D.create(
        moving_center, across, clamp, length, thickness, height
    )

    temp_fixed = tbm.createBox(fixed_box)
    temp_moving = tbm.createBox(moving_box)
    if not temp_fixed or not temp_moving:
        raise RuntimeError('Could not generate parallel box geometry.')

    bodies = p._create_persisted_bodies(
        design, [temp_fixed, temp_moving], lines
    )

    for body in bodies:
        try:
            bb = body.preciseBoundingBox
            lines.append(
                f'{body.name} root bbox: '
                f'{_fmt_point(bb.minPoint)}..{_fmt_point(bb.maxPoint)}'
            )
        except Exception as exc:
            lines.append(f'{body.name} bbox read failed: {exc}')

    lines.append(
        f'Parallel geometry: {parallel["length_mm"]:.1f} x '
        f'{parallel["thickness_mm"]:.1f} x {parallel["height_mm"]:.1f} mm; '
        f'gap={gap*10.0:.3f} mm.'
    )
    return bodies


def apply_patches():
    global _ORIG_SEAT_POINT, _ORIG_GENERATE
    _ORIG_SEAT_POINT = p.seat_point
    _ORIG_GENERATE = p.generate
    p.seat_point = world_seat_point
    p.generate = generate_world_aligned
