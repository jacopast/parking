import math
import os
import sys
import unittest


RHINO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rhino")
sys.path.insert(0, RHINO_DIR)

import parking_core as core


REFERENCE_SITE = [
    (0.0, 0.0),
    (173.0, 0.0),
    (290.0, 188.0),
    (0.0, 407.0),
]


class BayIslandReferenceTests(unittest.TestCase):
    def test_reference_horizontal_option_has_four_double_loaded_bays(self):
        street = core.street_edge_from_index(REFERENCE_SITE, 0)
        layout = core.best_layout(
            REFERENCE_SITE,
            0.0,
            5.0,
            core.access_points_on_street_edge(street),
            street_edge=street,
        )
        horizontal = min(
            core.option_layouts(layout),
            key=lambda option: abs(core.angle_key(option["angle"])),
        )

        self.assertEqual(horizontal["run_count"], 4)
        self.assertEqual(horizontal["stall_count"], 159)
        self.assertEqual(horizontal["perimeter_stalls"], 73)
        self.assertEqual(
            horizontal["stall_count"] - horizontal["perimeter_stalls"],
            86,
        )
        self.assertTrue(
            all(len(run["rows"]) == 2 for run in horizontal["skeleton_runs"])
        )
        # Tip dead-zone landscape sits outside the ring, not in the aisle.
        outer = core.as_xy_polygon(horizontal["ring_outer_poly"])
        pockets = core.tip_pocket_islands(REFERENCE_SITE, horizontal, 0.0)
        self.assertGreaterEqual(len(pockets), 1)
        xs = [point[0] for point in pockets[0]]
        ys = [point[1] for point in pockets[0]]
        center = (sum(xs) / len(xs), sum(ys) / len(ys))
        self.assertFalse(core.point_inside(outer, center[0], center[1]))
        self.assertLess(
            math.hypot(center[0] - 0.0, center[1] - 407.0),
            120.0,
        )
        # Ring corners stay at least square after tip chamfer.
        self.assertGreaterEqual(
            core.polygon_min_interior_angle(outer),
            core.MIN_DRIVE_CORNER_DEG - 0.5,
        )


if __name__ == "__main__":
    unittest.main()
