# Fusion Auto Vise

## V2 development status

The active entry point now loads V2. **This is a development build, not a verified release.**
The V0.17 implementation remains in Git at `631c35c` and in the legacy modules.

V2 edits an existing vise instance, reads explicit roles from its master, evaluates
parallel choices, and creates persistent parallel bodies. It supports a fixed jaw,
a movable jaw driven by an existing slider joint, opposing planar gripping faces,
a planar support seat, and fixed-size or relative-size rectangular box stock.

### Install or update the development build

1. Keep the complete `AutoVise` folder together, including `Resources` and `parallels.json`.
2. In Fusion, open **Scripts and Add-Ins** (`Shift+S`), select the Add-Ins tab,
   and add the `AutoVise` folder if it is not already registered.
3. Stop the existing Auto Vise add-in, then run it again to load the changed modules.
4. Open a machining document with a rectangular-stock setup and use the
   **Auto Vise** panel in Manufacture > Tools.

### Configure and edit

First open the original vise design. In the Design workspace, use the **Auto Vise
Master** panel's **Configure Vise Master** command and select these four roles:

| Role | Select |
| --- | --- |
| `AUTO_VISE_FIXED_GRIP` | The inward-facing flat gripping face of the fixed jaw |
| `AUTO_VISE_MOVING_GRIP` | The opposing gripping face of the movable jaw |
| `AUTO_VISE_PARALLEL_SEAT` | The upward-facing stationary support face beneath the parallels |
| `AUTO_VISE_SLIDER` | The existing slider joint controlling jaw opening |

OK writes attributes on the native faces and joint in the master file. Cancel
writes nothing. Save the master, then update its linked instances in machining
documents. The labels are attributes, not browser display names; renaming a
component or joint does not change its role. Merely renaming a joint does not
assign a role. No construction point is needed.

Use **Insert Vise** for a new linked instance, then open **Auto Vise V2**.
Choose the intended setup and top-level vise instance. There is no face-selection
wizard or automatic face detection in the fitting dialog. All four roles must
resolve exactly once within that instance. Missing, duplicated, stale, or changed
face geometry stops the fit with an error naming the role. Reconfigure the master
after deliberate face changes, save it, and update the links.

The master must contain local jaw components; configure it directly, not through a
linked insertion. A role on a component used more than once in the same master is
ambiguous and rejected. Separate inserted copies of the complete master are supported.
Old V2 instance face selections are not migrated or used as a fallback. After
configuration, existing setup options remain; jaw calibration is renewed once
per instance when the master contract changes.

Select manual grip or a parallel pair, Setup X/Y, and the fixed-jaw side. The dialog
shows opening, grip depth, stock protrusion, and reasons why each parallel cannot
fit. A minimum grip of zero means no shop requirement has been specified.
Cancel leaves fitting unapplied; OK applies the selected setup's configuration.
Use a separate vise instance for each setup.

Part Position is optional. Its XYZ fields are offsets from Fusion's selected
reference. To use machine positioning, select the Table Attach Point in Fusion
and use an explicit stable point on the vise base rather than a changing fixture
bounding box. Leave **Change offsets** unchecked to preserve existing values.

### Verification

See [the V2 verification report](tests/VERIFICATION.md) for passed checks and
remaining integration failures. `AutoVise/last_run.json` records the latest fit;
`AutoVise/bootstrap_debug.txt` records add-in loading failures.

## Historical V0.12 documentation

The following describes the earlier implementation, not V2's configuration workflow.

Fusion add-in that fits a linked vise around CAM stock, optionally generates a selected pair of parallels, and uses Fusion's native **Setup > Part Position** for placement inside the machine model.

## V0.12

V0.12 adds a parallel library and automatic clamping-depth calculation.

### Vise master requirement

The `fixed jaw` component must contain a construction point named:

`AUTO_VISE_PARALLEL_SEAT`

This point represents the centerline location where the bottom of a parallel sits against the fixed jaw. Auto Vise reads it through an assembly-context proxy, so it follows the linked vise occurrence correctly.

### Included parallel set

The library in `AutoVise/parallels.json` contains the user's actual set:

- heights: 10, 14, 18, 22, 26, 30, 34, 38, 42 mm
- thickness: 4 mm
- length: 100 mm

The library is data-driven and can be extended later without changing the geometry code.

### Workflow

The **Stock support** dropdown now offers:

- Manual grip depth
- 10 mm parallels
- 14 mm parallels
- 18 mm parallels
- 22 mm parallels
- 26 mm parallels
- 30 mm parallels
- 34 mm parallels
- 38 mm parallels
- 42 mm parallels

When a parallel is selected Auto Vise:

1. reads CAM stock,
2. opens the vise to the selected stock X/Y dimension,
3. reads `AUTO_VISE_PARALLEL_SEAT`,
4. calculates the resulting clamping depth from jaw top, seat height and parallel height,
5. rejects a parallel if the resulting grip is impossible for the stock thickness,
6. positions the vise so the stock bottom sits exactly on the parallel top,
7. generates two physical 100 x 4 x H mm parallel bodies,
8. places one against the fixed jaw and one against the movable jaw,
9. adds vise + parallels to the Setup fixtures when enabled,
10. applies Fusion Part Position X/Y/Z.

Generated geometry is named:

- `AUTO_PARALLEL_FIXED`
- `AUTO_PARALLEL_MOVING`

and is deleted/recreated on each run to keep repeated runs deterministic.

## Existing behavior retained

- Fixed Size Box and Relative Size Box stock
- Setup X / Setup Y clamp direction
- +/- fixed jaw side
- manual grip-depth fallback
- occurrence-specific slider-joint driving
- deep CAM/design frame validation
- stale vise/fixture cleanup
- remembered vise master and machine offsets
- generated debug stock

## Important Part Position detail

`job_positionXOffset`, `job_positionYOffset`, and `job_positionZOffset` are offsets from Fusion's **Table Attach Point**, not absolute machine coordinates.

## Current limitations

- Vise master must contain child components named `fixed jaw` and `movable jaw`.
- Parallel mode requires `AUTO_VISE_PARALLEL_SEAT` in the fixed jaw component.
- A slider joint is strongly preferred for the movable jaw.
- The machining file and vise master must currently be in the same Fusion project for linked insertion.
- Fusion's machine Table Attach Point still needs manual selection because the current API does not expose machine-model geometry sufficiently for automatic selection.
- Vise master Z is assumed to be up.
