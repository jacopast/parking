"""Headless harness: run parking_core.best_layout and draw the result to SVG.

Mirrors the land-use model used in Rhino:

    non-drivable (green)  -> tip pockets, setbacks, end-caps, mid-islands
    standing (yellow)     -> parking stalls
    moving (residual)     -> the 24 ft aisle emerges as the gap between greens

The authored ring centreline is diagnostic only; the product aisle is residual.
"""
import os
import sys

CORE_DIR = os.environ.get("PARKING_CORE_DIR", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CORE_DIR)
sys.modules.pop("parking_core", None)
import parking_core as core  # noqa: E402

# Site measured from the vector reference PDF (feet, origin = bottom-left).
# Keep this as the regression parcel: the target has four horizontal,
# double-loaded bay islands when the 0 degree option is selected.
SITE = [
    (0.0, 0.0),
    (173.0, 0.0),
    (290.0, 188.0),
    (0.0, 407.0),
]
STREET_INDEX = 0  # bottom edge
SETBACK = 5.0

COLORS = {
    "boundary": "#111111",
    "non_drivable": "#4c8c54",
    "non_drivable_stroke": "#3a6b40",
    "stalls": "#f5b301",
    "stall_fill": "#fff4cc",
    "moving": "#d9d9d9",
}


def polyline(points, color, width=0.9, close=True, fill="none"):
    pts = list(points)
    if close and pts and (abs(pts[0][0] - pts[-1][0]) > 1e-9 or abs(pts[0][1] - pts[-1][1]) > 1e-9):
        pts = pts + [pts[0]]
    d = " ".join("%s%.3f,%.3f" % ("M" if i == 0 else "L", p[0], p[1])
                 for i, p in enumerate(pts))
    return '<path d="%s" fill="%s" stroke="%s" stroke-width="%s" />' % (d, fill, color, width)


def run(polygon=None, setback=SETBACK, street_index=STREET_INDEX, out="out.svg", title="", force_angle=None):
    polygon = polygon or SITE
    poly3 = list(polygon)
    street_edge = core.street_edge_from_index(poly3, street_index)
    access_points = core.access_points_on_street_edge(street_edge)
    if force_angle is not None:
        real = core.primary_skeleton_orientations
        core.primary_skeleton_orientations = lambda *a, **k: [force_angle]
        try:
            layout = core.best_layout(poly3, 0.0, setback, access_points, street_edge=street_edge)
        finally:
            core.primary_skeleton_orientations = real
    else:
        layout = core.best_layout(poly3, 0.0, setback, access_points, street_edge=street_edge)

    parts = []
    # Site fill = provisional "moving" pavement; greens and stalls paint over it.
    parts.append(polyline(polygon, COLORS["boundary"], 1.2, fill=COLORS["moving"]))
    if layout is None:
        print("NO LAYOUT")
    else:
        land = core.layout_land_use(poly3, layout, setback, street_edge, 0.0)
        for region in land["non_drivable"]:
            parts.append(polyline(
                region,
                COLORS["non_drivable_stroke"],
                0.7,
                fill=COLORS["non_drivable"],
            ))
        for stall in land["standing"]:
            parts.append(polyline(
                stall,
                COLORS["stalls"],
                0.7,
                fill=COLORS["stall_fill"],
            ))
        parts.append(polyline(polygon, COLORS["boundary"], 1.4, fill="none"))

        print("%-22s stalls=%s perimeter=%s runs=%s angle=%.1f ring=%s non_drivable=%s" % (
            title or out,
            layout["stall_count"], layout["perimeter_stalls"],
            layout.get("run_count"), layout["angle"], layout.get("ring_mode"),
            len(land["non_drivable"]),
        ))
        for opt in layout.get("orientation_options", []):
            print("    option %.1f deg -> %s stalls" % (opt["angle"], opt["stall_count"]))

    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    pad = 20.0
    minx, maxx = min(xs) - pad, max(xs) + pad
    miny, maxy = min(ys) - pad, max(ys) + pad
    w, h = maxx - minx, maxy - miny
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" '
        'viewBox="%.3f %.3f %.3f %.3f">'
        '<g transform="translate(0,%.3f) scale(1,-1)">%s</g></svg>'
    ) % (w * 2.0, h * 2.0, minx, miny, w, h, miny + maxy, "".join(parts))
    with open(out, "w") as handle:
        handle.write(svg)
    return layout


if __name__ == "__main__":
    run(out=sys.argv[1] if len(sys.argv) > 1 else "out.svg")
