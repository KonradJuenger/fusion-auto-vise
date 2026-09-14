# Fusion Auto Vise

Fusion add-in that fits a linked vise around CAM stock and uses Fusion's native **Setup > Part Position** for placement inside the machine model.

## V0.8

V0.8 fixes the two failures exposed by the latest test.

### Slider jaw drive

`SliderJointMotion.slideValue` is an absolute joint position, not a delta. The previous version added the required gap correction to the current value. With the reference vise this produced a 2.887 mm opening instead of the required 51.887 mm.

V0.8 now drives the slider directly to the required geometric jaw gap, tries both signs for reversed slider orientation, and verifies the resulting gap before continuing. If a slider joint exists and cannot reach the requested gap, Auto Vise stops instead of falling back to a conflicting direct occurrence transform. The direct transform fallback is now reserved for vise masters without a slider joint.

### Vise orientation

The selected **Vise clamping direction** means the jaw opening/movement axis. V0.8 applies the vise transform after the jaw gap is solved, then verifies that the transformed clamp vector is parallel to the requested Setup X or Setup Y axis. A mismatch is now an explicit error rather than a silent visual failure.

The latest screenshot appeared to show the vise still along X while Setup Y was selected because V0.7 aborted during jaw adjustment before the orientation transform was ever applied.

### Code layout / hot reload

The add-in is split into:

- `AutoVise.py`, a small loader that reloads the implementation modules on every Run
- `autovise_geometry.py`, vise geometry, jaw and orientation logic
- `autovise_support.py`, settings, fixture, Part Position and debug helpers
- `autovise_impl.py`, Fusion UI and command execution

This makes Fusion development reloads more reliable and future fixes easier to isolate.

## Normal operation

1. Read evaluated CAM box stock.
2. Verify that the Setup has a Table Attach Point.
3. Insert the remembered linked vise master.
4. Detect its fixed gripping face and native clamp axis.
5. Drive the slider to the selected stock X/Y size and verify the measured jaw gap.
6. Orient the vise so the jaw movement axis matches selected Setup X or Setup Y.
7. Place the fixed jaw against the chosen stock side at the requested grip depth.
8. Add the vise as a Setup fixture.
9. Apply Fusion Part Position X/Y/Z offsets relative to the Table Attach Point.
10. Remember settings and write `last_debug.txt` when debug is enabled.

## Important Part Position detail

`job_positionXOffset`, `job_positionYOffset`, and `job_positionZOffset` are offsets from Fusion's **Table Attach Point**, not absolute machine coordinates. Select the machine table datum once in **Setup > Part Position > Table Attach Point**.

## Current limitations

- Fixed Size Box and Relative Size Box stock only.
- Vise master must contain child components named `fixed jaw` and `movable jaw`.
- A slider joint is strongly preferred for the movable jaw.
- The machining file and vise master must currently be in the same Fusion project for linked insertion.
- Fusion's machine Table Attach Point still needs manual selection because the current API does not expose the machine-model geometry sufficiently for automatic selection.
- Vise master Z is assumed to be up.
