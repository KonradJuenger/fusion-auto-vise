"""V2 command lifecycle. All dialog feedback is read-only."""
import os
import traceback
import adsk.core as c
import adsk.fusion as f
import adsk.cam as cam_api
import autovise_model as model
import autovise_roles as roles
import autovise_service as service
import autovise_support as defaults
import autovise_geometry as stock_api

CMD_ID = 'JK_AutoVise_Command'
INSERT_ID = 'JK_AutoVise_Insert'
MASTER_ID = 'JK_AutoVise_ConfigureMaster'
MASTER_PANEL_ID = 'JK_AutoVise_MasterPanel'
DEV_ID = 'JK_AutoVise_Development'
PANEL_ID = 'JK_AutoVise_Panel'
_handlers = globals().get('_handlers', [])
_sessions = globals().get('_sessions', [])


def products():
    app = c.Application.get()
    doc = app.activeDocument
    if not doc:
        raise ValueError('Open a machining document first.')
    design = f.Design.cast(doc.products.itemByProductType('DesignProductType'))
    design = design or f.Design.cast(doc.products.itemByProductType('WorkingModelProductType'))
    cam = cam_api.CAM.cast(doc.products.itemByProductType('CAMProductType'))
    if not design or not cam or not cam.setups.count:
        raise ValueError('Create a Manufacture setup with rectangular box stock first.')
    return app, design, cam


def connect(event, handler, keep):
    event.add(handler)
    keep.append(handler)


