import adsk.core
import adsk.fusion
import adsk.cam
import os
import traceback
from datetime import datetime

import autovise_geometry as g
import autovise_support as s
import autovise_parallels as p

APP_NAME = 'Auto Vise'
CMD_ID = 'JK_AutoVise_Command'
PANEL_ID = 'JK_AutoVise_Panel'
HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, 'Resources', 'AutoVise')
_handlers = []


def _app_ui():
    app = adsk.core.Application.get()
    return app, app.userInterface


def _cam(doc):
    return adsk.cam.CAM.cast(doc.products.itemByProductType('CAMProductType'))


def _design(doc):
    d = adsk.fusion.Design.cast(doc.products.itemByProductType('DesignProductType'))
    return d or adsk.fusion.Design.cast(doc.products.itemByProductType('WorkingModelProductType'))


def _parallel_by_name(name):
    for item in p.load_library():
        if item['name'] == name:
            return item
    return None


class InputChanged(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            if args.input.id != 'support':
                return
            support = args.inputs.itemById('support')
            grip = args.inputs.itemById('grip')
            grip.isVisible = bool(
                support.selectedItem and support.selectedItem.name == 'Manual grip depth'
            )
        except Exception:
            pass


class Created(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        app, ui = _app_ui()
        try:
            cam = _cam(app.activeDocument)
            if not cam or not cam.setups.count:
                raise RuntimeError('Create a Manufacture Setup first.')

            inputs = args.command.commandInputs
            settings = s._load_settings()

            setup_input = inputs.addDropDownCommandInput(
                'setup', 'Setup', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            for n in range(cam.setups.count):
                setup_input.listItems.add(cam.setups.item(n).name, n == 0, '')
            first_setup = cam.setups.item(0)

            saved_axis = settings.get('vise_setup_axis', 'X')
            axis = inputs.addDropDownCommandInput(
                'axis', 'Vise clamping direction', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            axis.listItems.add('Setup X', saved_axis == 'X', '')
            axis.listItems.add('Setup Y', saved_axis == 'Y', '')

            saved_sign = settings.get('fixed_jaw_sign', '+')
            side = inputs.addDropDownCommandInput(
                'side', 'Fixed jaw side', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            side.listItems.add('+ side', saved_sign == '+', '')
            side.listItems.add('- side', saved_sign == '-', '')

            library = p.load_library()
            saved_support = settings.get('parallel_selection', 'Manual grip depth')
            valid_support_names = {'Manual grip depth'} | {x['name'] for x in library}
            if saved_support not in valid_support_names:
                saved_support = 'Manual grip depth'

            support = inputs.addDropDownCommandInput(
                'support', 'Stock support', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            support.listItems.add(
                'Manual grip depth', saved_support == 'Manual grip depth', ''
            )
            for item in library:
                support.listItems.add(item['name'], saved_support == item['name'], '')

            grip_mm = float(settings.get('grip_depth_mm', 4.0))
            grip = inputs.addValueInput(
                'grip', 'Manual grip depth', 'mm',
                adsk.core.ValueInput.createByString(f'{grip_mm:.3f} mm')
            )
            grip.isVisible = support.selectedItem.name == 'Manual grip depth'

            xyz = s._saved_machine_position_mm() or s._part_position_values_mm(first_setup) or (0.0, 0.0, 0.0)
            inputs.addValueInput(
                'machine_x', 'Part Position X offset', 'mm',
                adsk.core.ValueInput.createByString(f'{xyz[0]:.3f} mm')
            )
            inputs.addValueInput(
                'machine_y', 'Part Position Y offset', 'mm',
                adsk.core.ValueInput.createByString(f'{xyz[1]:.3f} mm')
            )
            inputs.addValueInput(
                'machine_z', 'Part Position Z offset', 'mm',
                adsk.core.ValueInput.createByString(f'{xyz[2]:.3f} mm')
            )

            inputs.addBoolValueInput('remember_xyz', 'Remember XYZ offsets', True, '', True)
            inputs.addBoolValueInput('fixture', 'Add workholding to Setup fixtures', True, '', True)
            inputs.addBoolValueInput('choose', 'Choose/change vise master', True, '', False)
            inputs.addBoolValueInput('debug', 'Show local stock + write log', True, '', True)

            attach_ok, attach_text = s._table_attach_status(first_setup)
            master = settings.get('vise_name', 'none yet')
            attach_state = 'SET' if attach_ok is True else ('MISSING' if attach_ok is False else 'UNKNOWN')
            heights = ', '.join(str(int(x['height_mm'])) for x in library)
            inputs.addTextBoxCommandInput(
                'info', '',
                f'Remembered vise: {master}\n'
                f'Machine table attach point: {attach_state} ({attach_text})\n'
                f'Parallels: {heights} mm, all 100 x 4 mm. '
                f'Parallel mode reads {p.SEAT_POINT_NAME} from the fixed jaw and calculates clamping depth automatically.',
                6, True,
            )

            execute_handler = Execute()
            args.command.execute.add(execute_handler)
            _handlers.append(execute_handler)

            changed_handler = InputChanged()
            args.command.inputChanged.add(changed_handler)
            _handlers.append(changed_handler)

        except Exception:
            ui.messageBox(traceback.format_exc(), APP_NAME)


class Execute(adsk.core.CommandEventHandler):
    def notify(self, args):
        app, ui = _app_ui()
        lines = []

        try:
            doc = app.activeDocument
            cam = _cam(doc)
            design = _design(doc)
            inputs = args.command.commandInputs

            setup_name = inputs.itemById('setup').selectedItem.name
            setup = s._setup_by_name(cam, setup_name)
            if not setup:
                raise RuntimeError(f'Setup not found: {setup_name}')

            axis = 'X' if inputs.itemById('axis').selectedItem.name.endswith('X') else 'Y'
            fixed_sign = '+' if inputs.itemById('side').selectedItem.name.startswith('+') else '-'
            support_name = inputs.itemById('support').selectedItem.name
            manual_grip = inputs.itemById('grip').value

            stock = g._stock(setup)
            required_gap = stock['size_x'] if axis == 'X' else stock['size_y']
            origin, _, _, _ = setup.workCoordinateSystem.getAsCoordinateSystem()
            machine_xyz_mm = (
                inputs.itemById('machine_x').value * 10.0,
                inputs.itemById('machine_y').value * 10.0,
                inputs.itemById('machine_z').value * 10.0,
            )
            attach_ok, attach_text = s._table_attach_status(setup)

            selected_parallel = None
            if support_name != 'Manual grip depth':
                selected_parallel = _parallel_by_name(support_name)
                if not selected_parallel:
                    raise RuntimeError(f'Parallel definition not found: {support_name}')

            lines = [
                f'Auto Vise debug {datetime.now().isoformat(timespec="seconds")}',
                f'Document: {doc.name}',
                f'Setup: {setup.name}',
                f'Vise clamping direction Setup {axis}, fixed jaw {fixed_sign} side',
                f'Stock support: {support_name}',
                f'Stock size {stock["size_x"] * 10.0:.3f} x {stock["size_y"] * 10.0:.3f} x {stock["size_z"] * 10.0:.3f} mm',
                f'Setup WCS origin {s._xyz(origin)}',
                f'Setup WCS matrix {s._mat(setup.workCoordinateSystem)}',
                f'Requested Part Position offsets: {machine_xyz_mm[0]:.3f}, {machine_xyz_mm[1]:.3f}, {machine_xyz_mm[2]:.3f} mm',
                f'Table attach status: {attach_ok} | {attach_text}',
                'Part Position parameters:',
            ]
            lines += ['  ' + row for row in s._position_param_debug(setup)]

            if attach_ok is False:
                raise RuntimeError(
                    'Fusion Part Position has no Table Attach Point. '
                    'Open Setup > Part Position and select the machine table datum once.'
                )

            p.cleanup(design, cam, lines)

            df, remembered = s._master(doc, inputs.itemById('choose').value)
            if not df:
                return
            lines.append(f'Vise master {df.name}, remembered={remembered}, id={df.id}')

            vise = s._insert_fresh_vise(design, df)
            master_info = g._vise_info(vise)
            s._log_vise(lines, master_info, 'Master vise before jaw adjustment:')

            fitted_info, jaw_method = g._set_jaw_gap(design, vise, required_gap, lines)
            lines.append(f'Jaw adjustment method: {jaw_method}')
            s._log_vise(lines, fitted_info, 'Master vise after jaw adjustment:')

            if selected_parallel:
                grip = p.calculated_grip(
                    vise, fitted_info, selected_parallel, stock['size_z'], lines
                )
            else:
                grip = manual_grip
                lines.append(f'Manual grip depth {grip * 10.0:.3f} mm')

            placement, anchor, actual_clamp, alignment = g._place_vise(
                vise, fitted_info, setup, stock, grip, axis, fixed_sign
            )
            lines += [
                f'Effective clamping depth {grip * 10.0:.3f} mm',
                f'Local fixed-jaw anchor {s._xyz(anchor)}',
                f'Local vise placement {s._mat(placement)}',
                f'Actual world clamp vector {s._xyz(actual_clamp)}',
                f'Orientation alignment with Setup {axis}: {alignment:.6f}',
            ]

            parallel_bodies = []
            if selected_parallel:
                parallel_bodies = p.generate(
                    design, vise, fitted_info, placement, selected_parallel, lines
                )

            if inputs.itemById('fixture').value:
                if parallel_bodies:
                    p.add_to_fixtures(setup, vise, parallel_bodies)
                    lines.append(
                        f'Fixture assigned: vise + {len(parallel_bodies)} generated parallel bodies'
                    )
                else:
                    s._fixture(setup, vise)
                    lines.append('Fixture assigned: vise')

            s._set_part_position(setup, machine_xyz_mm)
            adsk.doEvents()
            applied = s._part_position_values_mm(setup)
            lines.append(
                'Part Position after: ' +
                (f'{applied[0]:.3f}, {applied[1]:.3f}, {applied[2]:.3f} mm'
                 if applied else '<unreadable>')
            )

            settings = s._load_settings()
            if inputs.itemById('remember_xyz').value:
                settings['machine_part_position_mm'] = list(machine_xyz_mm)
            settings['vise_setup_axis'] = axis
            settings['fixed_jaw_sign'] = fixed_sign
            settings['parallel_selection'] = support_name
            settings['grip_depth_mm'] = manual_grip * 10.0
            settings['vise_data_file_id'] = df.id
            settings['vise_name'] = df.name
            s._save_settings(settings)

            if inputs.itemById('debug').value:
                s._debug_stock(design, setup, stock)
                lines.append('Local cyan stock debug geometry created')

            path = s._write(lines)

            support_summary = (
                f'{selected_parallel["name"]}, calculated grip {grip*10.0:.3f} mm'
                if selected_parallel
                else f'manual grip {grip*10.0:.3f} mm'
            )
            ui.messageBox(
                f'Auto Vise updated {setup.name}.\n'
                f'Jaw gap: {fitted_info["gap"] * 10.0:.3f} mm.\n'
                f'Support: {support_summary}.\n'
                f'Vise clamp: Setup {axis}.\n'
                f'Part Position: X {machine_xyz_mm[0]:.3f}, '
                f'Y {machine_xyz_mm[1]:.3f}, Z {machine_xyz_mm[2]:.3f} mm.\n\n'
                f'Debug log: {path}',
                APP_NAME
            )

        except Exception as exc:
            lines += ['', f'EXCEPTION: {exc}', traceback.format_exc()]
            s._write(lines)
            ui.messageBox(f'{exc}\n\n{traceback.format_exc()}', APP_NAME)


def _destroy_ui(ui):
    try:
        ws = ui.workspaces.itemById('CAMEnvironment')
        panel = ws.toolbarPanels.itemById(PANEL_ID) if ws else None
        if panel:
            control = panel.controls.itemById(CMD_ID)
            if control:
                control.deleteMe()
            panel.deleteMe()
    except Exception:
        pass

    try:
        cmd = ui.commandDefinitions.itemById(CMD_ID)
        if cmd:
            cmd.deleteMe()
    except Exception:
        pass


def run(context):
    _, ui = _app_ui()
    try:
        _destroy_ui(ui)
        _handlers.clear()
        ws = ui.workspaces.itemById('CAMEnvironment')
        cmd = ui.commandDefinitions.addButtonDefinition(
            CMD_ID,
            'Auto Vise',
            'Fit a linked vise and optional parallels to CAM stock and set Fusion Part Position.',
            RES
        )
        handler = Created()
        cmd.commandCreated.add(handler)
        _handlers.append(handler)
        panel = ws.toolbarPanels.add(PANEL_ID, 'Auto Vise')
        control = panel.controls.addCommand(cmd)
        control.isPromotedByDefault = True
        control.isPromoted = True
    except Exception:
        ui.messageBox(traceback.format_exc(), APP_NAME)


def stop(context):
    _, ui = _app_ui()
    _destroy_ui(ui)
    s._clear_debug()
    _handlers.clear()
