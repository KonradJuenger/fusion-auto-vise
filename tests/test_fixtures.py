"""Regression checks for CAM's extra assembly context around design fixtures."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


class Occurrence:
    def __init__(self, native=None, parent=None):
        self.nativeObject = native
        self.assemblyContext = parent

    @staticmethod
    def cast(entity):
        return entity if isinstance(entity, Occurrence) else None


class FixtureContextTests(unittest.TestCase):
    def setUp(self):
        fusion = NS(Occurrence=Occurrence)
        core = NS()
        spec = importlib.util.spec_from_file_location('service_under_test',
            Path(__file__).resolve().parents[1] / 'AutoVise' / 'autovise_service.py')
        self.service = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            'adsk': NS(core=core, fusion=fusion), 'adsk.core': core, 'adsk.fusion': fusion,
            'autovise_geometry': NS(), 'autovise_support': NS(),
            'autovise_model': NS(same=lambda a, b: a is b),
            'autovise_rules': NS(evaluate=None, rectangle_in_polygon=None),
        }):
            spec.loader.exec_module(self.service)

    def test_cam_wrapper_resolves_to_design_occurrence(self):
        vise = Occurrence()
        proxy = Occurrence(native=vise, parent=Occurrence(parent=Occurrence()))
        self.assertTrue(self.service.fixture_uses(proxy, vise))

    def test_nested_fixture_body_belongs_to_vise(self):
        vise = Occurrence()
        body = NS(assemblyContext=Occurrence(parent=Occurrence(native=vise, parent=Occurrence())))
        self.assertTrue(self.service.fixture_uses(body, vise))

    def test_separate_occurrence_does_not_match(self):
        vise = Occurrence()
        other = Occurrence(native=Occurrence(), parent=Occurrence())
        self.assertFalse(self.service.fixture_uses(other, vise))

    def test_root_body_does_not_match(self):
        self.assertFalse(self.service.fixture_uses(NS(assemblyContext=None), Occurrence()))

    def test_fitting_does_not_require_machine_attachment(self):
        # No machine properties are available; fitting must not read them.
        self.assertFalse(self.service.machine_guard(object(), False))

    def test_offset_changes_still_require_table_attachment(self):
        self.service.defaults._table_attach_status = lambda setup: (False, None)
        with self.assertRaisesRegex(ValueError, 'Table Attach Point'):
            self.service.machine_guard(object(), True)


class ViseSelectionTests(unittest.TestCase):
    def setUp(self):
        fusion = NS(Occurrence=Occurrence)
        core = NS()
        spec = importlib.util.spec_from_file_location('model_under_test',
            Path(__file__).resolve().parents[1] / 'AutoVise' / 'autovise_model.py')
        self.model = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            'adsk': NS(core=core, fusion=fusion), 'adsk.core': core, 'adsk.fusion': fusion,
            'autovise_rules': NS(slider_target=None, gap_limits=None),
        }):
            spec.loader.exec_module(self.model)
        self.vise = Occurrence()
        self.other = Occurrence()
        roots = [self.vise, self.other]
        self.design = NS(rootComponent=NS(occurrences=NS(count=2, item=roots.__getitem__)))

    def test_direct_root_selection(self):
        self.assertIs(self.model.design_vise(self.design, self.vise), self.vise)

    def test_manufacture_selection_uses_native_root(self):
        proxy = Occurrence(native=self.vise, parent=Occurrence(parent=Occurrence()))
        self.assertIs(self.model.design_vise(self.design, proxy), self.vise)

    def test_nested_jaw_is_not_promoted_to_vise(self):
        jaw = Occurrence(native=Occurrence(), parent=self.vise)
        with self.assertRaisesRegex(ValueError, 'top-level'):
            self.model.design_vise(self.design, jaw)

    def test_distinct_instance_is_preserved(self):
        proxy = Occurrence(native=self.other, parent=Occurrence())
        self.assertIs(self.model.design_vise(self.design, proxy), self.other)