class Session:
    def __init__(self, command):
        self.app, self.design, self.cam = products()
        self.command, self.inputs = command, command.commandInputs
        self.handlers, self.preview = [], None
        self.busy, self.valid = False, False
        self.setups = model.items(self.cam.setups)
        self.rows = service.library()
        self.dropdown('setup', 'Setup', [f'{s.name} (ID {s.operationId})' for s in self.setups])
        self.select('vise', 'Vise instance', ['Occurrences'])
        self.inputs.addTextBoxCommandInput('master_status', 'Vise master',
            'Roles are read from the vise master. Configure them once in the original vise file.', 3, True)
        self.dropdown('axis', 'Clamp direction', ['Setup X', 'Setup Y'])
        self.dropdown('side', 'Fixed jaw side', ['+ side', '- side'])
        self.dropdown('support', 'Stock support', ['Manual grip depth'] + [r['name'] for r in self.rows])
        self.value('grip', 'Manual grip depth', 4)
        self.value('minimum', 'Your minimum grip requirement', 0)
        self.inputs.addTextBoxCommandInput('rules_note', '',
            'Minimum 0 means no shop requirement specified. Geometric fit does not establish holding force. '
            'Manual grip requires a suitable physical support arrangement.', 3, True)
        self.inputs.addBoolValueInput('fixture', 'Include workholding in CAM fixtures', True, '', True)
        self.inputs.addBoolValueInput('preview', 'Show grip region preview', True, '', True)
        machine = self.inputs.addGroupCommandInput('machine', 'Fusion Part Position (optional)')
        machine.isExpanded = False
        machine.children.addBoolValueInput('change_offsets', 'Change offsets from selected Fusion reference', True, '', False)
        for key in ('x', 'y', 'z'):
            self.value('offset_' + key, key.upper() + ' offset from selected reference', 0, machine.children)
        self.dropdown('wcs_units', 'CAM WCS origin units', ['mm', 'cm'])
        self.inputs.addTextBoxCommandInput('feedback', 'Fit feedback', '', 7, True)
        self.inputs.addTextBoxCommandInput('candidates', 'Parallel evaluation', '', 8, True)
        self.load_setup()
        connect(command.inputChanged, Changed(self), self.handlers)
        connect(command.validateInputs, Validate(self), self.handlers)
        connect(command.execute, Execute(self), self.handlers)
        connect(command.destroy, Destroy(self), self.handlers)
        self.refresh()

    def item(self, key):
        return self.inputs.itemById(key)

    def dropdown(self, key, label, names):
        drop = self.inputs.addDropDownCommandInput(key, label, c.DropDownStyles.TextListDropDownStyle)
        for index, name in enumerate(names):
            drop.listItems.add(name, index == 0, '')
        return drop

    def value(self, key, label, mm, inputs=None):
        return (inputs or self.inputs).addValueInput(key, label, 'mm', c.ValueInput.createByReal(mm / 10))

    def select(self, key, label, filters, inputs=None):
        item = (inputs or self.inputs).addSelectionInput(key, label, 'Select ' + label.lower())
        for filter_name in filters:
            item.addSelectionFilter(filter_name)
        item.setSelectionLimits(0, 1)
        return item

    def selected(self, key):
        item = self.item(key)
        return item.selection(0).entity if item.selectionCount else None

    def choose(self, key, name):
        for item in model.items(self.item(key).listItems):
            if item.name == name:
                item.isSelected = True
                return

    def setup(self):
        return self.setups[self.item('setup').selectedItem.index]

    def options(self):
        return dict(axis=self.item('axis').selectedItem.name[-1],
                    side=self.item('side').selectedItem.name[0],
                    grip_mm=self.item('grip').value * 10,
                    minimum_mm=self.item('minimum').value * 10,
                    fixture=self.item('fixture').value,
                    change_offsets=self.item('change_offsets').value,
                    offsets_mm=[self.item('offset_' + key).value * 10 for key in ('x', 'y', 'z')],
                    wcs_units=self.item('wcs_units').selectedItem.name)

    def load_setup(self):
        self.busy = True
        self.load_error = ''
        try:
            setup = self.setup()
            config = service.setup_config(setup)
            global_defaults = defaults._load_settings()
            self.choose('axis', 'Setup ' + config.get('axis', global_defaults.get('vise_setup_axis', 'X')))
            self.choose('side', config.get('side', '+') + ' side')
            self.choose('support', config.get('support', 'Manual grip depth'))
            self.choose('wcs_units', config.get('wcs_units', 'mm'))
            self.item('grip').value = config.get('grip_mm', 4) / 10
            self.item('minimum').value = config.get('minimum_mm', 0) / 10
            self.item('fixture').value = config.get('fixture', True)
            self.item('change_offsets').value = False
            offsets = defaults._part_position_values_mm(setup) or (0, 0, 0)
            for key, mm in zip(('x', 'y', 'z'), offsets):
                self.item('offset_' + key).value = mm / 10
            self.item('vise').clearSelection()
            self.item('master_status').text = 'Select a vise with configured master roles.'
            vise = service.configured_vise(self.design, setup)
            if vise:
                self.item('vise').addSelection(vise)
                self.load_vise(vise)
        except Exception as exc:
            self.load_error = str(exc)
        finally:
            self.busy = False

    def load_vise(self, vise):
        self.load_error = ''
        try:
            roles.resolve(vise.component, vise)
            self.item('master_status').text = 'All four master roles are present and valid.'
        except ValueError as exc:
            self.load_error = str(exc)
            self.item('master_status').text = self.load_error

    def profile(self):
        vise = self.selected('vise')
        if not vise:
            raise ValueError(self.load_error or 'Select an existing vise instance. For a new vise, Cancel and use Insert Vise first.')
        if vise.assemblyContext:
            raise ValueError('Select the top-level vise instance; jaw components may be nested inside it.')
        owner = model.read(vise, 'setup_id')
        if owner is not None and owner != self.setup().operationId:
            raise ValueError('That vise belongs to another setup. Use a separate instance.')
        configured = service.configured_vise(self.design, self.setup())
        if configured and not model.same(configured, vise):
            raise ValueError('This setup already has a vise. Edit its configured instance.')
        return roles.profile(vise)

    def parallel(self):
        name = self.item('support').selectedItem.name
        return next((r for r in self.rows if r['name'] == name), None)

    def clear_preview(self):
        if self.preview and self.preview.isValid:
            self.preview.deleteMe()
        self.preview = None

    def show_preview(self, frame, stock, result):
        if not self.item('preview').value:
            return
        origin, x, y, z = frame
        center = model.point(origin, model.vector(x, (stock['min_x'] + stock['max_x']) / 2),
                             model.vector(y, (stock['min_y'] + stock['max_y']) / 2),
                             model.vector(z, stock['min_z'] + result.grip / 20))
        box = c.OrientedBoundingBox3D.create(center, x, y, stock['size_x'], stock['size_y'], result.grip / 10)
        self.preview = self.design.rootComponent.customGraphicsGroups.add()
        body = self.preview.addBRepBody(f.TemporaryBRepManager.get().createBox(box))
        body.color = f.CustomGraphicsSolidColorEffect.create(c.Color.create(30, 170, 210, 100))
        body.isSelectable = False

    def refresh(self):
        if self.busy:
            return
        self.busy, self.valid = True, False
        try:
            self.clear_preview()
            self.item('grip').isVisible = self.parallel() is None
            for key in ('x', 'y', 'z'):
                self.item('offset_' + key).isEnabled = self.item('change_offsets').value
            profile = self.profile()
            stock = stock_api._stock(self.setup())
            options = self.options()
            target_frame = service.frame(self.setup(), stock, options['wcs_units'])
            result = service.evaluate_profile(profile, stock, options, self.parallel())
            lines = []
            for row in self.rows:
                candidate = service.evaluate_profile(profile, stock, options, row)
                lines.append(f'{row["name"]}: ' + ('fits' if candidate.valid else '; '.join(candidate.reasons)) +
                             f' | grip {candidate.grip:.2f}, protrusion {candidate.protrusion:.2f} mm')
            self.item('candidates').text = '\n'.join(lines)
            maximum = profile.opening_limits_mm()[1]
            limit = f'{maximum:.2f} mm' if maximum is not None else 'unspecified (no calibrated upper opening limit)'
            feedback = (f'Grip: {result.grip:.2f} mm\nStock above jaws: {result.protrusion:.2f} mm\n'
                        f'Required opening: {result.opening:.2f} mm\nMaximum opening: {limit}\n'
                        + ('Fits the selected geometric requirements.' if result.valid else '\n'.join(result.reasons)))
            service.machine_guard(self.setup(), options['change_offsets'])
            self.item('feedback').text = feedback
            self.valid = result.valid
            if result.valid:
                self.show_preview(target_frame, stock, result)
        except Exception as exc:
            self.item('feedback').text = str(exc)
            self.item('candidates').text = 'Configure valid master roles to evaluate supports.'
        finally:
            self.busy = False


