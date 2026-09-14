"""V0.10 diagnostics/fixes layered on top of V0.9.

Main goals:
- resolve the CAM/design frame before anything can abort;
- drive the slider joint in the assembly context of the inserted vise occurrence;
- force a real state change before writing the target, so stale slideValue state cannot
  suppress geometry updates;
- log native joint state, proxy joint state, occurrence transforms and geometric gap.
"""
import adsk.core
import adsk.fusion

import autovise_geometry as g
import autovise_v09 as v09

_ORIG_STOCK = None
_ORIG_SET_JAW = None


def _safe_name(obj):
    try:
        return obj.name or '<unnamed>'
    except Exception:
        return '<unnamed>'


def _safe_type(obj):
    try:
        return obj.objectType
    except Exception:
        return type(obj).__name__


def _assembly_context_name(obj):
    try:
        ctx = obj.assemblyContext
        return _safe_name(ctx) if ctx else '<native/no context>'
    except Exception:
        return '<unavailable>'


def _motion_value_mm(motion):
    try:
        return float(motion.slideValue) * 10.0
    except Exception:
        return None


def _occ_state(label, occ):
    rows = []
    try:
        rows.append(f'{label}: name={_safe_name(occ)} type={_safe_type(occ)} context={_assembly_context_name(occ)}')
        rows.append('  transform2=' + ', '.join(f'{x:.6f}' for x in occ.transform2.asArray()))
        bb = occ.preciseBoundingBox
        rows.append(
            f'  bbox=({bb.minPoint.x:.6f},{bb.minPoint.y:.6f},{bb.minPoint.z:.6f})..'
            f'({bb.maxPoint.x:.6f},{bb.maxPoint.y:.6f},{bb.maxPoint.z:.6f}) cm'
        )
    except Exception as exc:
        rows.append(f'{label}: <state read failed: {exc}>')
    return rows


def _joint_endpoints(joint):
    out = []
    for attr in ('occurrenceOne', 'occurrenceTwo'):
        try:
            occ = getattr(joint, attr)
            out.append(f'{attr}={_safe_name(occ) if occ else "<none>"}')
        except Exception:
            out.append(f'{attr}=<unavailable>')
    return ', '.join(out)


def _proxy_slider_candidates(vise, moving, lines):
    """Return occurrence-specific slider joint proxies, best candidate first."""
    found = []
    seen = set()

    def add_joint(joint, source, score_bonus=0):
        if not joint:
            return
        try:
            motion = adsk.fusion.SliderJointMotion.cast(joint.jointMotion)
            if not motion:
                return
            token = ''
            try:
                token = joint.entityToken
            except Exception:
                token = f'{id(joint)}'
            key = (token, _assembly_context_name(joint), source)
            if key in seen:
                return
            seen.add(key)
            score = score_bonus
            name = (_safe_name(joint)).lower()
            if 'slider' in name:
                score += 5
            if 'jaw' in name:
                score += 5
            ends = _joint_endpoints(joint).lower()
            if 'movable jaw' in ends:
                score += 20
            if _assembly_context_name(joint) != '<native/no context>':
                score += 30
            found.append((score, source, joint, motion))
        except Exception as exc:
            lines.append(f'  candidate add failed from {source}: {exc}')

    # Best route: Autodesk documents that Occurrence.joints returns proxies in the same
    # assembly context as the occurrence. The movable jaw occurrence is already a proxy.
    try:
        joints = moving.joints
        lines.append(f'Movable-jaw occurrence.joints count={joints.count}')
        for i in range(joints.count):
            add_joint(joints.item(i), 'moving.joints proxy', 100)
    except Exception as exc:
        lines.append(f'Movable-jaw occurrence.joints enumeration failed: {exc}')

    # Explicit proxy route from the native joint in the inserted vise's referenced component.
    component = vise.component
    native_lists = []
    try:
        native_lists.append(('component.joints', component.joints))
    except Exception:
        pass
    try:
        native_lists.append(('component.allJoints', component.allJoints))
    except Exception:
        pass

    native_seen = set()
    for source_name, joints in native_lists:
        try:
            for i in range(joints.count):
                joint = joints.item(i)
                try:
                    token = joint.entityToken
                except Exception:
                    token = f'{id(joint)}'
                if token in native_seen:
                    continue
                native_seen.add(token)
                try:
                    native_motion = adsk.fusion.SliderJointMotion.cast(joint.jointMotion)
                except Exception:
                    native_motion = None
                if not native_motion:
                    continue
                lines.append(
                    f'Native slider candidate {_safe_name(joint)} from {source_name}: '
                    f'context={_assembly_context_name(joint)}, slideValue={_motion_value_mm(native_motion)} mm, '
                    f'{_joint_endpoints(joint)}'
                )
                try:
                    proxy = joint.createForAssemblyContext(vise)
                    if proxy:
                        proxy_motion = adsk.fusion.SliderJointMotion.cast(proxy.jointMotion)
                        lines.append(
                            f'  createForAssemblyContext -> {_safe_name(proxy)} '
                            f'context={_assembly_context_name(proxy)}, '
                            f'slideValue={_motion_value_mm(proxy_motion)} mm, {_joint_endpoints(proxy)}'
                        )
                        add_joint(proxy, 'createForAssemblyContext(vise)', 80)
                    else:
                        lines.append('  createForAssemblyContext returned null')
                except Exception as exc:
                    lines.append(f'  createForAssemblyContext failed: {exc}')

    found.sort(key=lambda item: item[0], reverse=True)
    lines.append(f'Assembly-context slider candidates={len(found)}')
    for score, source, joint, motion in found:
        lines.append(
            f'  score={score} source={source} name={_safe_name(joint)} '
            f'context={_assembly_context_name(joint)} slideValue={_motion_value_mm(motion)} mm '
            f'{_joint_endpoints(joint)}'
        )
    return found


