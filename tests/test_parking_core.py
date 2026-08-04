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
        self.assertEqual(horizontal["stall_count"], 162)
        self.assertEqual(horizontal["perimeter_stalls"], 76)
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
        # The asymmetric tip chamfer has one square turn and one obtuse turn.
        square_corners = [
            core.interior_angle_deg(outer, index)
            for index in range(len(outer))
            if abs(core.interior_angle_deg(outer, index) - 90.0) < 0.1
        ]
        self.assertGreaterEqual(len(square_corners), 2)
        self.assertIn(horizontal["chamfer_side"], (-1, 1))
        inner = core.as_xy_polygon(horizontal["ring_inner_poly"])
        for x, y in inner:
            self.assertAlmostEqual(
                core.distance_to_polygon(outer, x, y),
                core.RING_WIDTH,
                delta=0.1,
            )

    def test_universal_ring_has_constant_width_and_driveable_centerline(self):
        parcels = [
            [(0, 0), (300, 0), (300, 200), (0, 200)],
            REFERENCE_SITE,
            [(0, 0), (180, 0), (360, 160), (0, 420)],
            [(0, 0), (240, 0), (400, 180), (0, 500)],
            [(0, 0), (300, 0), (360, 180), (250, 350), (0, 300)],
        ]
        for parcel in parcels:
            sides = (
                (-1, 1)
                if core.acute_vertices(parcel, core.MIN_DRIVE_CORNER_DEG)
                else (1,)
            )
            for side in sides:
                outer, inner = core.ring_band_points(
                    parcel,
                    0.0,
                    5.0,
                    5.0 + core.RING_WIDTH,
                    chamfer_side=side,
                )
                self.assertIsNotNone(outer)
                self.assertIsNotNone(inner)
                self.assertTrue(core.ring_drive_is_acceptable(outer, inner))

                outer_xy = core.as_xy_polygon(outer)
                inner_xy = core.as_xy_polygon(inner)
                centerline = core.offset_polygon_edges(
                    outer_xy,
                    [core.RING_WIDTH * 0.5] * len(outer_xy),
                )
                self.assertFalse(core.drive_path_has_sharp_turn(centerline))
                for x, y in inner_xy:
                    self.assertAlmostEqual(
                        core.distance_to_polygon(outer_xy, x, y),
                        core.RING_WIDTH,
                        delta=0.1,
                    )

        # A nominally square loop can still be undriveable if consecutive
        # corners are too close to fit two R15 tangencies.
        short_loop = [(0, 0), (40, 0), (40, 40), (0, 40)]
        self.assertFalse(core.ring_drive_is_acceptable(short_loop))

    def test_land_use_splits_non_drivable_and_standing(self):
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
        land = core.layout_land_use(REFERENCE_SITE, horizontal, 5.0, street, 0.0)
        self.assertGreater(len(land["non_drivable"]), 0)
        self.assertEqual(len(land["standing"]), horizontal["stall_count"])
        # Tip pocket + end-caps/islands are non-drivable; aisle is residual.
        self.assertIsNotNone(land["moving_hint"])
        self.assertEqual(land["moving_hint"]["nominal_width"], core.RING_WIDTH)


if __name__ == "__main__":
    unittest.main()
