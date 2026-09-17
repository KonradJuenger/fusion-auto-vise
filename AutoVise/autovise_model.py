"""Fusion geometry boundary. Lengths inside this module are centimetres.

All selected faces and joints are assembly-context proxies in the active design.
The profile uses entity tokens (resolved, never compared) and face signatures.
No names, face indexes, axis guesses, or native-component motion writes.
"""
import json
import adsk
import adsk.core as c
import adsk.fusion as f
from autovise_rules import slider_target, gap_limits

GROUP = 'JK_AutoViseV2'


def items(collection):
    return [collection.item(i) for i in range(collection.count)]


def read(owner, name, default=None):
    attr = owner.attributes.itemByName(GROUP, name)
    return json.loads(attr.value) if attr else default


def write(owner, name, value):
    owner.attributes.add(GROUP, name, json.dumps(value, allow_nan=False))


def resolve(design, token):
    found = design.findEntityByToken(token) if token else []
    if len(found) != 1 or not found[0].isValid:
        raise ValueError('A saved selection is missing or split. Select that geometry again.')
    return found[0]


def root_occurrence(entity):
    occ = f.Occurrence.cast(entity) or getattr(entity, 'assemblyContext', None)
    while occ and occ.assemblyContext:
        occ = occ.assemblyContext
    return occ


def same(a, b):
    return a == b


def vector(v, scale=1):
    return c.Vector3D.create(v.x * scale, v.y * scale, v.z * scale)


def point(p, *deltas):
    out = p.copy()
    for delta in deltas:
        out.translateBy(delta)
    return out


def dot(p, v):
    return p.x * v.x + p.y * v.y + p.z * v.z


def face_data(face):
    plane = c.Plane.cast(face.geometry)
    if not plane:
        raise ValueError('Select a planar face.')
    if face.loops.count != 1 or face.vertices.count < 3:
        raise ValueError('Select a planar face without holes or curved boundaries.')
    vertices = []
    for coedge in items(face.loops.item(0).coEdges):
        edge = coedge.edge
        if not c.Line3D.cast(edge.geometry):
            raise ValueError('Curved gripping or seat boundaries are not supported.')
        vertices.append((edge.endVertex if coedge.isOpposedToEdge else edge.startVertex).geometry)
    normal = plane.normal.copy()
    normal.normalize()
    if face.isParamReversed:
        normal.scaleBy(-1)
    origin = face.pointOnFace
    return origin, normal, vertices


def extent(vertices, direction):
    values = [dot(p, direction) for p in vertices]
    return min(values), max(values)


def signature(face):
    _, _, vertices = face_data(face)
    # Pairwise distances are invariant under jaw and vise transformations.
    distances = sorted(vertices[i].distanceTo(vertices[j]) for i in range(len(vertices)) for j in range(i))
    return [face.area] + distances


def revalidate(face, saved):
    current = signature(face)
    if len(current) != len(saved) or any(abs(a - b) > max(1e-5, abs(b) * 1e-4)
                                        for a, b in zip(current, saved)):
        raise ValueError('A configured face changed shape. Reselect the faces to confirm the new geometry.')


def descendants(vise):
    result = []
    def visit(occ):
        result.append(occ)
        for child in items(occ.childOccurrences):
            visit(child)
    visit(vise)
    return result


def sliders(vise):
    result = []
    for owner in descendants(vise):
        for collection_name in ('joints', 'asBuiltJoints'):
            for native in items(getattr(owner.component, collection_name)):
                if f.SliderJointMotion.cast(native.jointMotion):
                    proxy = native.createForAssemblyContext(owner)
                    if proxy and not any(same(proxy, existing) for existing in result):
                        result.append(proxy)
    return result


