# Fusion Auto Vise

Fusion add-in that fits a linked vise around CAM stock and uses Fusion's native **Setup > Part Position** for placement inside the machine model.

## V0.10

V0.10 addresses the intermittent linked-vise joint state seen in repeated runs and makes the setup-frame diagnostics run before any jaw operation can abort.

### Assembly-context slider drive

A linked Fusion component has one native joint definition, but each inserted occurrence has its own assembly context. The latest debug showed a freshly inserted vise with a **49 mm geometric jaw gap** while the native slider still reported **51.887 mm** from a previous run. Writing 51.887 mm to that native joint again therefore caused no geometric change.

V0.10 now:

1. gets the `movable jaw` occurrence inside the inserted vise,
2. inspects `Occurrence.joints`, which returns joint proxies in that occurrence's assembly context,
3. also attempts `Joint.createForAssemblyContext(vise)` as an explicit proxy route,
4. logs native and proxy joint values separately,
5. forces the occurrence-specific slider away from any cached value,
6. drives the requested target gap,
7. verifies the actual B-Rep jaw gap before continuing.

If no assembly-context slider proxy can produce the requested geometry, Auto Vise stops and records the full proxy/native state rather than silently moving the wrong object.

### Early setup-frame diagnostics

The model/WCS frame analysis now runs immediately after CAM stock is read. This means `last_debug.txt` contains frame diagnostics even if slider adjustment fails later.

Auto Vise compares the actual `Setup.models` bounding box against:

- WCS direct
- WCS direct with translation divided by 10
- WCS inverse
- WCS inverse with translation divided by 10

The best-fitting frame is used for the translucent stock box and vise placement.

### Repeat-run cleanup

Before inserting a new linked vise, Auto Vise removes prior managed fixture references, deletes all previous `AUTO_VISE` occurrences, processes Fusion events, verifies cleanup, and inserts one fresh occurrence.

## Normal workflow

1. Assign a machine and set **Setup > Part Position > Table Attach Point** once.
2. Run Auto Vise.
3. Choose Setup X or Setup Y as the jaw movement direction.
4. Choose the fixed-jaw side and grip depth.
5. Auto Vise resolves the actual local Setup frame, opens the vise using the occurrence-specific slider joint, places the vise around the resolved CAM stock, and adds it as a fixture.
6. Part Position X/Y/Z offsets are applied relative to Fusion's Table Attach Point.

## Debugging

Keep **Show local stock + write log** enabled. Important sections now include:

- `=== DEEP FRAME DEBUG ===`
- `CHOSEN FRAME: ...`
- `=== V0.10 ASSEMBLY-CONTEXT JOINT DEBUG ===`
- native slider state
- occurrence/proxy slider state
- movable-jaw transform and bounding box after every forced reset and target
- final geometric jaw gap
- final vise transform and orientation
- Part Position parameters

The first visual sanity check remains: the translucent stock box must surround the actual part. Machine Part Position comes after that local relationship is correct.

## Important Part Position detail

`job_positionXOffset`, `job_positionYOffset`, and `job_positionZOffset` are offsets from Fusion's **Table Attach Point**, not absolute machine coordinates.

## Current limitations

- Fixed Size Box and Relative Size Box stock only.
- Vise master must contain child components named `fixed jaw` and `movable jaw`.
- A slider joint is strongly preferred for the movable jaw.
- The machining file and vise master must currently be in the same Fusion project for linked insertion.
- Fusion's machine Table Attach Point still needs manual selection because the current API does not expose machine-model geometry sufficiently for automatic selection.
- Vise master Z is assumed to be up.
