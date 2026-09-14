# Fusion Auto Vise

Fusion add-in that fits a linked vise around CAM stock and uses Fusion's native **Setup > Part Position** for placement inside the machine model.

## V0.5 architecture

V0.4 tried to calibrate a Design-space vise transform against a machine that only exists in Manufacture. That was the wrong coordinate boundary.

V0.5 separates the problem into two layers:

`local setup / stock -> vise + jaw geometry`

`complete setup assembly -> Fusion Part Position -> machine model`

Normal operation is now:

1. Read evaluated CAM box stock.
2. Insert a fresh linked vise master.
3. Place the fixed gripping face against the selected stock side at the requested grip depth.
4. Move the movable jaw to the exact stock width/depth.
5. Add the vise as a Setup fixture.
6. Set Fusion's native Part Position parameters:
   - `job_positionXOffset`
   - `job_positionYOffset`
   - `job_positionZOffset`
7. Optionally remember those XYZ values for future setups.
8. Write `AutoVise/last_debug.txt` while debugging.

The Design geometry is no longer moved into machine coordinates. Part Position is responsible for locating the complete part + fixture assembly in the machine model.

## Dialog

Current controls:

- Setup
- Clamp along setup axis: X / Y
- Stock side at fixed jaw: X+, X-, Y+, Y-
- Grip depth
- Machine Part Position X
- Machine Part Position Y
- Machine Part Position Z
- Remember machine XYZ
- Add vise to Setup fixtures
- Choose/change vise master
- Show local stock + write log

The first selected vise master is remembered using its Fusion `DataFile.id`, so the cloud picker is skipped on normal runs.

The machine XYZ values are stored in `AutoVise/settings.json` under `machine_part_position_mm` when **Remember machine XYZ** is enabled.

## Recommended workflow

1. Assign the correct machine to the Fusion Setup.
2. Open Auto Vise.
3. Use the normal workholding settings, currently typically:
   - Clamp axis: `Y`
   - Fixed jaw side: `Y+`
   - Grip depth: `4 mm`
4. Enter provisional Part Position X/Y/Z values.
5. Apply and inspect the machine simulation / Setup Part Position result.
6. Tune X/Y/Z until the vise sits at the physical mounting position.
7. Keep **Remember machine XYZ** enabled.

After that, new setups can reuse the remembered machine position while the script adapts the jaw opening and local vise placement to each stock size.

## Current limitations

- Fixed Size Box and Relative Size Box stock only.
- Vise master must contain child components named `fixed jaw` and `movable jaw`.
- The reference vise's fixed gripping face is detected from B-Rep geometry because the `fixed jaw` component also contains the long vise base.
- The machining file and vise master must currently be in the same Fusion project for linked insertion.
- Part Position X/Y/Z requires the selected Setup to expose Fusion's `job_positionXOffset`, `job_positionYOffset`, and `job_positionZOffset` CAM parameters. In practice this means the Setup should have an appropriate machine / Part Position context.
- V0.5 handles XYZ Part Position offsets only. Additional machine attachment-point/orientation controls can be added later if the API exposes enough information for the selected machine.