class Profile:
    def __init__(self, vise, selected, saved=None):
        self.vise = vise
        self.entities = selected
        for key in ('fixed', 'moving', 'seat', 'joint'):
            if not selected.get(key):
                raise ValueError(f'Select the {key} ' + ('slider joint.' if key == 'joint' else 'face.'))
            if not same(root_occurrence(selected[key]), vise):
                raise ValueError(f'The {key} selection must belong to the selected vise instance.')
        if same(selected['fixed'].assemblyContext, selected['moving'].assemblyContext):
            raise ValueError('Jaws must be separate components; fused solids are unsupported.')
        if same(selected['moving'].assemblyContext, selected['seat'].assemblyContext):
            raise ValueError('Select a support seat on the stationary part of the vise.')
        self.motion = f.SliderJointMotion.cast(selected['joint'].jointMotion)
        if not self.motion or not selected['joint'].assemblyContext:
            raise ValueError('Select an assembly-context slider joint.')
        if saved:
            for key in ('fixed', 'moving', 'seat'):
                revalidate(selected[key], saved['signatures'][key])
        self.slope = saved.get('slope') if saved else None
        self.measure()

    def measure(self):
        fp, clamp, fv = face_data(self.entities['fixed'])
        mp, moving_normal, mv = face_data(self.entities['moving'])
        sp, up, sv = face_data(self.entities['seat'])
        if clamp.dotProduct(moving_normal) > -.999 or abs(clamp.dotProduct(up)) > .001:
            raise ValueError('Gripping faces must oppose each other and be perpendicular to the support seat.')
        across = clamp.crossProduct(up)
        across.normalize()
        if abs(clamp.dotProduct(self.motion.slideDirectionVector)) < .999:
            raise ValueError('Slider travel must be perpendicular to the gripping faces.')
        gap = fp.vectorTo(mp).dotProduct(clamp)
        if gap < -.001:
            raise ValueError('Selected gripping faces point away from the jaw opening.')
        fz, mz = extent(fv, up), extent(mv, up)
        fx, mx = extent(fv, across), extent(mv, across)
        top = min(fz[1], mz[1])
        seat = dot(sp, up)
        width = min(fx[1], mx[1]) - max(fx[0], mx[0])
        if top <= seat or width <= .001:
            raise ValueError('No common rectangular gripping area above the selected seat.')
        self.data = dict(fixed=fp, clamp=clamp, up=up, across=across, gap=max(0, gap),
                         jaw_height=top - seat, jaw_top=top, seat_height=seat,
                         jaw_width=width, jaw_bottom=max(fz[0], mz[0]),
                         across_center=(max(fx[0], mx[0]) + min(fx[1], mx[1])) / 2,
                         contact_polygons=[[(dot(p, across), dot(p, up)) for p in vs] for vs in (fv, mv)])
        return self.data

    def limits(self):
        limits = self.motion.slideLimits
        return (limits.minimumValue if limits.isMinimumValueEnabled else None,
                limits.maximumValue if limits.isMaximumValueEnabled else None)

    def opening_limits_mm(self):
        if self.slope is None:
            return None, None
        return tuple(None if v is None else v * 10 for v in
                     gap_limits(self.motion.slideValue, self.data['gap'], self.slope, self.limits()))

    def calibrate(self, design):
        """One reversible probe, only on first configuration and with vise hidden."""
        old = self.motion.slideValue
        before = self.measure().copy()
        low, high = self.limits()
        delta = .01 if high is None or old + .01 <= high else -.01
        if low is not None and old + delta < low:
            raise ValueError('Slider has no travel available for verification.')
        try:
            self.motion.slideValue = old + delta
            adsk.doEvents()
            after = self.measure()
            if before['fixed'].distanceTo(after['fixed']) > 1e-4:
                raise ValueError('The selected fixed jaw moves with this slider. Swap the jaw face selections.')
            self.slope = (after['gap'] - before['gap']) / delta
            try:
                slider_target(old, before['gap'], before['gap'], self.slope)
            except ValueError as exc:
                raise ValueError(f'{exc} Measured probe: slide {old:g} to {self.motion.slideValue:g} cm; '
                                 f'gap {before["gap"]:g} to {after["gap"]:g} cm.') from exc
        finally:
            self.motion.slideValue = old
            adsk.doEvents()
            self.measure()

    def drive(self, design, required_cm):
        old = self.motion.slideValue
        target = slider_target(old, self.data['gap'], required_cm, self.slope, self.limits())
        if abs(target - old) > 1e-6:
            self.motion.slideValue = target
            adsk.doEvents()
        if abs(self.measure()['gap'] - required_cm) > .005:
            raise ValueError('Measured jaw opening differs from the requested opening by more than 0.05 mm.')

    def serialize(self):
        return dict(version=2, tokens={key: obj.entityToken for key, obj in self.entities.items()},
                    signatures={key: signature(self.entities[key]) for key in ('fixed', 'moving', 'seat')},
                    slope=self.slope, master_revision=getattr(self, 'master_revision', None))

    def placement(self, frame, stock, axis, side, grip_cm):
        origin, sx, sy, sz = frame
        inward = vector(sx if axis == 'X' else sy, -1 if side == '+' else 1)
        across = inward.crossProduct(sz)
        across.normalize()
        center_x = (stock['min_x'] + stock['max_x']) / 2
        center_y = (stock['min_y'] + stock['max_y']) / 2
        if axis == 'X':
            center_x = stock['max_x'] if side == '+' else stock['min_x']
        else:
            center_y = stock['max_y'] if side == '+' else stock['min_y']
        anchor = point(origin, vector(sx, center_x), vector(sy, center_y),
                       vector(sz, stock['min_z'] + grip_cm))
        d = self.data
        source = point(d['fixed'], vector(d['across'], d['across_center'] - dot(d['fixed'], d['across'])),
                       vector(d['up'], d['jaw_top'] - dot(d['fixed'], d['up'])))
        correction = c.Matrix3D.create()
        if not correction.setToAlignCoordinateSystems(source, d['across'], d['clamp'], d['up'],
                                                      anchor, across, inward, sz):
            raise ValueError('Could not align vise gripping frame with stock.')
        result = self.vise.transform2.copy()
        result.transformBy(correction)
        return result

