import adsk.core
import adsk.fusion
import adsk.cam
import os
import traceback
from datetime import datetime

import autovise_geometry as g
import autovise_support as s

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

class Created(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        app, ui = _app_ui()
        try:
            cam = _cam(app.activeDocument)
            if not cam or not cam.setups.count:
                raise RuntimeError('Create a Manufacture Setup first.')
            inputs = args.command.commandInputs
            settings = s._load_settings()

            setup_input = inputs.addDropDownCommandInput('setup', 'Setup', adsk.core.DropDownStyles.TextListDropDownStyle)
            for n in range(cam.setups.count):
                setup_input.listItems.add(cam.setups.item(n).name, n == 0, '')
            first_setup = cam.setups.item(0)

            saved_axis = settings.get('vise_setup_axis', 'X')
            axis = inputs.addDropDownCommandInput('axis', 'Vise clamping direction', adsk.core.DropDownStyles.TextListDropDownStyle)
            axis.listItems.add('Setup X', saved_axis == 'X', '')
            axis.listItems.add('Setup Y', saved_axis == 'Y', '')

            saved_sign = settings.get('fixed_jaw_sign', '+')
            side = inputs.addDropDownCommandInput('side', 'Fixed jaw side', adsk.core.DropDownStyles.TextListDropDownStyle)
            side.listItems.add('+ side', saved_sign == '+', '')
            side.listItems.add('- side', saved_sign == '-', '')

            grip_mm = float(settings.get('grip_depth_mm', 4.0))
            inputs.addValueInput('grip', 'Grip depth', 'mm', adsk.core.ValueInput.createByString(f'{grip_mm:.3f} mm'))

            xyz = s._saved_machine_position_mm() or s._part_position_values_mm(first_setup) or (0.0, 0.0, 0.0)
            inputs.addValueInput('machine_x', 'Part Position X offset', 'mm', adsk.core.ValueInput.createByString(f'{xyz[0]:.3f} mm'))
            inputs.addValueInput('machine_y', 'Part Position Y offset', 'mm', adsk.core.ValueInput.createByString(f'{xyz[1]:.3f} mm'))
            inputs.addValueInput('machine_z', 'Part Position Z offset', 'mm', adsk.core.ValueInput.createByString(f'{xyz[2]:.3f} mm'))

            inputs.addBoolValueInput('remember_xyz', 'Remember XYZ offsets', True, '', True)
            inputs.addBoolValueInput('fixture', 'Add vise to Setup fixtures', True, '', True)
            inputs.addBoolValueInput('choose', 'Choose/change vise master', True, '', False)
            inputs.addBoolValueInput('debug', 'Show local stock + write log', True, '', True)

            attach_ok, attach_text = s._table_attach_status(first_setup)
            master = settings.get('vise_name', 'none yet')
            attach_state = 'SET' if attach_ok is True else ('MISSING' if attach_ok is False else 'UNKNOWN')
            inputs.addTextBoxCommandInput(
                'info', '',
                f'Remembered vise: {master}\n'
                f'Machine table attach point: {attach_state} ({attach_text})\n'
                'X/Y/Z are offsets from Fusion\'s Table Attach Point. '
                'The vise clamping direction is the jaw opening/movement axis.',
                5, True,
            )
            handler = Execute()
            args.command.execute.add(handler)
            _handlers.append(handler)
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
            grip = inputs.itemById('grip').value
            stock = g._stock(setup)
            required_gap = stock['size_x'] if axis == 'X' else stock['size_y']
            origin, _, _, _ = setup.workCoordinateSystem.getAsCoordinateSystem()
            machine_xyz_mm = (
                inputs.itemById('machine_x').value * 10.0,
                inputs.itemById('machine_y').value * 10.0,
                inputs.itemById('machine_z').value * 10.0,
            )
            attach_ok, attach_text = s._table_attach_status(setup)

            lines = [
                f'Auto Vise debug {datetime.now().isoformat(timespec="seconds")}',
                f'Document: {doc.name}', f'Setup: {setup.name}',
                f'Vise clamping direction Setup {axis}, fixed jaw {fixed_sign} side',
                f'Grip depth {grip * 10.0:.3f} mm',
                f'Stock size {stock["size_x"] * 10.0:.3f} x {stock["size_y"] * 10.0:.3f} x {stock["size_z"] * 10.0:.3f} mm',
                f'Setup WCS origin {s._xyz(origin)}', f'Setup WCS matrix {s._mat(setup.workCoordinateSystem)}',
                f'Requested Part Position offsets: {machine_xyz_mm[0]:.3f}, {machine_xyz_mm[1]:.3f}, {machine_xyz_mm[2]:.3f} mm',
                f'Table attach status: {attach_ok} | {attach_text}',
                'Part Position parameters:',
            ]
            lines += ['  ' + row for row in s._position_param_debug(setup)]

            if attach_ok is False:
                raise RuntimeError(
                    'Fusion Part Position has no Table Attach Point. Open Setup > Part Position and select the machine table datum once.'
                )

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

            placement, anchor, actual_clamp, alignment = g._place_vise(
                vise, fitted_info, setup, stock, grip, axis, fixed_sign
            )
            lines += [
                f'Local fixed-jaw anchor {s._xyz(anchor)}',
                f'Local vise placement {s._mat(placement)}',
                f'Actual world clamp vector {s._xyz(actual_clamp)}',
                f'Orientation alignment with Setup {axis}: {alignment:.6f}',
            ]

            if inputs.itemById('fixture').value:
                s._fixture(setup, vise)
                lines.append('Fixture assigned')

            s._set_part_position(setup, machine_xyz_mm)
            adsk.doEvents()
            applied = s._part_position_values_mm(setup)
            lines.append('Part Position after: ' + (
                f'{applied[0]:.3f}, {applied[1]:.3f}, {applied[2]:.3f} mm' if applied else '<unreadable>'
            ))

            settings = s._load_settings()
            if inputs.itemById('remember_xyz').value:
                settings['machine_part_position_mm'] = list(machine_xyz_mm)
            settings['vise_setup_axis'] = axis
            settings['fixed_jaw_sign'] = fixed_sign
            settings['grip_depth_mm'] = grip * 10.0
            settings['vise_data_file_id'] = df.id
            settings['vise_name'] = df.name
            s._save_settings(settings)

            if inputs.itemById('debug').value:
                s._debug_stock(design, setup, stock)
                lines.append('Local cyan stock debug geometry created')

            path = s._write(lines)
            ui.messageBox(
                f'Auto Vise updated {setup.name}.\n'
                f'Jaw gap: {fitted_info["gap"] * 10.0:.3f} mm.\n'
                f'Vise clamp: Setup {axis}.\n'
                f'Part Position: X {machine_xyz_mm[0]:.3f}, Y {machine_xyz_mm[1]:.3f}, Z {machine_xyz_mm[2]:.3f} mm.\n\n'
                f'Debug log: {path}', APP_NAME
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
            CMD_ID, 'Auto Vise', 'Fit a linked vise to CAM stock and set Fusion Part Position.', RES
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
