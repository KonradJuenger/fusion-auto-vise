# Fusion Auto Vise

Fusion add-in that fits a linked vise around CAM stock and uses Fusion's native **Setup > Part Position** for placement inside the machine model.

## V0.6

V0.6 keeps the V0.5 architecture but fixes two practical issues found during testing:

1. **Hot reload:** Fusion can retain an old `CommandDefinition` and its event handler even after the add-in files are replaced. Auto Vise now destroys and rebuilds its own toolbar panel, control and command definition every time `run()` is called. Re-running the add-in should therefore refresh the dialog without restarting Fusion.
2. **Vise orientation:** vise orientation is now explicit. Choose whether the jaws clamp along **Setup X** or **Setup Y**, and independently choose whether the fixed jaw is on the **+** or **-** side. The choices and grip depth are remembered.
3. **Jaw detection:** the master-vise detector no longer infers its native clamp direction from the overall vise bounding-box center. It tests `+X`, `-X`, `+Y`, and `-Y`, then chooses a direction that yields a real fixed gripping face and a positive jaw gap. This avoids the fixed-face failure seen in V0.5.

## Architecture

The problem is separated into two layers:

`local setup / stock -> vise + jaw geometry`

`complete setup assembly -> Fusion Part Position -> machine model`

Normal operation:

1. Read evaluated CAM box stock.
2. Insert a fresh linked vise master.
3. Detect the vise master's native jaw direction and fixed gripping face.
4. Rotate/orient the vise so it clamps along the selected Setup X or Setup Y axis.
5. Place the fixed gripping face against the chosen + or - stock side at the requested grip depth.
6. Move the movable jaw to the exact stock width/depth.
7. Add the vise as a Setup fixture.
8. Set Fusion's native Part Position parameters:
   - `job_positionXOffset`
   - `job_positionYOffset`
   - `job_positionZOffset`
9. Optionally remember those XYZ values.
10. Write `AutoVise/last_debug.txt` while debugging.

The Design geometry stays in local Setup coordinates. Fusion Part Position places the complete part + fixture assembly in the machine model.

## Dialog

- Setup
- **Vise clamping direction:** Setup X / Setup Y
- **Fixed jaw side:** + side / - side
- Grip depth
- Machine Part Position X
- Machine Part Position Y
- Machine Part Position Z
- Remember machine XYZ
- Add vise to Setup fixtures
- Choose/change vise master
- Show local stock + write log

The first selected vise master is remembered using its Fusion `DataFile.id`, so the cloud picker is skipped on normal runs.

The following values are remembered in `AutoVise/settings.json` after a successful run:

- machine Part Position XYZ, when enabled
- vise clamping direction
- fixed-jaw side
- grip depth
- vise master file ID/name

## Updating during development

After replacing/pulling the Python files, re-run Auto Vise from **Scripts and Add-Ins**. V0.6 rebuilds its Fusion UI objects on every `run()`, so a Fusion restart should no longer be required just to see changed dialog controls.

## Current limitations

- Fixed Size Box and Relative Size Box stock only.
- Vise master must contain child components named `fixed jaw` and `movable jaw`.
- The machining file and vise master must currently be in the same Fusion project for linked insertion.
- Part Position X/Y/Z requires the selected Setup to expose `job_positionXOffset`, `job_positionYOffset`, and `job_positionZOffset`.
- Vise master Z is assumed to be up. Arbitrarily tilted vise masters are not yet supported.
