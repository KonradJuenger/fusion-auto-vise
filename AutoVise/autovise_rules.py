"""Rectangular-stock workholding calculations. All lengths are millimetres.

This module deliberately has no Fusion dependency. Fit is not holding-force
certification; minimum grip is a user/shop requirement, never a safety default.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Evaluation:
    opening: float
    grip: float
    protrusion: float
    support: float
    reasons: tuple

    @property
    def valid(self):
        return not self.reasons


def evaluate(stock, axis, jaw_height, *, parallel=None, manual_grip=0,
             minimum_grip=0, jaw_width=None, opening_limits=(None, None)):
    """Evaluate a single pair standing on its specified height, or manual grip.

    jaw_height is gripping-face top minus seat plane. A pair needs two pieces,
    each entirely under the stock and within the usable jaw width. This is a
    conservative flat-face fit check, not pocket/chamfer/interference analysis.
    """
    if axis not in ('X', 'Y'):
        raise ValueError('Clamp direction must be X or Y.')
    sx, sy, sz = stock
    values = [sx, sy, sz, jaw_height, manual_grip, minimum_grip]
    if not all(math.isfinite(v) for v in values):
        raise ValueError('Dimensions must be finite numbers.')
    if min(sx, sy, sz, jaw_height) <= 0 or minimum_grip < 0:
        raise ValueError('Stock and jaw height must be positive; minimum grip cannot be negative.')
    opening, width = (sx, sy) if axis == 'X' else (sy, sx)
    reasons = []
    support = jaw_height - manual_grip
    if parallel is not None:
        support = float(parallel['height_mm'])
        thickness = float(parallel['thickness_mm'])
        length = float(parallel['length_mm'])
        if not all(math.isfinite(v) and v > 0 for v in (support, thickness, length)):
            raise ValueError('Parallel dimensions must be finite and positive.')
        if parallel.get('quantity', 2) < 2:
            reasons.append('Two matching parallels are required; inventory has fewer than two.')
        if 2 * thickness >= opening - 1e-6:
            reasons.append('Two parallels do not fit with clearance between the jaws.')
        if min(length, width, jaw_width if jaw_width is not None else width) <= thickness:
            reasons.append('Insufficient length of contact under the stock.')
    grip = jaw_height - support
    protrusion = sz - grip
    if grip <= 1e-6:
        reasons.append('No positive jaw engagement.')
    if protrusion <= 1e-6:
        reasons.append('Stock is at or below the jaw top.')
    if support < -1e-6:
        reasons.append('Stock bottom would be below the support seat.')
    if grip + 1e-6 < minimum_grip:
        reasons.append(f'Grip is below the user minimum of {minimum_grip:g} mm.')
    low, high = opening_limits
    if low is not None and opening < low - 1e-6:
        reasons.append(f'Opening is below the slider minimum ({low:g} mm).')
    if high is not None and opening > high + 1e-6:
        reasons.append(f'Opening exceeds the slider maximum ({high:g} mm).')
    return Evaluation(opening, grip, protrusion, support, tuple(reasons))


def slider_target(current_slide, current_gap, required_gap, slope, limits=(None, None)):
    """Direct slider solve after a measured, per-instance calibration."""
    if not all(math.isfinite(v) for v in (current_slide, current_gap, required_gap, slope)):
        raise ValueError('Invalid slider measurements.')
    if abs(abs(slope) - 1) > .02:
        raise ValueError('Slider does not produce independent linear jaw motion.')
    target = current_slide + (required_gap - current_gap) / slope
    low, high = limits
    if low is not None and target < low - 1e-6 or high is not None and target > high + 1e-6:
        raise ValueError('Required opening exceeds the slider travel limits.')
    return target


def gap_limits(slide, gap, slope, limits):
    low, high = limits
    result = [None if v is None else gap + slope * (v - slide) for v in (low, high)]
    return tuple(result if slope > 0 else reversed(result))


def rectangle_in_polygon(polygon, left, right, bottom, top, tolerance=1e-5):
    """Conservative exact band coverage for a simple polygon with straight edges.

    Cross-section endpoints vary linearly between vertex heights. Checking both
    sides of every critical height detects notches and chamfers in the grip band.
    """
    if right <= left or top <= bottom:
        return False
    levels = sorted({bottom, top} | {y for _, y in polygon if bottom < y < top})
    samples = []
    for low, high in zip(levels, levels[1:]):
        epsilon = min(tolerance / 10, (high - low) / 4)
        samples.extend((low + epsilon, high - epsilon))
    for y in samples:
        crossings = []
        for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
            if (y1 <= y < y2) or (y2 <= y < y1):
                crossings.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        crossings.sort()
        if not any(a <= left + tolerance and b >= right - tolerance
                   for a, b in zip(crossings[::2], crossings[1::2])):
            return False
    return bool(samples)
