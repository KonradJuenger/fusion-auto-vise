"""Command reactivation must not reset edits or restore selections too early."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


class ActivationTests(unittest.TestCase):
    def setUp(self):
        core = NS(**{name: type(name, (), {}) for name in (
            'CommandEventHandler', 'CommandCreatedEventHandler',
            'InputChangedEventHandler', 'ValidateInputsEventHandler')})
        fusion, cam = NS(), NS()
        spec = importlib.util.spec_from_file_location('commands_under_test',
            Path(__file__).resolve().parents[1] / 'AutoVise' / 'autovise_commands.py')
        self.commands = importlib.util.module_from_spec(spec)
        modules = {name: NS() for name in ('autovise_model', 'autovise_roles',
            'autovise_service', 'autovise_support', 'autovise_geometry')}
        modules.update({'adsk': NS(core=core, fusion=fusion, cam=cam),
                        'adsk.core': core, 'adsk.fusion': fusion, 'adsk.cam': cam})
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(self.commands)

    def test_activation_restores_once_then_preserves_user_edits(self):
        session = NS(activated=False, load_setup=Mock(), refresh=Mock())
        handler = self.commands.Activated(session)
        handler.notify(None)
        handler.notify(None)
        session.load_setup.assert_called_once_with()
        self.assertEqual(session.refresh.call_count, 2)

    def test_active_setup_is_used_instead_of_first_setup(self):
        first, active = NS(isActive=False), NS(isActive=True)
        self.commands.model.items = list
        self.assertIs(self.commands.active_setup(NS(setups=[first, active])), active)

    def test_multiple_inactive_setups_require_explicit_activation(self):
        self.commands.model.items = list
        with self.assertRaisesRegex(ValueError, 'Activate'):
            self.commands.active_setup(NS(setups=[NS(isActive=False), NS(isActive=False)]))

    def test_changes_before_activation_do_not_restore_selections(self):
        session = NS(activated=False, busy=False, load_setup=Mock(), refresh=Mock())
        self.commands.Changed(session).notify(NS(input=NS(id='setup')))
        session.load_setup.assert_not_called()
        session.refresh.assert_not_called()

    def test_insert_and_fit_use_one_execute(self):
        commands = self.commands
        vise, profile = object(), object()
        commands.c.Matrix3D = NS(create=lambda: 'identity')
        commands.service.configured_vise = Mock(return_value=None)
        commands.service.apply = Mock()
        commands.model.write = Mock()
        commands.roles.profile = Mock(return_value=profile)
        insert = Mock(return_value=vise)
        session = NS(clear_preview=Mock(), inserting=lambda: True,
            source_file=NS(id='master'), design=NS(rootComponent=NS(
                occurrences=NS(addByInsert=insert))), cam=object(), setup=lambda: 'setup',
            options=lambda: 'options', parallel=lambda: 'parallel', app=NS(log=Mock()))
        args = NS(executeFailed=False)
        commands.Execute(session).notify(args)
        self.assertFalse(args.executeFailed)
        insert.assert_called_once_with(session.source_file, 'identity', True)
        commands.roles.profile.assert_called_once_with(vise)
        commands.service.apply.assert_called_once_with(session.design, session.cam,
            'setup', profile, 'options', 'parallel')

    def test_invalid_insert_marks_command_for_rollback(self):
        commands = self.commands
        commands.c.Matrix3D = NS(create=lambda: 'identity')
        commands.service.configured_vise = Mock(return_value=None)
        commands.model.write = Mock()
        commands.roles.profile = Mock(side_effect=ValueError('Missing fixed grip role'))
        commands.service.apply = Mock()
        session = NS(clear_preview=Mock(), inserting=lambda: True,
            source_file=NS(id='master'), design=NS(rootComponent=NS(
                occurrences=NS(addByInsert=Mock(return_value=object())))),
            setup=lambda: 'setup', app=NS(log=Mock()))
        args = NS(executeFailed=False)
        commands.Execute(session).notify(args)
        self.assertTrue(args.executeFailed)
        self.assertEqual(args.executeFailedMessage, 'Missing fixed grip role')
        commands.service.apply.assert_not_called()
