"""Explicit native master labels, resolved in one inserted instance's context."""
import uuid
import adsk.fusion as f
import autovise_model as m

GROUP = 'JK_AutoViseMaster'
ROLES = dict(fixed='AUTO_VISE_FIXED_GRIP', moving='AUTO_VISE_MOVING_GRIP',
             seat='AUTO_VISE_PARALLEL_SEAT', joint='AUTO_VISE_SLIDER')
HELP = 'Open the original vise file, run Configure Vise Master, save it, then update the linked vise.'


def native(entity):
    return entity.nativeObject or entity


def entities(component, occurrence=None):
    """Yield native objects and their matching proxies, including nested owners.

    Repeated component instances deliberately yield separate candidates: a role
    on their shared native face would otherwise be ambiguous.
    """
    for body in m.items(component.bRepBodies):
        for face in m.items(body.faces):
            yield face, face.createForAssemblyContext(occurrence) if occurrence else face
    for collection in (component.joints, component.asBuiltJoints):
        for joint in m.items(collection):
            yield joint, joint.createForAssemblyContext(occurrence) if occurrence else joint
    children = occurrence.childOccurrences if occurrence else component.occurrences
    for child in m.items(children):
        yield from entities(child.component, child)


def contract(component):
    attr = component.attributes.itemByName(GROUP, 'contract')
    if not attr:
        raise ValueError('Vise master has no Auto Vise roles. ' + HELP)
    import json
    data = json.loads(attr.value)
    if data.get('version') != 1 or not data.get('revision'):
        raise ValueError('Unsupported Auto Vise master contract. ' + HELP)
    return data


def resolve(component, occurrence=None):
    data = contract(component)
    matches = {key: [] for key in ROLES}
    for source, proxy in entities(component, occurrence):
        for key, label in ROLES.items():
            attr = source.attributes.itemByName(GROUP, label)
            if attr:
                if attr.value != data['revision']:
                    raise ValueError(f'{label} has a stale master label. ' + HELP)
                matches[key].append(proxy)
    for key, found in matches.items():
        if len(found) != 1 or found[0] is None:
            raise ValueError(f'{ROLES[key]}: expected exactly one entity, found {len(found)}. ' + HELP)
    selected = {key: found[0] for key, found in matches.items()}
    validate(selected)
    for key in ('fixed', 'moving', 'seat'):
        try:
            m.revalidate(selected[key], data['signatures'][key])
        except (ValueError, KeyError) as exc:
            raise ValueError(f'{ROLES[key]} changed geometry. ' + HELP) from exc
    return selected, data


def validate(selected):
    for key in ROLES:
        if not selected.get(key):
            raise ValueError('Select ' + ROLES[key] + '.')
    for key in ('fixed', 'moving', 'seat'):
        if not f.BRepFace.cast(selected[key]):
            raise ValueError(ROLES[key] + ' must be a planar face.')
    joint = selected['joint']
    if not (f.Joint.cast(joint) or f.AsBuiltJoint.cast(joint)):
        raise ValueError('AUTO_VISE_SLIDER must be a joint.')
    motion = f.SliderJointMotion.cast(joint.jointMotion)
    if not motion:
        raise ValueError('AUTO_VISE_SLIDER must use slider motion.')
    fixed, moving, seat = (selected[key] for key in ('fixed', 'moving', 'seat'))
    if m.same(fixed.assemblyContext, moving.assemblyContext):
        raise ValueError('Fixed and moving gripping faces must belong to separate components.')
    if m.same(moving.assemblyContext, seat.assemblyContext):
        raise ValueError('The parallel seat must be on the stationary part of the vise.')
    fp, clamp, fv = m.face_data(fixed)
    mp, opposing, mv = m.face_data(moving)
    sp, up, _ = m.face_data(seat)
    if clamp.dotProduct(opposing) > -.999 or abs(clamp.dotProduct(up)) > .001:
        raise ValueError('Grip faces must oppose each other and be perpendicular to the seat.')
    if abs(clamp.dotProduct(motion.slideDirectionVector)) < .999:
        raise ValueError('Slider travel must be perpendicular to the gripping faces.')
    if fp.vectorTo(mp).dotProduct(clamp) < -.001:
        raise ValueError('The gripping faces point away from the opening.')
    if min(m.extent(fv, up)[1], m.extent(mv, up)[1]) <= m.dot(sp, up):
        raise ValueError('The support seat must be below both jaw tops.')


def configure(design, selected):
    """Write only during command.execute in the original, editable master."""
    root = design.rootComponent
    for occ in m.items(root.allOccurrences):
        if occ.isReferencedComponent:
            raise ValueError('Configure the original vise file, with local jaw components, not a linked insertion.')
    available = list(entities(root))
    validate(selected)
    for key, entity in selected.items():
        if not any(m.same(entity, proxy) for _, proxy in available):
            raise ValueError(ROLES[key] + ' must belong to this master document.')
        if sum(m.same(native(entity), source) for source, _ in available) != 1:
            raise ValueError(ROLES[key] + ' belongs to a repeated component. Make that component independent first.')
    # Validate everything before replacing labels. Cancel never reaches here.
    revision = str(uuid.uuid4())
    data = dict(version=1, revision=revision,
                signatures={key: m.signature(selected[key]) for key in ('fixed', 'moving', 'seat')})
    for source, _ in available:
        for label in ROLES.values():
            attr = source.attributes.itemByName(GROUP, label)
            if attr and not attr.deleteMe():
                raise ValueError('Could not replace existing master label: ' + label)
    for key, entity in selected.items():
        if not native(entity).attributes.add(GROUP, ROLES[key], revision):
            raise ValueError('Could not write master label: ' + ROLES[key])
    import json
    if not root.attributes.add(GROUP, 'contract', json.dumps(data, allow_nan=False)):
        raise ValueError('Could not save the master contract.')
    resolve(root)
    return data


def profile(vise):
    selected, data = resolve(vise.component, vise)
    saved = m.read(vise, 'profile')
    # Legacy instance selections never override labels. A changed master gets
    # a fresh calibration; unchanged roles reuse only instance-local motion data.
    if saved and saved.get('master_revision') != data['revision']:
        saved = None
    result = m.Profile(vise, selected, saved)
    result.master_revision = data['revision']
    return result
