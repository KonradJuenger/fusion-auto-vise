# Auto Vise V2 verification

## Status on 2026-09-17

Development build. Explicit master roles and stable parallel updates passed the
live checks below. Full UI and machine-placement acceptance is still outstanding.

## Explicit master role change

The fitting dialog now resolves four native master attributes, with no automatic
detection or fallback to old instance selections. Configure Vise Master is available
in the Design workspace and writes only on OK. Master revisions invalidate old
instance calibration. Setup options remain setup-specific.

Automated: 35 tests passed (13 calculations, 10 master contract tests, four
CAM fixture-context tests, four vise-selection tests, and four command tests), including
missing/duplicate/stale roles, changed face geometry, separate instance contexts,
reconfiguration, rejecting writes to linked masters, and calibration invalidation.
All active modules compile. Boundary doubles alone do not establish Fusion
attribute propagation or UI behavior.

Live Fusion MCP checks after restart:

- Assigned all four roles to the known gripping faces, seat and Slider 1 in the
  open master. Saved with user approval as `vise v7`.
- Inserted a new linked `vise v7` into `autovisetest v4`. All four native role
  attributes resolved through the linked assembly context.
- Created dedicated `Auto Vise V2 local test` (setup ID 4). Original Setup2
  retained zero fixtures.
- Initial X fit: 53.1962222 mm opening, 7 mm grip, 3 mm protrusion, 38 mm parallels.
- Y/+ fit: 51.8872278 mm opening; existing component and body tokens remained valid.
- Subsequent X/+ with 42 mm parallels, manual grip, Y/- with 38 mm parallels,
  and X/- with 38 mm parallels passed. Body volumes changed from 16.8 to 15.2 cm3.
- Manual grip removed parallels from this setup's fixtures and hid the retained
  component. Returning to parallel support reused it. Fixture count was one in
  manual mode and two with parallels; root occurrence count stayed at two.
- Fixed CAM fixture matching to recognize native design occurrences inside CAM's
  extra assembly wrappers; the old root-only comparison missed these references.
- Full recompute passed. A separate read after command completion confirmed
  53.1962222 mm opening, two valid fixtures, two bodies, and healthy timeline.
- New master and fit buttons were registered through the MCP. Interactive dialog
  selection/Cancel and updating an existing linked instance remain unverified.

The test machining document remains unsaved; the master was saved explicitly.

Follow-up in `autovisetest v5`: the interactive dialog rejected a valid Manufacture
selection because its occurrence had a CAM assembly context. Selection resolution
now matches the occurrence/native occurrence against the design's top-level
instances, without promoting nested jaws. Live read-only verification using
`cam.designRootOccurrence.childOccurrences` resolved `vise v7:1` and validated
Setup2, Y, 42 mm parallels, 2 mm minimum: 51.8872278 mm opening and 3 mm grip.
Updated command code requires an add-in restart; the enabled OK button itself
has not yet been verified after that restart.

Second-run follow-up: saved Setup2 configuration and master roles resolved correctly.
The dialog restored selections from commandCreated, where Fusion explicitly forbids
addSelection. Restoration now runs once from command.activate, using a CAM-context
proxy; subsequent activation preserves current edits. Early inputChanged callbacks
are ignored. Live inspection confirmed that the saved vise can be recreated as a
CAM selection and its saved support evaluates as valid. Full interactive reopen
still needs verification after loading this change.

Dialog cleanup: main choices and a full-width three-line fit summary remain visible.
Fixture/preview/origin/offset controls and parallel/master details are collapsed.
Unrelated errors no longer display a misleading instruction to configure master roles.

Unified command: Manufacture now registers one promoted Auto Vise command with
existing/new modes. New insertion and fitting run together in execute; a failed
master profile marks the command failed for rollback. Two boundary tests cover
successful dispatch and failed-profile rollback signaling. The separate Insert Vise
command is removed. Design's master command is named Setup Vise, uses plain role
labels, and restores selections on activation. These new UI flows and actual Fusion
rollback for combined insertion/fitting require live verification after add-in reload.

Automated: 13 calculation tests passed, covering grip, invalid parallels,
orientation-dependent opening, unit equivalence, slider direction and limits,
repeat positioning, and jaw notches/chamfers.

Live Fusion in `autovisetest (v4~recovered)`:

- Inspection and linked insertion passed.
- Created `Auto Vise V2 local test` (setup ID 5); original Setup2 remains separate.
- Removing the full recompute allowed the requested 53.1962222 mm jaw opening.
- Two persistent bodies were created, but their creation reset vise placement.
  The previous success log was premature; this is not a passing placement test.
- Added a final placement/opening check after fixture changes.
- Root cause of the placement reset: linked insertion enabled Ground to Parent.
  Releasing that constraint on the selected root and capturing its position held
  placement, 53.1962222 mm opening, 7 mm grip, and two parallels. The fit took
  approximately 208 ms. Opening and orientation survived a full recompute.
- CAM retained stale references after replacing/deleting the old parallel component.
  Reading fixtures raised InternalValidationError. Fusion subsequently crashed
  during another inspection, before a queued repair ran. Exact crash trigger is
  unconfirmed. Fixture replacement has not been repaired or verified.

The test helper consumes mutation plans before running them, requires a matching
document name and a command transaction, and otherwise performs read-only
inspection. Use the development command, not the standalone script, for fit plans.

## Still required

Check interactive create/edit/reopen and Cancel, invalid inputs, multiple-setup
isolation, renamed components, nested geometry, existing-link updates, save/reopen,
and machine placement stability. The old crashed/recovered document's stale
fixture references have not been repaired; current checks used the clean v4 file.

The active V2 modules compile independently. Compiling every historical module
also encounters a pre-existing syntax error in inactive `autovise_v10.py`.

## 2026-09-18: active setup and optional machine offsets

Removed the Setup dropdown. Auto Vise captures the active CAM setup on opening;
with multiple setups and none active it requests activation instead of guessing.
Machine attachment/fixture-origin checks now apply only when changing Part Position
offsets. Ordinary fitting remains in setup coordinates and leaves those settings
unchanged. Machine simulation placement still needs its own verification.
39 automated tests pass, including active setup selection, ambiguous setup rejection,
ordinary fitting without machine attachment, and retained offset-change protection.
The updated dialog's OK button still requires live verification after add-in reload.
