import importlib
import os
import sys
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BOOTSTRAP_LOG = os.path.join(HERE, 'bootstrap_debug.txt')
if HERE not in sys.path:
    sys.path.insert(0, HERE)

_loaded_impl = None


def _write_bootstrap(stage, exc=None, extra=None):
    lines = [
        f'Auto Vise bootstrap {datetime.now().isoformat(timespec="seconds")}',
        f'Stage: {stage}',
    ]
    if extra:
        lines.append(str(extra))
    if exc is not None:
        lines += [f'Exception: {exc}', traceback.format_exc()]
    text = '\n'.join(lines) + '\n'

    try:
        with open(BOOTSTRAP_LOG, 'w', encoding='utf-8') as f:
            f.write(text)
    except Exception:
        pass

    try:
        import adsk.core
        adsk.core.Application.get().log(text)
    except Exception:
        pass

    return text


def _message(text):
    try:
        import adsk.core
        app = adsk.core.Application.get()
        if app and app.userInterface:
            app.userInterface.messageBox(text, 'Auto Vise bootstrap')
    except Exception:
        pass


def _load_modules():
    global _loaded_impl

    try:
        _write_bootstrap('starting module load')

        import autovise_geometry
        import autovise_support
        import autovise_parallels
        import autovise_impl
        import autovise_v09
        import autovise_v11
        import autovise_v13
        import autovise_v14
        import autovise_v15
        import autovise_v16
        import autovise_v17

        _write_bootstrap('base imports succeeded')

        importlib.reload(autovise_geometry)
        _write_bootstrap('reloaded autovise_geometry')

        importlib.reload(autovise_support)
        _write_bootstrap('reloaded autovise_support')

        importlib.reload(autovise_parallels)
        _write_bootstrap('reloaded autovise_parallels')

        importlib.reload(autovise_impl)
        _write_bootstrap('reloaded autovise_impl')

        importlib.reload(autovise_v09)
        _write_bootstrap('reloaded autovise_v09')
        autovise_v09.apply_patches()
        _write_bootstrap('applied V0.9 frame/cleanup patches')

        importlib.reload(autovise_v11)
        _write_bootstrap('reloaded autovise_v11')
        autovise_v11.apply_patches()
        _write_bootstrap('applied V0.11 jaw/proxy patches')

        importlib.reload(autovise_v13)
        _write_bootstrap('reloaded autovise_v13')
        autovise_v13.apply_patches()
        _write_bootstrap('applied V0.13 parallel frame patches')

        importlib.reload(autovise_v14)
        _write_bootstrap('reloaded autovise_v14')
        autovise_v14.apply_patches()
        _write_bootstrap('applied V0.14 parallel centering patch')

        importlib.reload(autovise_v15)
        _write_bootstrap('reloaded autovise_v15')
        autovise_v15.apply_patches()
        _write_bootstrap('applied V0.15 measured orientation patch')

        importlib.reload(autovise_v16)
        _write_bootstrap('reloaded autovise_v16')
        autovise_v16.apply_patches()
        _write_bootstrap('applied V0.16 stable fixture-reference patch')

        importlib.reload(autovise_v17)
        _write_bootstrap('reloaded autovise_v17')
        autovise_v17.apply_patches()
        _write_bootstrap('applied V0.17 native script-generated parallel patch')

        _loaded_impl = autovise_impl
        _write_bootstrap('module load complete')
        return autovise_impl

    except Exception as exc:
        text = _write_bootstrap('MODULE LOAD FAILED', exc)
        _message(text + f'\nBootstrap log: {BOOTSTRAP_LOG}')
        _loaded_impl = None
        return None


def run(context):
    try:
        impl = _load_modules()
        if impl is None:
            return

        _write_bootstrap('calling autovise_impl.run')
        impl.run(context)
        _write_bootstrap('autovise_impl.run returned successfully')

    except Exception as exc:
        text = _write_bootstrap('RUN FAILED', exc)
        _message(text + f'\nBootstrap log: {BOOTSTRAP_LOG}')


def stop(context):
    global _loaded_impl

    try:
        impl = _loaded_impl
        if impl is None:
            try:
                import autovise_impl as impl
            except Exception:
                impl = None

        if impl is not None:
            impl.stop(context)

        _write_bootstrap('stop completed')

    except Exception as exc:
        text = _write_bootstrap('STOP FAILED', exc)
        _message(text + f'\nBootstrap log: {BOOTSTRAP_LOG}')

    finally:
        _loaded_impl = None
