# Fusion Auto Vise

Fusion add-in that fits a linked vise around CAM stock and uses Fusion's native **Setup > Part Position** for placement inside the machine model.

## V0.7

V0.7 fixes the two issues found in the latest test.

### Movable jaw

If the vise master contains a slider joint, Auto Vise now drives that joint instead of moving the `movable jaw` occurrence directly.

The add-in:

1. measures the current geometric jaw gap,
2. finds a Slider Joint or Slider As-Built Joint in the vise master,
3. tests the required slide direction,
4. drives `SliderJointMotion.slideValue`,
5. measures the jaw gap again,
6. only continues when the measured gap matches the CAM stock width/depth within tolerance.

If no usable slider exists, the old direct occurrence transform remains as a fallback and is also geometrically verified.

### Fusion Part Position

`job_positionXOffset`, `job_positionYOffset`, and `job_positionZOffset` are **offsets from Fusion's Table Attach Point**. They are not absolute machine XYZ coordinates.

A Setup therefore needs a machine **Table Attach Point** before these offsets can locate the part + fixture assembly in the machine model.

Auto Vise now checks `job_positionAttach`. If Fusion reports that no table/machine attach entity is selected, the add-in stops and tells you to set it in:

**Setup > Part Position > Table Attach Point**

This is currently a Fusion API limitation: the CAM API can set the XYZ offsets, but machine-model geometry is not exposed sufficiently for Auto Vise to choose the machine attachment point automatically. The machine attachment point therefore remains a one-time/manual selection per Setup for now.

The debug log also dumps the Part Position / attachment-related CAM parameters so we can inspect future Fusion API changes.

## Architecture

The problem stays split into two layers:

`local setup / stock -> vise + verified jaw geometry`

`complete setup assembly -> Fusion Part Position -> machine model`

Normal operation:

1. Read evaluated CAM box stock.
2. Verify that the Setup has a Table Attach Point for machine placement.
3. Insert a fresh linked vise master.
4. Detect the vise master's native clamp direction and fixed gripping face.
5. Drive the slider joint until the measured jaw gap equals the selected stock X/Y dimension.
6. Rotate/orient the vise so it clamps along selected Setup X or Setup Y.
7. Place the fixed gripping face against the chosen + or - stock side at the requested grip depth.
8. Add the vise as a Setup fixture.
9. Set Fusion Part Position X/Y/Z offsets.
10. Optionally remember those offsets and the workholding settings.
11. Write `AutoVise/last_debug.txt` while debugging.

The Design geometry stays in local Setup coordinates. Part Position moves the complete part + fixture assembly relative to the machine attachment point.

## Dialog

- Setup
- **Vise clamping direction:** Setup X / Setup Y
- **Fixed jaw side:** + side / - side
- Grip depth
- Part Position X offset
- Part Position Y offset
- Part Position Z offset
- Remember XYZ offsets
- Add vise to Setup fixtures
- Choose/change vise master
- Show local stock + write log

The dialog also reports whether Auto Vise can see a machine Table Attach Point on the first Setup.

## Updating during development

Auto Vise deletes and rebuilds its own Fusion command definition, toolbar control, panel and event handler every time `run()` executes. After replacing/pulling files, use **Scripts and Add-Ins > Stop > Run**. A Fusion restart should not be required just to refresh the Auto Vise dialog.

## Current limitations

- Fixed Size Box and Relative Size Box stock only.
- Vise master must contain child components named `fixed jaw` and `movable jaw`.
- A slider joint is strongly preferred for the movable jaw.
- The machining file and vise master must currently be in the same Fusion project for linked insertion.
- Fusion Part Position still requires a manually selected machine Table Attach Point because the current API does not expose machine-model geometry sufficiently for automatic selection.
- Vise master Z is assumed to be up. Arbitrarily tilted vise masters are not yet supported.
