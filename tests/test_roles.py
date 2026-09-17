"""Master contract tests with small Fusion boundary doubles; no Fusion runtime."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import unittest
from unittest.mock import patch


class Attributes:
    def __init__(self):
        self.data = {}

    def itemByName(self, group, name):
        return self.data.get((group, name))

    def add(self, group, name, value):
        def delete():
            self.data.pop((group, name), None)
            return True
        attr = NS(value=value, deleteMe=delete)
        self.data[group, name] = attr
        return attr


class Entity:
    def __init__(self):
        self.attributes = Attributes()
        self.nativeObject = None
        self.shape = [10, 2, 3]

    def createForAssemblyContext(self, occurrence):
        return NS(nativeObject=self, assemblyContext=occurrence, shape=self.shape)


def component(faces=(), joints=()):
    return NS(attributes=Attributes(), bRepBodies=[NS(faces=list(faces))],
              joints=list(joints), asBuiltJoints=[], occurrences=[], allOccurrences=[])


class MasterRoles(unittest.TestCase):
    def setUp(self):
        self.saved = None
        def revalidate(entity, signature):
            if entity.shape != signature:
                raise ValueError('Changed shape')
        self.model = NS(items=list, same=lambda a, b: a is b,
                        signature=lambda e: e.shape[:], revalidate=revalidate,
                        read=lambda *a: self.saved,
                        Profile=lambda vise, selected, saved: NS(vise=vise, entities=selected, saved=saved))
        adsk = NS(fusion=NS())
        spec = importlib.util.spec_from_file_location('roles_under_test',
            Path(__file__).resolve().parents[1] / 'AutoVise' / 'autovise_roles.py')
        self.roles = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'adsk': adsk, 'adsk.fusion': adsk.fusion,
                                     'autovise_model': self.model}):
            spec.loader.exec_module(self.roles)
        # Existing geometry validation has its own tests; isolate role identity.
        self.validation = patch.object(self.roles, 'validate')
        self.validation.start()
        self.addCleanup(self.validation.stop)
        self.selected = {key: Entity() for key in self.roles.ROLES}
        self.root = component([self.selected[k] for k in ('fixed', 'moving', 'seat')],
                              [self.selected['joint']])
        self.design = NS(rootComponent=self.root)
        self.contract = self.roles.configure(self.design, self.selected)

    def test_configuration_writes_native_labels_and_resolves_exact_entities(self):
        selected, data = self.roles.resolve(self.root)
        self.assertEqual(selected, self.selected)
        self.assertEqual(data['revision'], self.contract['revision'])
        for key, entity in selected.items():
            self.assertIsNotNone(entity.attributes.itemByName(self.roles.GROUP, self.roles.ROLES[key]))

    def test_two_instances_get_their_own_proxies(self):
        one, two = (NS(component=self.root, childOccurrences=[]) for _ in range(2))
        for occurrence in (one, two):
            selected, _ = self.roles.resolve(self.root, occurrence)
            self.assertTrue(all(e.assemblyContext is occurrence for e in selected.values()))
            self.assertTrue(all(e.nativeObject is self.selected[k] for k, e in selected.items()))

    def test_missing_and_duplicate_roles_fail_without_guessing(self):
        label = self.roles.ROLES['fixed']
        self.selected['fixed'].attributes.itemByName(self.roles.GROUP, label).deleteMe()
        with self.assertRaisesRegex(ValueError, 'AUTO_VISE_FIXED_GRIP.*found 0'):
            self.roles.resolve(self.root)
        self.selected['fixed'].attributes.add(self.roles.GROUP, label, self.contract['revision'])
        extra = Entity()
        extra.attributes.add(self.roles.GROUP, label, self.contract['revision'])
        self.root.bRepBodies[0].faces.append(extra)
        with self.assertRaisesRegex(ValueError, 'AUTO_VISE_FIXED_GRIP.*found 2'):
            self.roles.resolve(self.root)

    def test_split_face_or_changed_shape_requires_reconfiguration(self):
        self.selected['seat'].shape = [99]
        with self.assertRaisesRegex(ValueError, 'AUTO_VISE_PARALLEL_SEAT changed geometry'):
            self.roles.resolve(self.root)

    def test_revision_change_discards_calibration_and_legacy_tokens(self):
        vise = NS(component=self.root, childOccurrences=[])
        self.saved = {'tokens': {'fixed': 'old'}, 'slope': -1}
        self.assertIsNone(self.roles.profile(vise).saved)
        self.saved['master_revision'] = self.contract['revision']
        self.assertIs(self.roles.profile(vise).saved, self.saved)
        self.roles.configure(self.design, self.selected)
        self.assertIsNone(self.roles.profile(vise).saved)

    def test_stale_label_is_not_silently_ignored(self):
        self.selected['joint'].attributes.add(self.roles.GROUP, self.roles.ROLES['joint'], 'old')
        with self.assertRaisesRegex(ValueError, 'stale master label'):
            self.roles.resolve(self.root)

    def test_invalid_selection_does_not_erase_existing_contract(self):
        bad = dict(self.selected, fixed=Entity())
        with self.assertRaisesRegex(ValueError, 'must belong'):
            self.roles.configure(self.design, bad)
        self.assertEqual(self.roles.resolve(self.root)[1], self.contract)

    def test_linked_master_cannot_be_modified(self):
        self.root.allOccurrences = [NS(isReferencedComponent=True)]
        with self.assertRaisesRegex(ValueError, 'original vise file'):
            self.roles.configure(self.design, self.selected)
        self.assertEqual(self.roles.resolve(self.root)[1], self.contract)

    def test_reconfiguration_removes_old_role_assignment(self):
        old = self.selected['fixed']
        replacement = Entity()
        self.root.bRepBodies[0].faces.append(replacement)
        self.selected['fixed'] = replacement
        self.roles.configure(self.design, self.selected)
        self.assertIsNone(old.attributes.itemByName(self.roles.GROUP, self.roles.ROLES['fixed']))
        self.assertIs(self.roles.resolve(self.root)[0]['fixed'], replacement)

    def test_unconfigured_master_does_not_use_names(self):
        root = component()
        root.name = 'fixed jaw movable jaw AUTO_VISE_SLIDER'
        with self.assertRaisesRegex(ValueError, 'no Auto Vise roles'):
            self.roles.resolve(root)


if __name__ == '__main__':
    unittest.main()
