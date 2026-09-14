"""V0.14 parallel centering fix.

AUTO_VISE_PARALLEL_SEAT is authoritative for the physical seat height and the
fixed-jaw contact plane, but its lateral/across coordinate does not need to be
at the centre of the gripping face. V0.13 used the complete point, which can
offset a 100 mm parallel laterally when the fixed-jaw component origin is not
centred on the jaw.

This patch keeps the final V0.13 world frame, then projects the seat point onto
the actual fixed-jaw gripping-face centreline along the parallel length axis.
"""
import adsk.core
import adsk.fusion

import autovise_parallels as p
import autovise_v13 as v13

_ORIG_GENERATE = None


def _fmt_point(pt):
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


def _world_contact_center(info, placement):
    """Point on fixed gripping plane and centred across the jaw in root coords."""
    # Native vise axes are orthogonal axis vectors. Build a point whose clamp and
    # across coordinates match the detected fixed gripping face. Z is irrelevant
    # for lateral centering, so use zero and preserve seat Z later.
    q = adsk.core.Point3D.create(0.0, 0.0, 0.0)
    q.translateBy(_v(info['clamp'], info['fixed_inner']))
    q.translateBy(_v(info['across'], info['across_center']))
    q.transformBy(placement)
    return q


def generate_centered(design, vise, info, placement, parallel, lines=None):
    lines = lines if lines is not None else []

    seat_world, _ = v13.world_seat_point(vise, lines)

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

    # The construction point gives the correct seat height and clamp plane, but
    # centre the parallels using the detected fixed gripping face. Only movement
    # along `across` is allowed here, so seat height and jaw-contact depth remain
    # exactly as defined by AUTO_VISE_PARALLEL_SEAT.
    contact_center = _world_contact_center(info, placement)
    to_contact = adsk.core.Vector3D.create(
        contact_center.x - seat_world.x,
        contact_center.y - seat_world.y,
        contact_center.z - seat_world.z,
    )
    lateral_delta = to_contact.dotProduct(across)
    seat_centered = _pt(seat_world, _v(across, lateral_delta))

    height = parallel['height_mm'] / 10.0
    thickness = parallel['thickness_mm'] / 10.0
    length = parallel['length_mm'] / 10.0
    gap = info['gap']

    if gap <= thickness * 2.0:
        raise RuntimeError(
            f'Jaw gap {gap*10.0:.3f} mm is too small for two '
            f'{parallel["thickness_mm"]:.3f} mm-thick parallels.'
        )

    fixed_center = _pt(
        seat_centered,
        _v(clamp, thickness / 2.0),
        _v(up, height / 2.0),
    )
    moving_center = _pt(
        seat_centered,
        _v(clamp, gap - thickness / 2.0),
        _v(up, height / 2.0),
    )

    lines.append('=== V0.14 PARALLEL CENTERING DEBUG ===')
    lines.append(f'Raw seat world={_fmt_point(seat_world)} cm')
    lines.append(f'Fixed gripping-face center reference={_fmt_point(contact_center)} cm')
    lines.append(f'Across/length axis={_fmt_vec(across)}')
    lines.append(f'Lateral seat correction={lateral_delta*10.0:.3f} mm')
    lines.append(f'Centered seat={_fmt_point(seat_centered)} cm')
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

    bodies = p._create_persisted_bodies(design, [temp_fixed, temp_moving], lines)

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
    global _ORIG_GENERATE
    _ORIG_GENERATE = p.generate
    p.generate = generate_centered
