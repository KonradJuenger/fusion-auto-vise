"""Setup-scoped fitting, fixture geometry and structured measurements."""
import json
import os
import time
import uuid
import adsk
import adsk.core as c
import adsk.fusion as f
import autovise_geometry as stock_api
import autovise_support as defaults
import autovise_model as m
from autovise_rules import evaluate, rectangle_in_polygon

HERE = os.path.dirname(__file__)


def library():
    with open(os.path.join(HERE, 'parallels.json'), encoding='utf-8') as stream:
        rows = json.load(stream)['parallels']
    names = [row['name'] for row in rows]
    if len(names) != len(set(names)):
        raise ValueError('Parallel names must be unique.')
    return rows


def setup_config(setup):
    return m.read(setup, 'configuration', {})


def fixture_uses(entity, occurrence):
    """CAM proxies include a manufacturing wrapper above the design assembly."""
    current = f.Occurrence.cast(entity) or getattr(entity, 'assemblyContext', None)
    target = occurrence.nativeObject or occurrence
    while current:
        if m.same(current.nativeObject or current, target):
            return True
        current = current.assemblyContext
    return False


def configured_vise(design, setup):
    config = setup_config(setup)
    if not config:
        return None
    vise = f.Occurrence.cast(m.resolve(design, config['vise']))
    if not vise or m.read(vise, 'setup_id') != setup.operationId:
        raise ValueError('Saved vise ownership is missing or belongs to another setup.')
    return vise


def frame(setup, stock, origin_units='mm'):
    """Fusion CAM WCS translation is mm in the tested host; BRep/stock are cm.

    The unit choice is explicit and persisted. No best-score or inverse guessing.
    Validate transformed model vertices against the box to reject a wrong choice.
    """
    origin, x, y, z = setup.workCoordinateSystem.getAsCoordinateSystem()
    scale = .1 if origin_units == 'mm' else 1.0
    origin = c.Point3D.create(origin.x * scale, origin.y * scale, origin.z * scale)
    for axis in (x, y, z):
        axis.normalize()
    if abs(x.dotProduct(y)) > 1e-5 or x.crossProduct(y).dotProduct(z) < .999:
        raise ValueError('Setup coordinate system is not a right-handed orthogonal frame.')
    for model in m.items(setup.models):
        bodies = m.items(model.bRepBodies) if f.Occurrence.cast(model) else [model]
        for body in bodies:
            if not f.BRepBody.cast(body):
                raise ValueError('Only BRep model geometry is supported for frame validation.')
            for vertex in m.items(body.vertices):
                delta = origin.vectorTo(vertex.geometry)
                for key, axis in zip(('x', 'y', 'z'), (x, y, z)):
                    value = delta.dotProduct(axis)
                    if value < stock['min_' + key] - .01 or value > stock['max_' + key] + .01:
                        raise ValueError('Setup frame does not contain the model in its stock. Check WCS origin units and stock bounds.')
    return origin, x, y, z


def machine_guard(setup, change_offsets):
    # Fitting is defined in setup coordinates. Machine attachment is only a
    # prerequisite for the optional action that writes Part Position offsets.
    if not change_offsets:
        return False
    attach, _ = defaults._table_attach_status(setup)
    if change_offsets and attach is not True:
        raise ValueError('Select Fusion’s Table Attach Point before changing Part Position offsets.')
    enabled = setup.parameters.itemByName('job_position')
    active = enabled and enabled.value.value and attach is not False
    if active:
        mode = setup.parameters.itemByName('job_positionReference_origin_mode')
        if not mode or mode.value.value == 'fixturePoint':
            selected = setup.parameters.itemByName('job_positionReference_fixture_point')
            objects = getattr(getattr(selected, 'value', None), 'value', [])
            if not objects:
                raise ValueError('Part Position uses a changing fixture bounding box. Select a fixed point on the vise base in Setup > Part Position before fitting.')
    return active


def evaluate_profile(profile, stock, options, parallel):
    d = profile.measure()
    result = evaluate(tuple(stock['size_' + axis] * 10 for axis in ('x', 'y', 'z')),
                      options['axis'], d['jaw_height'] * 10, parallel=parallel,
                      manual_grip=options['grip_mm'], minimum_grip=options['minimum_mm'],
                      jaw_width=d['jaw_width'] * 10, opening_limits=profile.opening_limits_mm())
    width = min(d['jaw_width'], stock['size_y' if options['axis'] == 'X' else 'size_x'])
    if result.grip > 0 and any(not rectangle_in_polygon(polygon,
            d['across_center'] - width / 2, d['across_center'] + width / 2,
            d['jaw_top'] - result.grip / 10, d['jaw_top']) for polygon in d['contact_polygons']):
        from dataclasses import replace
        result = replace(result, reasons=result.reasons + ('The grip band crosses a jaw notch, chamfer, or missing contact area.',))
    return result


