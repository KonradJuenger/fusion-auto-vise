# Fusion Auto Vise

Fusion add-in that positions CAM stock in a fixed machine-mounted vise.

## V0.4 architecture

The vise no longer follows the part.

The physical model is now:

`machine -> fixed vise -> jaw opening -> part/stock loaded into vise`

Auto Vise stores the vise position once in machine/design coordinates. On normal runs it:

1. Reads the selected Manufacture Setup and evaluated box stock.
2. Inserts a fresh linked copy of the remembered vise master.
3. Places the vise at the saved machine position.
4. Reads the actual fixed gripping face and current jaw gap.
5. Moves the movable jaw to the required stock width/depth.
6. Calculates the rigid transform that puts the CAM stock against the fixed jaw at the requested grip depth.
7. Moves the Setup model geometry into the vise.
8. Re-reads the Setup WCS/stock after the move.
9. Adds the vise as a Setup fixture.
10. Draws the final stock as translucent debug geometry and writes `AutoVise/last_debug.txt` when debug is enabled.

The Setup model remains the canonical part geometry. `Setup.models` can contain an Occurrence or a BRepBody. Component occurrences are repositioned using Fusion occurrence transforms. Root-level BRep bodies, like the current `Body1` reference test, are moved using a Fusion Move feature.

## One-time vise calibration

The add-in cannot infer where your physical vise is bolted to the machine bed. That is a machine-specific datum, so it is calibrated once.

1. Start Auto Vise. If no machine position is stored, the linked `AUTO_VISE` occurrence is inserted.
2. Move that occurrence manually to the vise's real position on the machine bed.
3. Run Auto Vise again.
4. Enable **Capture current vise as machine position**.
5. Confirm.

The root occurrence transform is stored in `AutoVise/settings.json` as `machine_vise_transform` and reused across machining documents.

Normal runs then delete/reinsert a fresh linked vise master before applying the saved transform. This prevents previous movable-jaw transforms from accumulating.

## Current normal settings

For the reference vise:

- Clamp along Setup axis: `Y`
- Stock side at fixed jaw: `Y+`
- Grip depth: `4 mm`
- Add vise to Setup fixtures: enabled
- Capture current vise as machine position: disabled after calibration
- Show final stock + write log: enabled while testing

## Remembered vise master

The first selected vise file is stored using its Fusion `DataFile.id` and resolved on later runs. Normal use therefore bypasses the cloud file picker. Enable **Choose/change vise master** only when you want to switch fixture files.

## Important V0.4 behavior

Moving the Setup model is intentional. This makes the CAD/CAM state match the physical loading process instead of moving the fixture around an arbitrary part location.

For a root-level BRepBody, Auto Vise creates a timeline Move feature named `AUTO_VISE part position`. For a component-based workpiece, it moves the occurrence directly.

## Current limitations

- Fixed Size Box and Relative Size Box stock only.
- Vise master must contain child components named `fixed jaw` and `movable jaw`.
- Vise must be mounted level with its clamp/across directions parallel to machine X/Y. 90-degree rotations around Z are supported; arbitrary tilted fixture orientations are not yet supported.
- The machining file and vise master must currently be in the same Fusion project for linked insertion.
- Root-body runs currently add another Move feature when another real reposition is required. A later version can manage a single persistent positioning feature or use a Manufacturing Model for a completely non-destructive CAM-only placement workflow.
