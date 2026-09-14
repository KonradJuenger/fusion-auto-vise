import adsk.core
import adsk.fusion
import adsk.cam
import json
import os

ATTR = 'JK_AutoVise'
PARALLEL_FEATURE_NAME = 'AUTO_VISE_PARALLELS'
SEAT_POINT_NAME = 'AUTO_VISE_PARALLEL_SEAT'
HERE = os.path.dirname(os.path.abspath(__file__))
LIBRARY_PATH = os.path.join(HERE, 'parallels.json')


def load_library():
    with open(LIBRARY_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    items = data.get('parallels', [])
    out = []
    for item in items:
        out.append({
            'name': str(item.get('name') or f'{item["height_mm"]} mm'),
            'height_mm': float(item['height_mm']),
            'thickness_mm': float(item.get('thickness_mm', 4.0)),
            'length_mm': float(item.get('length_mm', 100.0)),
        })
    out.sort(key=lambda x: x['height_mm'])
    return out


def _v(v, k):
    return adsk.core.Vector3D.create(v.x * k, v.y * k, v.z * k)


def _pt(p, *vectors):
    q = adsk.core.Point3D.create(p.x, p.y, p.z)
    for vec in vectors:
        q.translateBy(vec)
    return q


def _managed_parallel_body(body):
    try:
        a = body.attributes.itemByName(ATTR, 'parallel')
        return bool(a and a.value == '1')
    except Exception:
        return False


def cleanup(design, cam=None, lines=None):
    lines = lines if lines is not None else []

    if cam:
        for si in range(cam.setups.count):
            setup = cam.setups.item(si)
            try:
                old = setup.fixtures
                kept = adsk.core.ObjectCollection.create()
                removed = 0
                for i in range(old.count):
                    item = old.item(i)
                    body = adsk.fusion.BRepBody.cast(item)
                    if body and _managed_parallel_body(body):
                        removed += 1
                        continue
                    if getattr(item, 'isValid', True):
                        kept.add(item)
                if removed:
                    setup.fixtures = kept
                    lines.append(f'Parallel cleanup: removed {removed} fixture reference(s) from {setup.name}.')
            except Exception as exc:
                lines.append(f'Parallel fixture cleanup warning for {setup.name}: {exc}')

    root = design.rootComponent

    try:
        bfs = root.features.baseFeatures
        for i in range(bfs.count - 1, -1, -1):
            bf = bfs.item(i)
            managed = False
            try:
                a = bf.attributes.itemByName(ATTR, 'parallel_feature')
                managed = bool(a and a.value == '1')
            except Exception:
                pass
            if managed or bf.name == PARALLEL_FEATURE_NAME:
                try:
                    bf.deleteMe()
                    lines.append('Parallel cleanup: deleted previous base feature.')
                except Exception as exc:
                    lines.append(f'Parallel base feature delete warning: {exc}')
    except Exception as exc:
        lines.append(f'Parallel base-feature cleanup warning: {exc}')

    try:
        bodies = root.bRepBodies
        for i in range(bodies.count - 1, -1, -1):
            body = bodies.item(i)
            if _managed_parallel_body(body):
                try:
                    body.deleteMe()
                except Exception:
                    pass
    except Exception:
        pass


def seat_point(vise, lines=None):
    lines = lines if lines is not None else []
    import autovise_geometry as g

    fixed = g._named(vise, 'fixed jaw')
    if not fixed:
        raise RuntimeError('Could not find child occurrence "fixed jaw".')

    native = fixed.component.constructionPoints.itemByName(SEAT_POINT_NAME)
    if not native:
        names = []
        try:
            cps = fixed.component.constructionPoints
            for i in range(cps.count):
                names.append(cps.item(i).name)
        except Exception:
            pass
        raise RuntimeError(
            f'Vise is missing construction point "{SEAT_POINT_NAME}" in the fixed jaw component. '
            f'Found: {", ".join(names) if names else "<none>"}'
        )

    try:
        proxy = native.createForAssemblyContext(fixed)
    except Exception:
        proxy = None

    cp = proxy or native
    point = cp.geometry
    lines.append(
        f'Parallel seat point {SEAT_POINT_NAME}: '
        f'({point.x:.6f}, {point.y:.6f}, {point.z:.6f}) cm; proxy={proxy is not None}'
    )
    return point, cp


def calculated_grip(vise, info, parallel, stock_height, lines=None):
    lines = lines if lines is not None else []
    point, _ = seat_point(vise, lines)
    height = parallel['height_mm'] / 10.0
    grip = info['jaw_top'] - (point.z + height)

    lines.append(
        f'Parallel {parallel["name"]}: seatZ={point.z*10.0:.3f} mm, '
        f'height={parallel["height_mm"]:.3f} mm, jawTop={info["jaw_top"]*10.0:.3f} mm, '
        f'calculated grip={grip*10.0:.3f} mm.'
    )

    if grip <= 0.0:
        raise RuntimeError(
            f'{parallel["name"]} are too tall for this vise setup. '
            f'Calculated clamping depth is {grip*10.0:.3f} mm.'
        )
    if grip >= stock_height:
        raise RuntimeError(
            f'{parallel["name"]} place the jaw top at or above the stock top. '
            f'Calculated clamping depth {grip*10.0:.3f} mm, '
            f'stock thickness {stock_height*10.0:.3f} mm.'
        )

    return grip


def _create_persisted_bodies(design, temp_bodies, lines):
    root = design.rootComponent
    result = []

    if design.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        bf = root.features.baseFeatures.add()
        if not bf:
            raise RuntimeError('Could not create base feature for parallel geometry.')

        bf.name = PARALLEL_FEATURE_NAME
        try:
            bf.attributes.add(ATTR, 'parallel_feature', '1')
        except Exception:
            pass

        bf.startEdit()
        try:
            for temp in temp_bodies:
                body = root.bRepBodies.add(temp, bf)
                if not body:
                    raise RuntimeError('Could not persist generated parallel body.')
        finally:
            bf.finishEdit()

        try:
            for i in range(bf.bodies.count):
                result.append(bf.bodies.item(i))
        except Exception:
            pass
    else:
        for temp in temp_bodies:
            body = root.bRepBodies.add(temp)
            if not body:
                raise RuntimeError('Could not persist generated parallel body.')
            result.append(body)

    if len(result) < 2:
        raise RuntimeError(
            f'Generated parallel geometry, but Fusion returned only {len(result)} persisted bodies.'
        )

    result[0].name = 'AUTO_PARALLEL_FIXED'
    result[1].name = 'AUTO_PARALLEL_MOVING'

    for body in result:
        try:
            body.attributes.add(ATTR, 'parallel', '1')
        except Exception:
            pass

    lines.append(f'Generated persisted parallel bodies: {len(result)}')
    return result


def generate(design, vise, info, placement, parallel, lines=None):
    lines = lines if lines is not None else []
    seat, _ = seat_point(vise, lines)

    clamp = info['clamp'].copy()
    clamp.transformBy(placement)
    clamp.normalize()

    up = adsk.core.Vector3D.create(0, 0, 1)
    up.transformBy(placement)
    up.normalize()

    across = clamp.crossProduct(up)
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

    fixed_center = _pt(
        seat,
        _v(clamp, thickness / 2.0),
        _v(up, height / 2.0),
    )
    moving_center = _pt(
        seat,
        _v(clamp, gap - thickness / 2.0),
        _v(up, height / 2.0),
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

    bodies = _create_persisted_bodies(design, [temp_fixed, temp_moving], lines)
    lines.append(
        f'Parallel geometry: {parallel["length_mm"]:.1f} x '
        f'{parallel["thickness_mm"]:.1f} x {parallel["height_mm"]:.1f} mm; '
        f'gap={gap*10.0:.3f} mm.'
    )
    return bodies


def add_to_fixtures(setup, vise, parallel_bodies):
    setup.fixtureEnabled = True
    items = adsk.core.ObjectCollection.create()

    try:
        old = setup.fixtures
        for i in range(old.count):
            item = old.item(i)
            occ = adsk.fusion.Occurrence.cast(item)
            body = adsk.fusion.BRepBody.cast(item)

            if occ:
                try:
                    a = occ.attributes.itemByName(ATTR, 'managed')
                    if a and a.value == '1':
                        continue
                except Exception:
                    pass

            if body and _managed_parallel_body(body):
                continue

            if getattr(item, 'isValid', True):
                items.add(item)
    except Exception:
        pass

    items.add(vise)
    for body in parallel_bodies:
        items.add(body)

    setup.fixtures = items