class Changed(c.InputChangedEventHandler):
    def __init__(self, session):
        super().__init__()
        self.session = session

    def notify(self, args):
        s = self.session
        if s.busy:
            return
        try:
            if args.input.id == 'setup':
                s.load_setup()
            elif args.input.id == 'vise':
                s.busy = True
                try:
                    if s.selected('vise'):
                        s.load_vise(s.selected('vise'))
                finally:
                    s.busy = False
            s.refresh()
        except Exception as exc:
            s.valid = False
            s.item('feedback').text = str(exc)


class Validate(c.ValidateInputsEventHandler):
    def __init__(self, session):
        super().__init__()
        self.session = session

    def notify(self, args):
        args.areInputsValid = self.session.valid


class Execute(c.CommandEventHandler):
    def __init__(self, session):
        super().__init__()
        self.session = session

    def notify(self, args):
        s = self.session
        try:
            s.clear_preview()
            service.apply(s.design, s.cam, s.setup(), s.profile(), s.options(), s.parallel())
        except Exception as exc:
            args.executeFailed = True
            args.executeFailedMessage = str(exc)
            s.app.log(traceback.format_exc())


class Destroy(c.CommandEventHandler):
    def __init__(self, session):
        super().__init__()
        self.session = session

    def notify(self, args):
        self.session.clear_preview()
        if self.session in _sessions:
            _sessions.remove(self.session)
        self.session.handlers.clear()


