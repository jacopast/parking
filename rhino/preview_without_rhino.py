"""Headless harness: run parking_core.best_layout and draw the result to SVG.

Mirrors what parking_layout.draw_layout() puts in the Rhino document so the
output can be compared with the manual drawings without Rhino.
"""
import importlib
import math
import os
import sys

CORE_DIR = os.environ.get("PARKING_CORE_DIR", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CORE_DIR)
sys.modules.pop("parking_core", None)
import parking_core as core  # noqa: E402

# Site traced from the manual drawing (feet, origin = bottom-left corner).
SITE = [
    (0.0, 0.0),
    (182.0, 0.0),
    (303.0, 197.0),
    (0.0, 425.0),
]
STREET_INDEX = 0  # bottom edge
SETBACK = 5.0

COLORS = {
    "boundary": "#111111",
    "stalls": "#f5b301",
    "aisles": "#3d5a80",
    "circulation": "#118ab2",
    "curbs": "#8a8a8a",
    "islands": "#4c8c54",
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

    parts = [polyline(polygon, COLORS["boundary"], 1.4)]
    if layout is None:
        print("NO LAYOUT")
    else:
        outer, inner = core.layout_ring_polylines(layout, poly3, 0.0)
        for band in (outer, inner):
            if band:
                parts.append(polyline(band, COLORS["circulation"], 1.0))
        for curb in core.build_curb_polylines(poly3, 0.0, layout, setback, street_edge):
            if curb and len(curb) > 1:
                closed = (abs(curb[0][0] - curb[-1][0]) < 1e-6
                          and abs(curb[0][1] - curb[-1][1]) < 1e-6)
                parts.append(polyline(curb, COLORS["curbs"], 0.8, close=closed))
        for env in core.rounded_bay_envelopes(layout, 0.0):
            parts.append(polyline(env, COLORS["circulation"], 0.9))
        for pocket in core.tip_pocket_islands(poly3, layout, 0.0):
            parts.append(polyline(pocket, COLORS["circulation"], 0.9))
        for island in core.rounded_layout_islands(layout, 0.0):
            parts.append(polyline(island, COLORS["islands"], 0.9))
        for stall in layout["stalls"]:
            parts.append(polyline(stall, COLORS["stalls"], 0.8))

        print("%-22s stalls=%s perimeter=%s runs=%s angle=%.1f ring=%s" % (
            title or out,
            layout["stall_count"], layout["perimeter_stalls"],
            layout.get("run_count"), layout["angle"], layout.get("ring_mode"),
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
