"""V0.16 stabilizes Fusion Part Position when generated parallels are enabled.

The Setup currently uses Part Position with a Fixture box point. If generated
parallel bodies are added to setup.fixtures, Fusion recomputes that fixture box
and the machine-placement reference changes. Manual mode only has the vise in
the fixture set, which is why it appears correctly positioned while parallel
mode can jump/appear reoriented in the machine view even though the local vise
transform is identical.

Until Auto Vise uses an explicit fixed fixture datum for Part Position, keep the
parallel bodies as visible generated geometry but do not add them to the CAM
fixture collection. The linked vise remains the Setup fixture, keeping the
Fixture box point stable between manual and parallel support modes.
"""

import adsk.core
import adsk.fusion

import autovise_parallels as p
import autovise_support as s

_ORIG_ADD_TO_FIXTURES = None


def add_vise_only_to_fixtures(setup, vise, parallel_bodies):
    """Keep generated parallels out of setup.fixtures to stabilize Part Position."""
    s._fixture(setup, vise)
    try:
        app = adsk.core.Application.get()
        app.log(
            'Auto Vise V0.16: generated parallels remain visible geometry but are '
            'not added to setup.fixtures because Part Position currently references '
            'the Fixture box point. Adding the parallels changes that reference.'
        )
    except Exception:
        pass


def apply_patches():
    global _ORIG_ADD_TO_FIXTURES
    _ORIG_ADD_TO_FIXTURES = p.add_to_fixtures
    p.add_to_fixtures = add_vise_only_to_fixtures
