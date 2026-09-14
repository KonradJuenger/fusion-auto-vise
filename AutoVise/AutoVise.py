import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import autovise_geometry
import autovise_support
import autovise_impl
import autovise_v09


def _reload():
    importlib.reload(autovise_geometry)
    importlib.reload(autovise_support)
    importlib.reload(autovise_impl)
    importlib.reload(autovise_v09)
    autovise_v09.apply_patches()


def run(context):
    _reload()
    autovise_impl.run(context)


def stop(context):
    autovise_impl.stop(context)