class Created(c.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            _sessions.append(Session(args.command))
        except Exception:
            c.Application.get().userInterface.messageBox(traceback.format_exc(), 'Auto Vise V2')


class InsertExecute(c.CommandEventHandler):
    def __init__(self, data_file):
        super().__init__()
        self.data_file = data_file

    def notify(self, args):
        try:
            app, design, _ = products()
            vise = design.rootComponent.occurrences.addByInsert(self.data_file, c.Matrix3D.create(), True)
            if not vise:
                raise ValueError('Fusion could not insert the linked vise.')
            model.write(vise, 'source_file', self.data_file.id)
            app.userInterface.messageBox('Vise inserted. Open Auto Vise V2, select this instance. Its roles are read from the configured master.', 'Auto Vise V2')
        except Exception as exc:
            args.executeFailed = True
            args.executeFailedMessage = str(exc)


class InsertCreated(c.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            app, _, _ = products()
            data_file, _ = defaults._master(app.activeDocument, False)
            if not data_file:
                return
            args.command.commandInputs.addTextBoxCommandInput('insert_info', '',
                f'Insert {data_file.name} as a new independent vise instance. Existing vises are preserved.', 3, True)
            connect(args.command.execute, InsertExecute(data_file), _handlers)
        except Exception as exc:
            c.Application.get().userInterface.messageBox(str(exc), 'Auto Vise V2')


class MasterSession:
    def __init__(self, command):
        self.command = command
        self.handlers = []
        self.inputs = command.commandInputs
        doc = c.Application.get().activeDocument
        self.design = f.Design.cast(doc.products.itemByProductType('DesignProductType')) if doc else None
        if not self.design:
            raise ValueError('Open the original vise design first.')
        self.inputs.addTextBoxCommandInput('master_help', '',
            'Select the three faces and slider joint in the original vise file. OK stores their roles; '
            'then save the master and update linked instances. Cancel makes no changes.', 4, True)
        for key, label in roles.ROLES.items():
            selection = self.inputs.addSelectionInput(key, label, 'Select ' + label)
            selection.addSelectionFilter('Joints' if key == 'joint' else 'PlanarFaces')
            selection.setSelectionLimits(1, 1)
        try:
            selected, _ = roles.resolve(self.design.rootComponent)
            for key, entity in selected.items():
                self.inputs.itemById(key).addSelection(entity)
        except ValueError:
            pass  # An unconfigured or changed master needs explicit selections.
        connect(command.execute, MasterExecute(self), self.handlers)
        connect(command.destroy, Destroy(self), self.handlers)

    def clear_preview(self):
        pass


class MasterExecute(c.CommandEventHandler):
    def __init__(self, session):
        super().__init__()
        self.session = session

    def notify(self, args):
        try:
            s = self.session
            selected = {}
            for key in roles.ROLES:
                item = s.inputs.itemById(key)
                selected[key] = item.selection(0).entity if item.selectionCount else None
            roles.configure(s.design, selected)
        except Exception as exc:
            args.executeFailed = True
            args.executeFailedMessage = str(exc)


class MasterCreated(c.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            _sessions.append(MasterSession(args.command))
        except Exception as exc:
            c.Application.get().userInterface.messageBox(str(exc), 'Configure Vise Master')


class DevelopmentExecute(c.CommandEventHandler):
    def notify(self, args):
        import importlib.util
        path = os.path.join(os.path.dirname(__file__), '..', 'tests', 'FusionInspect', 'FusionInspect.py')
        spec = importlib.util.spec_from_file_location('autovise_development_check', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.run(args)


class DevelopmentCreated(c.CommandCreatedEventHandler):
    def notify(self, args):
        args.command.commandInputs.addTextBoxCommandInput('development_info', '',
            'Run the repository development check in the current document. This tool is absent from standalone add-in installations.', 3, True)
        connect(args.command.execute, DevelopmentExecute(), _handlers)


def stop(context):
    ui = c.Application.get().userInterface
    for session in list(_sessions):
        session.clear_preview()
    _sessions.clear()
    ws = ui.workspaces.itemById('CAMEnvironment')
    panel = ws.toolbarPanels.itemById(PANEL_ID) if ws else None
    if panel:
        panel.deleteMe()
    for key in (CMD_ID, INSERT_ID, DEV_ID, MASTER_ID):
        definition = ui.commandDefinitions.itemById(key)
        if definition:
            definition.deleteMe()
    design_ws = ui.workspaces.itemById('FusionSolidEnvironment')
    master_panel = design_ws.toolbarPanels.itemById(MASTER_PANEL_ID) if design_ws else None
    if master_panel:
        master_panel.deleteMe()
    _handlers.clear()


def run(context):
    stop(context)
    ui = c.Application.get().userInterface
    panel = ui.workspaces.itemById('CAMEnvironment').toolbarPanels.add(PANEL_ID, 'Auto Vise')
    definitions = [(CMD_ID, 'Auto Vise V2', Created()), (INSERT_ID, 'Insert Vise', InsertCreated())]
    if os.path.isfile(os.path.join(os.path.dirname(__file__), '..', '.autovise-dev')):
        definitions.append((DEV_ID, 'Auto Vise development check', DevelopmentCreated()))
    for key, name, handler in definitions:
        definition = ui.commandDefinitions.addButtonDefinition(key, name, name,
            os.path.join(os.path.dirname(__file__), 'Resources', 'AutoVise'))
        connect(definition.commandCreated, handler, _handlers)
        control = panel.controls.addCommand(definition)
        control.isPromoted = True

    design_ws = ui.workspaces.itemById('FusionSolidEnvironment')
    master_panel = design_ws.toolbarPanels.add(MASTER_PANEL_ID, 'Auto Vise Master')
    definition = ui.commandDefinitions.addButtonDefinition(MASTER_ID, 'Configure Vise Master',
        'Assign the four Auto Vise roles once in the original vise file.')
    connect(definition.commandCreated, MasterCreated(), _handlers)
    master_panel.controls.addCommand(definition).isPromoted = True

