# Fusion Auto Vise

Fusion add-in that places a linked vise from the stock defined by a Manufacture Setup, without requiring a separate stock body in Design.

## V0.3

Built against the reference files in `ref/`.

Main changes from V0.2:

- Uses Fusion's evaluated `stockXLow/High`, `stockYLow/High`, and `stockZLow/High` values.
- Default fixed-jaw side is now `Y+`.
- No longer treats the full `fixed jaw` component bounding box as the gripping face. The reference vise has the long base inside that component, so V0.3 searches its B-Rep faces for the actual gripping face.
- The resulting true jaw gap is used to move `movable jaw` to the CAM stock width/depth.
- The selected vise master is remembered by `DataFile.id` in `AutoVise/settings.json`. After the first selection, normal runs bypass the Fusion cloud file picker. Enable `Choose/change vise master` when you want to select a different one.
- Debug output now logs the selected fixed-jaw contact face and the other candidate faces.
- Keeps the cyan stock debug box and `AutoVise/last_debug.txt`.

## Install / update

Copy the `AutoVise` folder into your Fusion add-ins directory, or use **Utilities > Add-Ins > Scripts and Add-Ins > Add-Ins > +**.

After updating, stop and restart the add-in, or restart Fusion.

## Current test settings

- Clamp axis: `Y`
- Fixed jaw side: `Y+`
- Grip depth: `4 mm`
- Show debug stock + write log: enabled

On the first run, select the vise master from Fusion cloud. It is remembered afterwards.

## Current limitations

- Fixed Size Box and Relative Size Box stock only.
- Vise master must contain child components named `fixed jaw` and `movable jaw`.
- Vise master is expected to be axis-aligned before insertion and local Z is up.
- The machining file and vise master must currently be in the same Fusion project for linked insertion.
- Arbitrarily rotated vise-master coordinate systems are not handled yet.
