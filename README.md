# Fusion Auto Vise

Fusion add-in that fits a linked vise around CAM stock and uses Fusion's native **Setup > Part Position** for placement inside the machine model.

## V0.9

V0.9 is primarily a diagnostic and determinism release. The latest tests show that the jaw and orientation logic can succeed while the actual Setup model still sits somewhere else. The new version therefore validates the coordinate relationship between **CAM stock/WCS** and the actual **Setup model geometry** before placing the vise.

### Deep setup-frame validation

Auto Vise now logs the world/assembly bounding box of every object in `Setup.models` and compares it against four interpretations of `Setup.workCoordinateSystem`:

- WCS direct
- WCS direct with translation divided by 10
- WCS inverse
- WCS inverse with translation divided by 10

The candidate whose resolved stock box best contains and centers on the actual Setup model is selected automatically. The chosen interpretation is written to `last_debug.txt`.

This is intentionally defensive. Autodesk documents CAM length database units as centimeters, but the current test data strongly suggests the WCS translation and the CAM stock/body geometry are not being interpreted in the same coordinate scale by the previous code. V0.9 validates against the real model instead of assuming the matrix convention.

The translucent debug stock is now drawn using this same resolved frame, so if the debug stock encloses the part, the vise placement uses exactly that frame too.

### Repeat-run cleanup

Before inserting a new linked vise, V0.9:

1. removes previous Auto Vise references from all Setup fixture collections,
2. deletes all managed `AUTO_VISE` occurrences,
3. processes Fusion events,
4. verifies that no managed occurrence remains,
5. inserts one fresh linked vise.

The deep log includes occurrence tokens and cleanup results. This is intended to remove the run-to-run instability caused by stale linked occurrences or fixture references.

### Deterministic jaw drive

When a slider joint exists, V0.9 ignores its starting position and drives absolute `+target` and `-target` values. It then selects the result whose measured geometric jaw gap matches the required stock width/depth. The linked component's state from a previous run therefore should not affect the result.

## Normal workflow

1. Assign a machine and set **Setup > Part Position > Table Attach Point** once.
2. Run Auto Vise.
3. Choose Setup X or Setup Y as the jaw movement direction.
4. Choose the fixed-jaw side and grip depth.
5. Auto Vise opens the jaw to the stock size, resolves the actual local Setup frame, places the vise around the resolved stock, and adds it as a fixture.
6. Part Position X/Y/Z offsets are then applied relative to Fusion's Table Attach Point.

## Debugging

Keep **Show local stock + write log** enabled while testing. The log now includes:

- Setup model object types and world/assembly bounding boxes
- raw WCS matrix
- all frame candidates and their stock AABBs
- center-distance and containment error for every frame candidate
- chosen frame
- stale fixture/occurrence cleanup
- slider target and measured jaw gap
- final vise transform and orientation
- Part Position parameters before and after

The most important sanity check is simple: the translucent stock box must surround the actual part before machine Part Position is considered.

## Important Part Position detail

`job_positionXOffset`, `job_positionYOffset`, and `job_positionZOffset` are offsets from Fusion's **Table Attach Point**, not absolute machine coordinates.

## Current limitations

- Fixed Size Box and Relative Size Box stock only.
- Vise master must contain child components named `fixed jaw` and `movable jaw`.
- A slider joint is strongly preferred for the movable jaw.
- The machining file and vise master must currently be in the same Fusion project for linked insertion.
- Fusion's machine Table Attach Point still needs manual selection because the current API does not expose machine-model geometry sufficiently for automatic selection.
- Vise master Z is assumed to be up.
