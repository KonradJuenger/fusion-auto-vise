# Fusion Auto Vise

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