def parallel_boxes(profile, parallel):
    d = profile.measure()
    h, t, length = [parallel[key] / 10 for key in ('height_mm', 'thickness_mm', 'length_mm')]
    seat = m.point(d['fixed'], m.vector(d['up'], d['seat_height'] - m.dot(d['fixed'], d['up'])),
                   m.vector(d['across'], d['across_center'] - m.dot(d['fixed'], d['across'])))
    manager = f.TemporaryBRepManager.get()
    return [manager.createBox(c.OrientedBoundingBox3D.create(
        m.point(seat, m.vector(d['clamp'], shift), m.vector(d['up'], h / 2)),
        d['across'], d['clamp'], length, t, h)) for shift in (t / 2, d['gap'] - t / 2)]


def persist_parallels(design, profile, parallel, instance, existing=None):
    # Build the requested world geometry before opening a timeline feature.
    boxes = parallel_boxes(profile, parallel)
    if any(box is None for box in boxes):
        raise ValueError('Could not construct parallel geometry.')
    occ = existing
    if occ:
        if m.read(occ, 'instance') != instance:
            raise ValueError('Saved parallel ownership does not match this setup.')
        if design.designType != f.DesignTypes.ParametricDesignType:
            raise ValueError('Updating existing parallels requires a parametric design.')
        if occ.component.features.baseFeatures.count != 1 or occ.component.bRepBodies.count != 2:
            raise ValueError('Saved parallel geometry was edited. Restore its two-body base feature before fitting.')
        inverse = occ.transform2.copy()
        if not inverse.invert():
            raise ValueError('Could not resolve the parallel component position.')
        manager = f.TemporaryBRepManager.get()
        for box in boxes:
            if not manager.transform(box, inverse):
                raise ValueError('Could not transform parallel geometry.')
    else:
        occ = design.rootComponent.occurrences.addNewComponent(c.Matrix3D.create())
        occ.component.name = 'Auto Vise parallels'
        m.write(occ, 'instance', instance)
    component = occ.component
    feature = None
    if design.designType == f.DesignTypes.ParametricDesignType:
        feature = component.features.baseFeatures.item(0) if existing else component.features.baseFeatures.add()
        if not feature.startEdit():
            raise ValueError('Could not start parallel base feature.')
    try:
        if existing:
            sources = m.items(feature.bodies)
            if len(sources) != 2:
                raise ValueError('Expected two editable parallel source bodies.')
            for source, box in zip(sources, boxes):
                if not feature.updateBody(source, box):
                    raise ValueError('Could not update the existing parallel body.')
        else:
            for box in boxes:
                if not component.bRepBodies.add(box, feature):
                    raise ValueError('Could not create persistent parallel bodies.')
    finally:
        if feature:
            if not feature.finishEdit():
                raise ValueError('Could not finish the parallel base feature.')
    bodies = m.items(component.bRepBodies)
    if len(bodies) != 2:
        raise ValueError('Expected exactly two persistent parallel bodies.')
    for body, name in zip(bodies, ('AUTO_PARALLEL_FIXED', 'AUTO_PARALLEL_MOVING')):
        body.name = name
    occ.isLightBulbOn = True
    return occ


def write_log(report):
    try:
        with open(os.path.join(HERE, 'last_run.json'), 'w', encoding='utf-8') as stream:
            json.dump(report, stream, indent=2, allow_nan=False)
    except OSError:
        c.Application.get().log(json.dumps(report))


