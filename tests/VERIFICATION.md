# Auto Vise V2 verification

## Status on 2026-09-17

Development build. Fixture replacement remains unresolved; not ready for normal use.

## Explicit master role change

The fitting dialog now resolves four native master attributes, with no automatic
detection or fallback to old instance selections. Configure Vise Master is available
in the Design workspace and writes only on OK. Master revisions invalidate old
instance calibration. Setup options remain setup-specific.

Automated: 23 tests passed (13 calculations and 10 master contract tests), including
missing/duplicate/stale roles, changed face geometry, separate instance contexts,
reconfiguration, rejecting writes to linked masters, and calibration invalidation.
All active modules compile. These boundary-double tests do not establish Fusion
attribute propagation or UI behavior. The new master configuration command and
save/update of attributes through linked files still require live Fusion verification.

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

Resolve fixture replacement, then check create/edit/reopen, Cancel, repeated runs
without duplicate geometry, X/Y and both sides, parallel changes, invalid inputs,
setup isolation, renamed components, nested geometry, save/reopen, and machine
placement stability. Report measured results after command completion as well as
inside the command.

The active V2 modules compile independently. Compiling every historical module
also encounters a pre-existing syntax error in inactive `autovise_v10.py`.
