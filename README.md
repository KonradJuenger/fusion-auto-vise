# Fusion Auto Vise

Experimental Fusion add-in that places a vise from the **stock defined by a Manufacture Setup**, without requiring a separate stock body in Design.

## V1

The first version is intentionally narrow. It is built against the reference files in `ref/`:

- `ref/vise.f3d`
  - contains components named `fixed jaw` and `movable jaw`
- `ref/loch9mm.f3z`
  - representative machining file with `Setup1`
  - Relative Size Box stock
  - 50 x 50 mm model envelope, 1 mm top stock offset
  - WCS at `top center`

V1 can:

1. Select an existing Manufacture Setup.
2. Read **Fixed Size Box** or **Relative Size Box** stock directly from CAM parameters.
3. Resolve the selected stock-box WCS point, including Fusion's top/middle/bottom corner and side points.
4. Select a vise master through Fusion's cloud file dialog.
5. Insert the vise as an **externally referenced component**.
6. Detect `fixed jaw` and `movable jaw` by name.
7. Detect the vise's clamping axis from those jaw positions.
8. Attempt to move the movable jaw to the CAM stock width/depth.
9. Place the fixed jaw against the selected stock side at a specified grip depth.
10. Optionally add the vise occurrence to the Setup as a fixture.

## Important V1 test

The main unresolved Fusion behavior is whether `Occurrence.transform2` can move the `movable jaw` child occurrence while its parent vise is an external reference.

The add-in intentionally tries this and reports the result. If Fusion treats that linked child as read-only, the next version will change the master architecture. The likely robust solution is to keep the static vise body externally linked while making the movable jaw a separately controllable linked occurrence.

## Install

Copy the `AutoVise` folder into your Fusion add-ins directory, or add it through **Utilities > Add-Ins > Scripts and Add-Ins > Add-Ins > +**.

Run **AutoVise**. It creates an **Auto Vise** panel in the Manufacture workspace.

## Test with the reference setup

1. Open/import `ref/loch9mm.f3z` in Fusion.
2. Make sure `Setup1` exists.
3. Make sure the vise master is saved in the **same Fusion project** as the machining file. Linked insertion requires this.
4. Start the add-in.
5. In Manufacture, click **Auto Vise**.
6. Use:
   - Setup: `Setup1`
   - Clamp axis: `Y`
   - Fixed jaw side: `Y-`
   - Grip depth: `4 mm`
7. Click OK and select the vise master file in the Fusion cloud dialog.

Send the resulting dialog/error text if the jaw move fails. That result determines the V2 vise-master architecture.

## Current limitations

- Box stock only.
- Relative Size Box currently supports Fusion's normal `simple` side + top/bottom offset mode.
- Requires WCS origin from a stock/model box point.
- Assumes the vise's local Z is up.
- Master vise component names are currently fixed to `fixed jaw` and `movable jaw`.
- Linked child-jaw movement still needs to be tested inside Fusion.
