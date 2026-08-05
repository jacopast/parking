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
    def test_reference_horizontal_keeps_full_capacity(self):
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

        # Capacity is the primary objective; ordered rectangular fields are
        # preferred only when they do not strand a large amount of parking.
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
        inner = core.as_xy_polygon(horizontal["ring_inner_poly"])
        for field in horizontal.get("interior_fields") or []:
            for x, y in core.as_xy_polygon(field):
                self.assertTrue(
                    core.point_inside(inner, x, y)
                    or core.distance_to_polygon(inner, x, y) < 0.1
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

    def test_wide_l_composes_two_ordered_rectangular_fields(self):
        parcel = [
            (0, 0), (500, 0), (500, 180),
            (220, 180), (220, 450), (0, 450),
        ]
        basis = core.make_basis(core.polygon_centroid(parcel), 0.0)
        geometry = core.module_geometry(90, "two-way")
        interior = core.layout_composed_rectangular_fields(
            parcel, basis, 0.0, geometry,
            drive_polygon=parcel,
            site_polygon=parcel,
        )
        self.assertIsNotNone(interior)
        self.assertEqual(len(interior["interior_fields"]), 2)
        self.assertGreater(interior["stall_count"], 0)
        fields = [
            core.as_xy_polygon(field)
            for field in interior["interior_fields"]
        ]
        self.assertFalse(core.convex_overlap(fields[0], fields[1]))

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

    def test_layout_can_be_cancelled_at_deep_checkpoints(self):
        parcel = [(0, 0), (300, 0), (300, 400), (0, 400)]
        street = core.street_edge_from_index(parcel, 0)
        checks = [0]

        def cancel():
            checks[0] += 1
            return checks[0] >= 8

        with self.assertRaises(core.LayoutCancelled):
            core.best_layout(
                parcel, 0.0, 5.0,
                core.access_points_on_street_edge(street),
                street_edge=street,
                cancel=cancel,
            )
        self.assertGreaterEqual(checks[0], 8)
        self.assertIsNone(core._CANCEL_CHECKER)

    def test_offset_rejects_flipped_u_shape_core(self):
        # Deep offset used to flip the U and invent a courtyard core.
        self.assertIsNone(core.offset_polygon(self.U_SHAPE, 47.0))
        shallow = core.offset_polygon(self.U_SHAPE, 5.0)
        self.assertIsNotNone(shallow)
        self.assertTrue(core.offset_result_is_valid(self.U_SHAPE, shallow, 5.0))
        self.assertGreater(core.signed_area(self.U_SHAPE) * core.signed_area(shallow), 0)

    def test_offset_rejects_impossible_acute_wedge(self):
        self.assertIsNone(core.offset_polygon(self.ACUTE_WEDGE, 47.0))

    def test_simplify_caps_dense_curve_boundaries(self):
        import math
        n = 400
        circle = [
            (150 + 140 * math.cos(2 * math.pi * i / n),
             150 + 140 * math.sin(2 * math.pi * i / n))
            for i in range(n)
        ]
        simplified = core.simplify_closed_polygon(circle)
        self.assertLessEqual(len(simplified), core.MAX_BOUNDARY_VERTICES)
        self.assertGreaterEqual(len(simplified), 3)
        ratio = abs(core.signed_area(simplified) / core.signed_area(circle))
        self.assertGreater(ratio, 0.95)
        self.assertLess(ratio, 1.05)

    def test_curved_boundary_still_gets_perimeter_parking(self):
        """Short chords must merge into runs, or curved sites lose all rows."""
        blob = [
            (
                150 + 150 * (1 + 0.18 * math.sin(3 * a)) * math.cos(a),
                150 + 120 * (1 + 0.15 * math.cos(2 * a)) * math.sin(a),
            )
            for a in [2 * math.pi * i / 48 for i in range(48)]
        ]
        edges = [
            math.hypot(
                blob[(i + 1) % len(blob)][0] - blob[i][0],
                blob[(i + 1) % len(blob)][1] - blob[i][1],
            )
            for i in range(len(blob))
        ]
        min_row = core.STALL_WIDTH * (
            core.TERMINAL_ISLAND_COLUMNS * 2 + core.MIN_RUN_COLUMNS
        )
        # No single chord is long enough; runs are what make this work.
        self.assertTrue(all(length < min_row for length in edges))

        runs = core.boundary_straight_runs(blob, min_row, skip_index=0)
        self.assertGreaterEqual(len(runs), 3)

        street = core.street_edge_from_index(blob, 0)
        stalls, _placed, _islands = core.perimeter_row(
            blob, 0.0, 5.0, None, core.STALL_WIDTH, street_edge=street,
        )
        self.assertGreater(len(stalls), 20)

        layout = core.best_layout(
            blob, 0.0, 5.0,
            core.access_points_on_street_edge(street),
            street_edge=street,
        )
        self.assertIsNotNone(layout)
        self.assertGreater(layout["perimeter_stalls"], 20)
        # Interior must not be abandoned just because one clean ring failed.
        self.assertGreater(
            layout["stall_count"] - layout["perimeter_stalls"], 0,
        )

    def test_raster_fields_find_off_center_rectangles(self):
        parcel = [
            (0, 0), (500, 0), (500, 180),
            (220, 180), (220, 450), (0, 450),
        ]
        basis = core.make_basis(core.polygon_centroid(parcel), 0.0)
        rects = core._raster_field_candidates(parcel, basis)
        self.assertGreaterEqual(len(rects), 2)
        for u0, u1, v0, v1 in rects:
            self.assertGreaterEqual(u1 - u0, core.MIN_INTERIOR_FIELD_U)
            self.assertGreaterEqual(v1 - v0, core.MIN_INTERIOR_FIELD_V)
            self.assertTrue(
                core.rectangular_field_is_strictly_inside(
                    parcel, basis, (u0, u1, v0, v1),
                )
            )

    def test_end_caps_reach_core_edge_and_mid_islands_stay_square(self):
        street = core.street_edge_from_index(REFERENCE_SITE, 0)
        layout = core.best_layout(
            REFERENCE_SITE, 0.0, 5.0,
            core.access_points_on_street_edge(street),
            street_edge=street,
        )
        horizontal = min(
            core.option_layouts(layout),
            key=lambda option: abs(core.angle_key(option["angle"])),
        )
        kinds = horizontal["island_kinds"]
        islands = horizontal["islands"]
        self.assertEqual(len(kinds), len(islands))
        self.assertIn("terminal", kinds)
        self.assertIn("interior", kinds)

        inner = core.as_xy_polygon(horizontal["ring_inner_poly"])
        for kind, island in zip(kinds, islands):
            if kind != "terminal":
                continue
            for x, y in core.as_xy_polygon(island):
                self.assertTrue(
                    core.point_inside(inner, x, y)
                    or core.distance_to_polygon(inner, x, y) < 1.5,
                    msg="end cap escaped the parking core",
                )

        # Mid-row islands are drawn square; end caps keep their R5 return.
        curves = core.rounded_layout_islands(horizontal, 0.0)
        self.assertEqual(len(curves), len(islands))
        for kind, island, curve in zip(kinds, islands, curves):
            if kind != "interior":
                continue
            self.assertLessEqual(len(core.as_xy_polygon(curve)), 5)
            self.assertEqual(
                len(core.as_xy_polygon(island)),
                4,
                msg="mid-row island must stay a plain rectangle",
            )

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