def _drive_proxy_gap(design, vise, required_gap, lines):
    initial = g._vise_info(vise)
    moving = initial['moving']
    lines.append('=== V0.10 ASSEMBLY-CONTEXT JOINT DEBUG ===')
    lines.append(
        f'Initial geometric gap={initial["gap"]*10.0:.3f} mm; target={required_gap*10.0:.3f} mm'
    )
    lines.extend(_occ_state('Vise occurrence', vise))
    lines.extend(_occ_state('Movable jaw occurrence', moving))

    candidates = _proxy_slider_candidates(vise, moving, lines)
    if not candidates:
        lines.append('No assembly-context slider proxy found; delegating to previous driver.')
        return _ORIG_SET_JAW(design, vise, required_gap, lines)

    failures = []
    for score, source, joint, motion in candidates:
        lines.append(
            f'TRY slider proxy: source={source}, joint={_safe_name(joint)}, '
            f'context={_assembly_context_name(joint)}'
        )
        try:
            before_value = float(motion.slideValue)
        except Exception as exc:
            failures.append(f'{source}: cannot read slideValue: {exc}')
            continue

        try:
            # Force a real change first. This fixes the observed state where a freshly inserted
            # occurrence has a 49 mm geometric gap while the native joint still reports the
            # previous run's 51.887 mm value. Writing 51.887 -> 51.887 is otherwise a no-op.
            reset_candidates = (0.0, required_gap * 0.5)
            reset_done = False
            for reset in reset_candidates:
                if abs(reset - before_value) < 1e-6:
                    continue
                try:
                    motion.slideValue = reset
                    adsk.doEvents()
                    reset_info = g._vise_info(vise)
                    lines.append(
                        f'  forced reset requested={reset*10.0:.3f} mm; '
                        f'actualSlide={float(motion.slideValue)*10.0:.3f} mm; '
                        f'geometricGap={reset_info["gap"]*10.0:.3f} mm'
                    )
                    lines.extend(_occ_state('  movable after reset', reset_info['moving']))
                    reset_done = True
                    break
                except Exception as exc:
                    lines.append(f'  reset {reset*10.0:.3f} mm failed: {exc}')
            if not reset_done:
                lines.append('  could not force a reset, continuing with target trials')

            trials = []
            for target in (required_gap, -required_gap):
                try:
                    motion.slideValue = target
                    adsk.doEvents()
                    trial = g._vise_info(vise)
                    actual_slide = float(motion.slideValue)
                    err = abs(trial['gap'] - required_gap)
                    lines.append(
                        f'  target={target*10.0:+.3f} mm -> actualSlide={actual_slide*10.0:.3f} mm, '
                        f'geometricGap={trial["gap"]*10.0:.3f} mm, error={err*10.0:.3f} mm'
                    )
                    lines.extend(_occ_state('  movable after target', trial['moving']))
                    trials.append((err, actual_slide, trial))
                except Exception as exc:
                    lines.append(f'  target {target*10.0:+.3f} mm failed: {exc}')

            if not trials:
                failures.append(f'{source}: no target trial succeeded')
                continue
            trials.sort(key=lambda item: item[0])
            best_err, best_value, _ = trials[0]

            # Force away from the final value again if the final trial left us there, then set it
            # once more. This makes the final state deterministic even with cached drive state.
            if abs(float(motion.slideValue) - best_value) < 1e-7:
                try:
                    motion.slideValue = 0.0 if abs(best_value) > 1e-6 else required_gap * 0.25
                    adsk.doEvents()
                except Exception:
                    pass
            motion.slideValue = best_value
            adsk.doEvents()
            final = g._vise_info(vise)
            final_err = abs(final['gap'] - required_gap)
            lines.append(
                f'  FINAL proxy slide={float(motion.slideValue)*10.0:.3f} mm; '
                f'geometricGap={final["gap"]*10.0:.3f} mm; error={final_err*10.0:.3f} mm'
            )
            lines.extend(_occ_state('  final movable jaw', final['moving']))
            if final_err <= 0.005:
                return final, f'assembly-context slider {_safe_name(joint)} via {source}'
            failures.append(
                f'{source}/{_safe_name(joint)} final gap={final["gap"]*10.0:.3f} mm '
                f'(target {required_gap*10.0:.3f})'
            )
        except Exception as exc:
            failures.append(f'{source}/{_safe_name(joint)}: {exc}')

    raise RuntimeError(
        'No assembly-context slider proxy could produce the requested jaw gap. '
        + ' | '.join(failures)
    )


def _stock_with_early_frame(setup):
    stock = _ORIG_STOCK(setup)
    # Run frame resolution here, before vise insertion/jaw adjustment. This guarantees that a
    # failure in the joint logic still produces the design/CAM coordinate diagnostics.
    try:
        v09.resolve_frame(setup, stock)
    except Exception as exc:
        v09.DEEP_ROWS.append(f'Early frame resolution failed: {exc}')
    return stock


def apply_patches():
    global _ORIG_STOCK, _ORIG_SET_JAW
    # V0.9 has already patched these by the time this is called.
    _ORIG_STOCK = g._stock
    _ORIG_SET_JAW = g._set_jaw_gap
    g._stock = _stock_with_early_frame
    g._set_jaw_gap = _drive_proxy_gap
