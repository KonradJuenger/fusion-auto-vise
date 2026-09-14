import adsk.core
import adsk.fusion
import adsk.cam
import json
import os

ATTR = 'JK_AutoVise'
HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, 'settings.json')
_debug_groups = []

# local small geometry helpers used for debug only
def _v(v, k):
    return adsk.core.Vector3D.create(v.x*k, v.y*k, v.z*k)

def _pt(p, *vectors):
    q = adsk.core.Point3D.create(p.x, p.y, p.z)
    for vec in vectors:
        q.translateBy(vec)
    return q


def _app_ui():
    app = adsk.core.Application.get()
    return app, app.userInterface

def _load_settings():
    try:
        with open(SETTINGS, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_settings(settings):
    try:
        with open(SETTINGS, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception:
        return False


def _master(doc, choose=False):
    app, ui = _app_ui()
    settings = _load_settings()
    if not choose and settings.get('vise_data_file_id'):
        try:
            df = app.data.findFileById(settings['vise_data_file_id'])
            if df and (not doc.dataFile or df.parentProject.id == doc.dataFile.parentProject.id):
                return df, True
        except Exception:
            pass
    dialog = ui.createCloudFileDialog()
    dialog.title = 'Select vise master (remembered after this)'
    dialog.isMultiSelectEnabled = False
    dialog.filter = '*'
    try:
        if doc.dataFile:
            dialog.dataFolder = doc.dataFile.parentFolder
    except Exception:
        pass
    if dialog.showOpen() != adsk.core.DialogResults.DialogOK or not dialog.dataFile:
        return None, False
    df = dialog.dataFile
    if not doc.dataFile:
        raise RuntimeError('Save the machining document to Fusion cloud first.')
    if df.parentProject.id != doc.dataFile.parentProject.id:
        raise RuntimeError('Vise and machining file must be in the same Fusion project.')
    settings['vise_data_file_id'] = df.id
    settings['vise_name'] = df.name
    _save_settings(settings)
    return df, False


def _managed_vise(root):
    for i in range(root.occurrences.count):
        occ = root.occurrences.item(i)
        a = occ.attributes.itemByName(ATTR, 'managed')
        if a and a.value == '1':
            return occ
    return None


def _insert_fresh_vise(design, df):
    root = design.rootComponent
    old = _managed_vise(root)
    if old:
        try: old.deleteMe()
        except Exception: pass
    occ = root.occurrences.addByInsert(df, adsk.core.Matrix3D.create(), True)
    if not occ:
        raise RuntimeError('Failed to insert linked vise.')
    try: occ.name = 'AUTO_VISE: ' + df.name
    except Exception: pass
    occ.attributes.add(ATTR, 'managed', '1')
    occ.attributes.add(ATTR, 'data_file_id', df.id)
    return occ


def _fixture(setup, vise):
    setup.fixtureEnabled = True
    items = adsk.core.ObjectCollection.create()
    try:
        old = setup.fixtures
        for i in range(old.count):
            item = old.item(i)
            occ = adsk.fusion.Occurrence.cast(item)
            if occ:
                a = occ.attributes.itemByName(ATTR, 'managed')
                if a and a.value == '1':
                    continue
            items.add(item)
    except Exception:
        pass
    items.add(vise)
    setup.fixtures = items


PART_POS_PARAMS = ('job_positionXOffset', 'job_positionYOffset', 'job_positionZOffset')


def _part_position_values_mm(setup):
    values = []
    for name in PART_POS_PARAMS:
        p = setup.parameters.itemByName(name)
        if not p or not p.value:
            return None
        try: values.append(float(p.value.value) * 10.0)
        except Exception: return None
    return tuple(values)


def _table_attach_status(setup):
    p = setup.parameters.itemByName('job_positionAttach')
    if not p or not p.value:
        return None, 'job_positionAttach not exposed'
    try:
        value = adsk.cam.CadObjectParameterValue.cast(p.value)
        objects = list(value.value) if value else []
        if objects:
            return True, ', '.join(getattr(o, 'name', getattr(o, 'objectType', '<entity>')) for o in objects)
        return False, 'no Table Attach Point selected'
    except Exception as exc:
        return None, str(exc)


def _set_part_position(setup, xyz_mm):
    for name, mm in zip(PART_POS_PARAMS, xyz_mm):
        p = setup.parameters.itemByName(name)
        if not p:
            raise RuntimeError(f'Setup does not expose {name}.')
        p.expression = f'{mm:.6f} mm'


def _position_param_debug(setup):
    rows = []
    try:
        params = setup.parameters
        for i in range(params.count):
            p = params.item(i)
            low = p.name.lower()
            if 'position' not in low and 'attach' not in low:
                continue
            try:
                val = p.value
                typ = val.objectType if val else '<none>'
                cad = adsk.cam.CadObjectParameterValue.cast(val) if val else None
                if cad:
                    content = f'{len(cad.value)} object(s)'
                else:
                    content = str(getattr(val, 'value', '<n/a>')) if val else '<none>'
                rows.append(f'{p.name}: {typ} = {content}; expr={p.expression}')
            except Exception as exc:
                rows.append(f'{p.name}: <read failed {exc}>')
    except Exception as exc:
        rows.append(f'<enumeration failed {exc}>')
    return rows


def _saved_machine_position_mm():
    value = _load_settings().get('machine_part_position_mm')
    if isinstance(value, list) and len(value) == 3:
        try: return tuple(float(x) for x in value)
        except Exception: pass
    return None


def _setup_by_name(cam, name):
    for i in range(cam.setups.count):
        s = cam.setups.item(i)
        if s.name == name:
            return s
    return None


def _xyz(v):
    return f'({v.x:.6f}, {v.y:.6f}, {v.z:.6f})'


def _bb(bb):
    return f'min={_xyz(bb.minPoint)} max={_xyz(bb.maxPoint)}'


def _mat(m):
    try: return '[' + ', '.join(f'{x:.6f}' for x in m.asArray()) + ']'
    except Exception: return '<unavailable>'


def _clear_debug():
    global _debug_groups
    for group in _debug_groups:
        try: group.deleteMe()
        except Exception: pass
    _debug_groups = []


def _debug_stock(design, setup, stock):
    _clear_debug()
    group = design.rootComponent.customGraphicsGroups.add()
    _debug_groups.append(group)
    origin, x, y, z = setup.workCoordinateSystem.getAsCoordinateSystem()
    x.normalize(); y.normalize(); z.normalize()
    center = _pt(
        origin,
        _v(x, (stock['min_x'] + stock['max_x']) / 2.0),
        _v(y, (stock['min_y'] + stock['max_y']) / 2.0),
        _v(z, (stock['min_z'] + stock['max_z']) / 2.0),
    )
    box = adsk.core.OrientedBoundingBox3D.create(center, x, y, stock['size_x'], stock['size_y'], stock['size_z'])
    body = group.addBRepBody(adsk.fusion.TemporaryBRepManager.get().createBox(box))
    try: body.setOpacity(0.18, True)
    except Exception: pass


def _write(lines):
    path = os.path.join(HERE, 'last_debug.txt')
    text = '\n'.join(lines) + '\n'
    try:
        with open(path, 'w', encoding='utf-8') as f: f.write(text)
    except Exception:
        path = '<write failed>'
    try: adsk.core.Application.get().log(text)
    except Exception: pass
    return path


def _log_vise(lines, info, label):
    lines += [
        label,
        f'  native clamp={info["native_clamp_label"]} vector={_xyz(info["clamp"])} across={_xyz(info["across"])}',
        f'  fixed bbox: {_bb(info["fixed"].preciseBoundingBox)}',
        f'  moving bbox: {_bb(info["moving"].preciseBoundingBox)}',
        f'  fixed contact={info["fixed_inner"]:.6f} moving contact={info["moving_inner"]:.6f} gap={info["gap"] * 10.0:.3f} mm',
        f'  chosen contact body={info["contact_body"]} face={info["contact_face"]} {_bb(info["contact_bb"])}',
    ]
    for attempt in info.get('detector_attempts', []):
        lines.append('  detector: ' + attempt)


