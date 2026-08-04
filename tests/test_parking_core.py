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
        self.assertEqual(horizontal["stall_count"], 164)
        self.assertEqual(horizontal["perimeter_stalls"], 78)
        self.assertEqual(
            horizontal["stall_count"] - horizontal["perimeter_stalls"],
            86,
        )
        self.assertTrue(
            all(len(run["rows"]) == 2 for run in horizontal["skeleton_runs"])
        )


if __name__ == "__main__":
    unittest.main()