def apply(design, cam, setup, profile, options, parallel):
    """Called only from command.execute; Fusion aborts its transaction on failure."""
    started = time.perf_counter()
    report = {'version': 2, 'setup_id': setup.operationId, 'timings_ms': {}}
    vise = profile.vise
    visible = vise.isLightBulbOn
    old_transform = vise.transform2.copy()
    old_slide = profile.motion.slideValue
    old_ground_to_parent = vise.isGroundToParent
    old_grounded = vise.isGrounded
    try:
        owner = m.read(vise, 'setup_id')
        if owner is not None and owner != setup.operationId:
            raise ValueError('This vise is owned by another setup. Insert a separate instance.')
        # Also protect manually shared or legacy fixture references.
        for other in m.items(cam.setups):
            if other.operationId != setup.operationId and any(fixture_uses(x, vise) for x in m.items(other.fixtures)):
                raise ValueError('This vise is used as a fixture in another setup. Insert a separate instance.')
        stock = stock_api._stock(setup)
        target_frame = frame(setup, stock, options['wcs_units'])
        machine_guard(setup, options['change_offsets'])
        result = evaluate_profile(profile, stock, options, parallel)
        if not result.valid:
            raise ValueError('\n'.join(result.reasons))
        report['before'] = {'gap_mm': profile.data['gap'] * 10, 'slide_cm': old_slide,
                            'transform_cm': old_transform.asArray(),
                            'ground_to_parent': old_ground_to_parent, 'grounded': old_grounded}
        vise.isLightBulbOn = False
        # Linked insertion grounds the root occurrence to its insertion frame.
        # Release only this instance; internal jaw constraints remain intact.
        if old_ground_to_parent:
            vise.isGroundToParent = False
        if old_grounded:
            vise.isGrounded = False
        if profile.slope is None:
            profile.calibrate(design)
        report['timings_ms']['configuration'] = (time.perf_counter() - started) * 1000
        profile.drive(design, result.opening / 10)
        target_transform = profile.placement(target_frame, stock, options['axis'], options['side'], result.grip / 10)
        vise.transform2 = target_transform
        adsk.doEvents()
        report['placement'] = {'target': target_transform.asArray(), 'assigned': vise.transform2.asArray(),
                               'grounded': vise.isGrounded, 'ground_to_parent': vise.isGroundToParent}
        # Timeline edits (including a parallel base feature) otherwise discard
        # the occurrence's pending placement.
        if design.designType == f.DesignTypes.ParametricDesignType and design.snapshots.hasPendingSnapshot:
            snapshot = design.snapshots.add()
            if not snapshot:
                raise ValueError('Could not capture the fitted vise position.')
            snapshot.name = 'Auto Vise position'
        report['placement']['captured'] = vise.transform2.asArray()
        d = profile.measure()
        if abs(d['gap'] * 10 - result.opening) > .05:
            raise ValueError(f'Placement changed the measured opening: expected {result.opening:g}, measured {d["gap"] * 10:g} mm.')
        target_clamp = m.vector(target_frame[1 if options['axis'] == 'X' else 2], -1 if options['side'] == '+' else 1)
        if d['clamp'].dotProduct(target_clamp) < .999 or d['up'].dotProduct(target_frame[3]) < .999:
            raise ValueError('Measured placement orientation differs from the selected setup direction.')
        report['timings_ms']['fit'] = (time.perf_counter() - started) * 1000
        old_config = setup_config(setup)
        instance = old_config.get('instance', str(uuid.uuid4()))
        old_parallel = m.resolve(design, old_config['parallels']) if old_config.get('parallels') else None
        if old_parallel and m.read(old_parallel, 'instance') != instance:
            raise ValueError('Saved parallel ownership does not match this setup.')
        if old_parallel:
            for other in m.items(cam.setups):
                if other.operationId != setup.operationId and any(fixture_uses(x, old_parallel) for x in m.items(other.fixtures)):
                    raise ValueError('These parallels are used by another setup. Use separate fixture instances.')
        new_parallel = persist_parallels(design, profile, parallel, instance, old_parallel) if parallel else None
        fixtures = c.ObjectCollection.create()
        for entity in m.items(setup.fixtures):
            if old_parallel and fixture_uses(entity, old_parallel):
                continue
            if not fixture_uses(entity, vise):
                fixtures.add(entity)
        if options['fixture'] or any(fixture_uses(x, vise) for x in m.items(setup.fixtures)):
            fixtures.add(vise)
            if new_parallel:
                fixtures.add(new_parallel)
        if fixtures.count:
            setup.fixtureEnabled = True
        setup.fixtures = fixtures
        # Retain the owned component when switching to manual support. Deleting
        # it can invalidate CAM references; it can be reused on the next fit.
        if old_parallel and not parallel:
            old_parallel.isLightBulbOn = False
        if options['change_offsets']:
            defaults._set_part_position(setup, options['offsets_mm'])
        # Re-read after all geometry and CAM changes, which can recompute the
        # assembly. Never report the earlier, transient placement as success.
        adsk.doEvents()
        d = profile.measure()
        if abs(d['gap'] * 10 - result.opening) > .05:
            raise ValueError('Creating fixtures changed the fitted jaw opening.')
        if any(abs(actual - expected) > 1e-5 for actual, expected in
               zip(vise.transform2.asArray(), target_transform.asArray())):
            raise ValueError('Creating fixtures changed the fitted vise position.')
        m.write(vise, 'setup_id', setup.operationId)
        m.write(vise, 'profile', profile.serialize())
        config = dict(options, instance=instance, vise=vise.entityToken,
                      parallels=(new_parallel or old_parallel).entityToken if (new_parallel or old_parallel) else None,
                      support=parallel['name'] if parallel else 'Manual grip depth')
        # Offsets are a deliberate action, never automatically repeated on reopen.
        config['change_offsets'] = False
        m.write(setup, 'configuration', config)
        report['after'] = {'gap_mm': d['gap'] * 10, 'grip_mm': result.grip,
                           'protrusion_mm': result.protrusion, 'transform_cm': vise.transform2.asArray(),
                           'parallel_bodies': 2 if new_parallel else 0}
        report['status'] = 'passed'
        return report
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = str(exc)
        # Explicitly restore motion too; external assembly joint overrides may not
        # all participate in a failed Fusion command transaction.
        try:
            profile.motion.slideValue = old_slide
            vise.transform2 = old_transform
            vise.isGroundToParent = old_ground_to_parent
            vise.isGrounded = old_grounded
            adsk.doEvents()
        except Exception as restore_error:
            report['restore_error'] = str(restore_error)
        raise
    finally:
        if vise.isValid:
            vise.isLightBulbOn = visible
        report['timings_ms']['total'] = (time.perf_counter() - started) * 1000
        write_log(report)
