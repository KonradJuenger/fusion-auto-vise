# Fusion Auto Vise

Experimental Fusion add-in that places a vise from the **stock defined by a Manufacture Setup**, without requiring a separate stock body in Design.

## V0.2

This version is built against the reference files in `ref/`:

- `ref/vise.f3d`, with components named `fixed jaw` and `movable jaw`
- `ref/loch9mm.f3z`, representative machining file with `Setup1`

The first placement test exposed two problems in V0.1: stock placement was reconstructed from several setup parameters instead of using Fusion's evaluated stock bounds, and the movable jaw was transformed before the linked vise root was in its final assembly position.

V0.2 changes the flow to:

1. Select a Manufacture Setup.
2. Read Fusion's evaluated `stockXLow/High`, `stockYLow/High`, and `stockZLow/High` values.
3. Optionally draw the interpreted stock as a translucent cyan debug box with Setup WCS axes.
4. Insert the vise as an externally referenced component.
5. Detect `fixed jaw` and `movable jaw` by name.
6. Position the complete vise from the fixed jaw against the selected stock face.
7. Re-read the jaw occurrences in the final assembly context.
8. Move the movable jaw to the required stock width/depth.
9. Optionally add the vise occurrence to the Setup as a fixture.
10. Write detailed placement diagnostics to `AutoVise/last_debug.txt` and Fusion's application log.

The fixed-jaw transverse centering is now calculated from the fixed jaw itself rather than the complete vise bounding box, so opening the movable jaw cannot shift the vise center calculation.

## Install

Copy the `AutoVise` folder into your Fusion add-ins directory, or add it through **Utilities > Add-Ins > Scripts and Add-Ins > Add-Ins > +**.

After updating an existing installation, stop and restart the add-in, or restart Fusion. This also refreshes the toolbar command definition and its icon resources.

The command appears in an **Auto Vise** panel in the Manufacture workspace.

## Reference test

1. Open/import `ref/loch9mm.f3z` in Fusion.
2. Make sure `Setup1` exists.
3. Save the vise master in the **same Fusion project** as the machining file.
4. Start Auto Vise.
5. Use:
   - Setup: `Setup1`
   - Clamp axis: `Y`
   - Fixed jaw side: `Y-`
   - Grip depth: `4 mm`
   - Show debug stock + write log: enabled
6. Select the vise master file in the Fusion cloud dialog.

The cyan debug box should coincide exactly with the CAM stock. If it does not, the stock/WCS interpretation is the first thing to fix. If the cyan stock is correct but the vise is wrong, `last_debug.txt` contains the root, fixed-jaw, moving-jaw, WCS, and bounding-box transforms needed to isolate the placement problem.

## Current limitations

- Fixed Size Box and Relative Size Box stock only.
- Assumes the vise master is axis-aligned before insertion and local Z is up.
- Master vise component names are currently fixed to `fixed jaw` and `movable jaw`.
- Moving a nested occurrence inside an external reference is still Fusion-dependent. V0.2 tries `rootComponent.transformOccurrences` first and falls back to `Occurrence.transform2`.
- Arbitrarily rotated vise-master coordinate systems are not handled yet.
