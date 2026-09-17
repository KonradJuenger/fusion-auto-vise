"""Live inspection; optional one-shot test plans run inside a Fusion command."""
import importlib
import json
import os
import sys
import traceback
from datetime import datetime, timezone


def run(context):
    root = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    sys.path.insert(0, os.path.join(root, 'AutoVise'))
    import adsk.core as c
    import adsk.fusion as f
    import adsk.cam as cam_api
    import autovise_model as m
    import autovise_geometry as g
    importlib.reload(m)
    # Safe between user commands: keep command handler references alive while
    # refreshing class definitions used by the next command-created event.
    for name in ('autovise_rules', 'autovise_roles', 'autovise_service', 'autovise_commands'):
        importlib.reload(importlib.import_module(name))
    app = c.Application.get()
    report = {'checked_at': datetime.now(timezone.utc).isoformat()}
    try:
        doc = app.activeDocument
        report['document'] = doc.name
        report['products'] = [dict(type=p.productType, name=getattr(p, 'name', ''),
                                  occurrences=f.Design.cast(p).rootComponent.occurrences.count if f.Design.cast(p) else None,
                                  bodies=f.Design.cast(p).rootComponent.bRepBodies.count if f.Design.cast(p) else None)
                              for p in m.items(doc.products)]
        design = f.Design.cast(doc.products.itemByProductType('DesignProductType'))
        design = design or f.Design.cast(doc.products.itemByProductType('WorkingModelProductType'))
        cam = cam_api.CAM.cast(doc.products.itemByProductType('CAMProductType'))
        report['setups'] = []
        for setup in m.items(cam.setups):
            try:
                fixture_tokens = [entity.entityToken for entity in m.items(setup.fixtures)]
            except Exception as exc:
                fixture_tokens = {'error': str(exc)}
            report['setups'].append(dict(name=setup.name, id=setup.operationId, stock=g._stock(setup),
                wcs=setup.workCoordinateSystem.asArray(), config=m.read(setup, 'configuration'),
                fixtures=fixture_tokens,
                position=[dict(name=p.name, expression=p.expression) for p in m.items(setup.parameters) if 'position' in p.name.lower()]))
        report['occurrences'] = []
        for vise in m.items(design.rootComponent.occurrences):
            row = dict(name=vise.name, token=vise.entityToken, transform=vise.transform2.asArray(),
                       grounded=vise.isGrounded, ground_to_parent=vise.isGroundToParent, children=[], joints=[])
            report['occurrences'].append(row)
            row['bodies'] = [dict(name=b.name, volume=b.volume, low=b.boundingBox.minPoint.asArray(),
                                  high=b.boundingBox.maxPoint.asArray()) for b in m.items(vise.bRepBodies)]
            saved_profile = m.read(vise, 'profile')
            if saved_profile:
                try:
                    profile = m.Profile(vise, {key: m.resolve(design, token) for key, token in saved_profile['tokens'].items()}, saved_profile)
                    measured = profile.measure()
                    row['measured'] = dict(gap_mm=measured['gap'] * 10,
                                           clamp=measured['clamp'].asArray(), up=measured['up'].asArray())
                except Exception as exc:
                    row['profile_error'] = str(exc)
            for occ in m.descendants(vise):
                child = dict(name=occ.name, token=occ.entityToken, faces=[])
                row['children'].append(child)
                for body in m.items(occ.bRepBodies):
                    for face in m.items(body.faces):
                        plane = c.Plane.cast(face.geometry)
                        if plane:
                            child['faces'].append(dict(token=face.entityToken, area=face.area,
                                point=face.pointOnFace.asArray(), normal=plane.normal.asArray(),
                                reversed=face.isParamReversed, vertices=face.vertices.count, loops=face.loops.count))
            for joint in m.sliders(vise):
                row['joints'].append(dict(name=joint.name, token=joint.entityToken,
                    one=joint.occurrenceOne.name, two=joint.occurrenceTwo.name,
                    direction=joint.jointMotion.slideDirectionVector.asArray(), slide=joint.jointMotion.slideValue))
            try:
                import autovise_roles as roles
                selected, _ = roles.resolve(vise.component, vise)
                row['master_roles'] = {key: entity.entityToken for key, entity in selected.items()}
            except Exception as exc:
                row['detection_error'] = str(exc)
    except Exception:
        report['error'] = traceback.format_exc()
    plan_path = os.path.join(root, 'tests', 'fusion-plan.json')
    if os.path.isfile(plan_path):
        try:
            with open(plan_path, encoding='utf-8') as stream:
                plan = json.load(stream)
            # Consume before execution: a second inspection must never replay a
            # mutation left over from an earlier test (successful or failed).
            os.replace(plan_path, os.path.join(root, 'tests', 'fusion-plan.last.json'))
            if plan['document'] != app.activeDocument.name:
                raise ValueError('The development plan is restricted to a different test document.')
            if not hasattr(context, 'executeFailed'):
                raise ValueError('Run mutation plans through Auto Vise development check so Fusion can roll back failures.')
            if plan['action'] == 'fit':
                import autovise_service as service
                setup = cam.setups.itemByOperationId(plan['setup_id'])
                vise = m.resolve(design, plan['vise'])
                import autovise_roles as roles
                profile = roles.profile(vise)
                parallel = next((r for r in service.library() if r['name'] == plan.get('support')), None)
                report['fit'] = service.apply(design, cam, setup, profile, plan['options'], parallel)
            elif plan['action'] == 'create_test_setup':
                existing = next((s for s in m.items(cam.setups) if s.name == 'Auto Vise V2 local test'), None)
                if existing:
                    raise ValueError('Test setup already exists; use its id instead of creating a duplicate.')
                setup_input = cam.setups.createInput(cam_api.OperationTypes.MillingOperation)
                setup_input.models = m.items(design.rootComponent.bRepBodies)
                setup = cam.setups.add(setup_input)
                setup.name = 'Auto Vise V2 local test'
                report['created_setup_id'] = setup.operationId
            elif plan['action'] == 'recompute':
                if not design.computeAll():
                    raise ValueError('Fusion recompute failed.')
                report['recomputed'] = True
            elif plan['action'] == 'repair_test_fixtures':
                setup = cam.setups.itemByOperationId(plan['setup_id'])
                if setup.name != 'Auto Vise V2 local test':
                    raise ValueError('Fixture repair is limited to the dedicated local test setup.')
                config = m.read(setup, 'configuration')
                collection = c.ObjectCollection.create()
                for key in ('vise', 'parallels'):
                    if config.get(key):
                        collection.add(m.resolve(design, config[key]))
                setup.fixtures = c.ObjectCollection.create()
                setup.fixtureEnabled = True
                setup.fixtures = collection
                report['repaired_fixture_count'] = setup.fixtures.count
            else:
                raise ValueError('Unknown development plan action: ' + str(plan['action']))
        except Exception:
            report['test_error'] = traceback.format_exc()
            if hasattr(context, 'executeFailed'):
                context.executeFailed = True
                context.executeFailedMessage = report['test_error']
    path = os.path.join(root, 'tests', 'fusion-inspection.json')
    with open(path, 'w', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
    app.log('Auto Vise development report: ' + path)
