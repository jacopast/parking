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
        double_loaded = [
            run for run in horizontal["skeleton_runs"] if len(run["rows"]) == 2
        ]
        self.assertEqual(len(double_loaded), 4)
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


class GeometryFailureRegressionTests(unittest.TestCase):
    """Cases from multi-geometry agent reviews (offset / multi-span / U-shape)."""

    U_SHAPE = [
        (0, 0), (300, 0), (300, 340), (260, 340),
        (260, 60), (40, 60), (40, 340), (0, 340),
    ]
    THIN_NECK_L = [
        (0, 0), (300, 0), (300, 50), (50, 50), (50, 340), (0, 340),
    ]
    ACUTE_WEDGE = [(0, 0), (400, 0), (0, 60)]

    def test_offset_rejects_flipped_u_shape_core(self):
        # Deep offset used to flip the U and invent a courtyard core.
        self.assertIsNone(core.offset_polygon(self.U_SHAPE, 47.0))
        shallow = core.offset_polygon(self.U_SHAPE, 5.0)
        self.assertIsNotNone(shallow)
        self.assertTrue(core.offset_result_is_valid(self.U_SHAPE, shallow, 5.0))
        self.assertGreater(core.signed_area(self.U_SHAPE) * core.signed_area(shallow), 0)

    def test_offset_rejects_impossible_acute_wedge(self):
        self.assertIsNone(core.offset_polygon(self.ACUTE_WEDGE, 47.0))

    def test_all_true_spans_keeps_every_lobe(self):
        flags = [False, True, True, False, True, True, True, False, True]
        self.assertEqual(core.all_true_spans(flags, 2), [(1, 3), (4, 7)])
        self.assertEqual(core.all_true_spans(flags, 1)[-1], (8, 9))

    def test_remainder_strip_does_not_block_neighbour_aisle(self):
        """Single-loaded leftovers must not sit inside a double-loaded aisle."""
        rect = [(0, 0), (300, 0), (300, 400), (0, 400)]
        street = core.street_edge_from_index(rect, 0)
        layout = core.best_layout(
            rect, 0.0, 5.0,
            core.access_points_on_street_edge(street),
            street_edge=street,
        )
        self.assertIsNotNone(layout)
        geom = core.module_geometry(layout["park_angle"], layout["flow"])
        bands = []
        for index, run in enumerate(layout["skeleton_runs"]):
            for row in run["rows"]:
                sv0, sv1 = core._row_v_range(geom, run["center_v"], row["sign"])
                av0, av1 = core._aisle_v_range(geom, run["center_v"], row["sign"])
                bands.append((index, "stall", sv0, sv1, row["u0"], row["u1"]))
                bands.append((index, "aisle", av0, av1, row["u0"], row["u1"]))
        for stall in bands:
            if stall[1] != "stall":
                continue
            for aisle in bands:
                if aisle[1] != "aisle" or aisle[0] == stall[0]:
                    continue
                v_hit = stall[2] < aisle[3] - 0.01 and stall[3] > aisle[2] + 0.01
                u_hit = stall[4] < aisle[5] - 0.01 and stall[5] > aisle[4] + 0.01
                self.assertFalse(
                    v_hit and u_hit,
                    msg="stall run %s blocks aisle of run %s" % (stall[0], aisle[0]),
                )

    def test_u_shape_layout_does_not_invent_courtyard_stalls(self):
        street = core.street_edge_from_index(self.U_SHAPE, 0)
        layout = core.best_layout(
            self.U_SHAPE, 0.0, 5.0,
            core.access_points_on_street_edge(street),
            street_edge=street,
        )
        if layout is None:
            return
        # No stall centroid may sit in the open courtyard (40..260, 60..340)
        # which is outside the U parcel.
        for stall in layout["stalls"]:
            cx = sum(p[0] for p in stall) / 4.0
            cy = sum(p[1] for p in stall) / 4.0
            self.assertTrue(
                core.point_inside(self.U_SHAPE, cx, cy),
                msg="stall escaped the U parcel at (%.1f, %.1f)" % (cx, cy),
            )
        failures = core.validate_layout(self.U_SHAPE, layout)
        self.assertFalse(
            any("flipped" in f or "escapes" in f for f in failures),
            msg=str(failures),
        )


if __name__ == "__main__":
    unittest.main()
