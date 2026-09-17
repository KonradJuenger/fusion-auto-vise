import math
import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'AutoVise'))
from autovise_rules import evaluate, slider_target, gap_limits, rectangle_in_polygon


class GripRules(unittest.TestCase):
    def setUp(self):
        self.parallel = dict(height_mm=38, thickness_mm=4, length_mm=100, quantity=2)

    def test_actual_library_pair_allows_longitudinal_overhang(self):
        r = evaluate((53, 52, 10), 'X', 42, parallel=self.parallel, minimum_grip=3, jaw_width=100)
        self.assertTrue(r.valid)
        self.assertEqual((r.opening, r.grip, r.protrusion), (53, 4, 6))

    def test_no_engagement_and_buried_stock(self):
        for height, reason in ((42, 'engagement'), (30, 'below the jaw top')):
            p = dict(self.parallel, height_mm=height)
            self.assertTrue(any(reason in x for x in evaluate((53, 52, 10), 'X', 42, parallel=p).reasons))

    def test_orientation_rechecks_pair_width_and_opening_limit(self):
        x = evaluate((50, 7, 10), 'X', 42, parallel=self.parallel, opening_limits=(0, 40))
        y = evaluate((50, 7, 10), 'Y', 42, parallel=self.parallel, opening_limits=(0, 40))
        self.assertTrue(any('maximum' in x for x in x.reasons))
        self.assertTrue(any('clearance' in x for x in y.reasons))
        self.assertEqual(y.opening, 7)

    def test_minimum_is_explicit(self):
        self.assertTrue(evaluate((50, 50, 10), 'X', 42, parallel=self.parallel).valid)
        self.assertFalse(evaluate((50, 50, 10), 'X', 42, parallel=self.parallel, minimum_grip=5).valid)

    def test_manual_support_below_seat(self):
        self.assertFalse(evaluate((50, 50, 60), 'X', 42, manual_grip=50).valid)

    def test_inventory_and_invalid_dimensions(self):
        self.assertFalse(evaluate((50, 50, 10), 'X', 42, parallel=dict(self.parallel, quantity=1)).valid)
        for value in (0, -1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                evaluate((50, 50, 10), 'X', 42, parallel=dict(self.parallel, height_mm=value))

    def test_mm_and_inch_equivalent_inputs(self):
        a = evaluate((50.8, 25.4, 12.7), 'Y', 42, manual_grip=4)
        b = evaluate(tuple(x * 25.4 for x in (2, 1, .5)), 'Y', 42, manual_grip=4)
        self.assertEqual(a, b)


class SliderRules(unittest.TestCase):
    def test_both_directions_and_nonzero_closed_gap(self):
        self.assertAlmostEqual(slider_target(2, 3, 5, 1), 4)
        self.assertAlmostEqual(slider_target(-2, 3, 5, -1), -4)

    def test_repeated_update_is_idempotent(self):
        self.assertEqual(slider_target(4, 5, 5, 1), 4)

    def test_motion_and_travel_rejection(self):
        for slope in (0, .5, 2, math.nan):
            with self.assertRaises(ValueError):
                slider_target(0, 1, 5, slope)
        with self.assertRaises(ValueError):
            slider_target(0, 1, 5, 1, (0, 3))

    def test_limit_direction_and_unknown_limit(self):
        self.assertEqual(gap_limits(-2, 3, -1, (-5, 0)), (1, 6))
        self.assertEqual(gap_limits(-2, 3, -1, (None, 0)), (1, None))


class ContactRules(unittest.TestCase):
    def test_notch_below_grip_is_allowed_but_not_in_grip(self):
        polygon = [(-5, 0), (5, 0), (5, -5), (1, -5), (1, -3), (-1, -3), (-1, -5), (-5, -5)]
        self.assertTrue(rectangle_in_polygon(polygon, -2, 2, -1, 0))
        self.assertFalse(rectangle_in_polygon(polygon, -2, 2, -4, 0))

    def test_chamfer_and_outside_band(self):
        polygon = [(-4, 0), (4, 0), (5, -1), (5, -5), (-5, -5), (-5, -1)]
        self.assertFalse(rectangle_in_polygon(polygon, -5, 5, -2, 0))
        self.assertTrue(rectangle_in_polygon(polygon, -3, 3, -2, 0))


if __name__ == '__main__':
    unittest.main()
