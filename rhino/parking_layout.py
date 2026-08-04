"""Create a driveable surface parking layout inside Rhino.

Run with Rhino's RunPythonScript command. Pick a closed usable area curve,
then select one existing edge of that curve as the street frontage.
Do not draw a new line.

The layout uses circulation-first packing:

    find orientation -> orthogonal ring drive -> outer stalls on the ring -> core grid

Trial-and-error searches aisle orientation and racetrack seating so the loop
stays orthogonal to the stall grid. Only the outside of the ring gets a
perimeter stall row; the inside is filled with a double-loaded module grid.
Short or unconnected bay runs are trimmed away. No text is drawn.
"""

import math
import os
import sys

import rhinoscriptsyntax as rs

# Always load parking_core.py from this script's folder. Rhino keeps imported
# modules in memory between RunPythonScript calls, so an older core would
# otherwise survive after you unzip a newer toolkit.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR in sys.path:
    sys.path.remove(_SCRIPT_DIR)
sys.path.insert(0, _SCRIPT_DIR)
sys.modules.pop("parking_core", None)

import parking_core as core

if not hasattr(core, "street_edge_from_pick"):
    raise ImportError(
        "parking_core.py is outdated or from the wrong folder.\n"
        "Keep parking_layout.py and parking_core.py together, then re-download:\n"
        "https://github.com/jacopast/parking/archive/refs/heads/cursor/rhino-parking-layout-e880.zip"
    )


DEFAULT_SETBACK = 5.0

LAYERS = {
    "root": "Parking Layout",
    "stalls": "Parking Layout::Stalls",
    "aisles": "Parking Layout::Aisles",
    "circulation": "Parking Layout::Circulation",
    "curbs": "Parking Layout::Curbs",
    "islands": "Parking Layout::Islands",
    "boundary": "Parking Layout::Available Area",
}


def ensure_layer(name, color):
    if not rs.IsLayer(name):
        rs.AddLayer(name, color=color)
    return name


def setup_layers():
    ensure_layer(LAYERS["root"], (40, 40, 40))
    ensure_layer(LAYERS["stalls"], (255, 183, 3))
    ensure_layer(LAYERS["aisles"], (61, 90, 128))
    ensure_layer(LAYERS["circulation"], (17, 138, 178))
    ensure_layer(LAYERS["curbs"], (90, 90, 90))
    ensure_layer(LAYERS["islands"], (76, 140, 84))
    ensure_layer(LAYERS["boundary"], (239, 71, 111))


def get_number(prompt, default, minimum=None):
    value = rs.GetReal(prompt, default)
    if value is None:
        return None
    if minimum is not None and value < minimum:
        rs.MessageBox("%s must be at least %s." % (prompt, minimum), 48, "Parking Layout")
        return get_number(prompt, default, minimum)
    return value


def add_polyline(points, layer, close=True):
    draw_points = list(points)
    if close and draw_points and draw_points[0] != draw_points[-1]:
        draw_points.append(draw_points[0])

    object_id = rs.AddPolyline(draw_points)
    if object_id:
        rs.ObjectLayer(object_id, layer)
    return object_id


def draw_curbs(polygon, z, layout, setback, street_edge):
    created = []
    for curb in core.build_curb_polylines(polygon, z, layout, setback, street_edge):
        if not curb or len(curb) < 2:
            continue
        # Closed only when the first/last points already match (island loops).
        closed = (
            abs(curb[0][0] - curb[-1][0]) < 1e-6
            and abs(curb[0][1] - curb[-1][1]) < 1e-6
        )
        object_id = add_polyline(curb, LAYERS["curbs"], close=closed)
        if object_id:
            created.append(object_id)
    return created


def pick_street_edge(boundary_id, polygon, z):
    """Select one existing side of the site — nothing new to draw."""
    street_edge = core.pick_street_edge(polygon, z, rs, boundary_id)
    if not street_edge:
        rs.MessageBox(
            "Select one existing edge of the site boundary that fronts the street.\n"
            "You do not need to draw a new line.",
            48,
            "Parking Layout",
        )
        return None
    return street_edge


def draw_ring(polygon, z, layout):
    created = []
    outer, inner = core.layout_ring_polylines(layout, polygon, z)

    for band in (outer, inner):
        if not band:
            continue
        valid = [point for point in band if core.point_inside(polygon, point[0], point[1])]
        if len(valid) < 3:
            continue
        object_id = add_polyline(valid, LAYERS["circulation"])
        if object_id:
            created.append(object_id)

    return created


def draw_street_edge(street_edge, z):
    """Highlight the designated street frontage."""
    created = []
    a = street_edge["a"]
    b = street_edge["b"]
    edge_id = rs.AddLine((a[0], a[1], z), (b[0], b[1], z))
    if edge_id:
        rs.ObjectLayer(edge_id, LAYERS["circulation"])
        created.append(edge_id)
    return created


def draw_access(polygon, z, layout, access_points, street_edge=None):
    """Draw entry/exit driveway throats straight inward from the street."""
    created = []
    if not access_points or not street_edge:
        return created

    outer, inner = core.layout_ring_polylines(layout, polygon, z)
    if not outer:
        return created

    clear = layout.get("access_clear", core.DRIVEWAY_CLEAR)
    sax, say = street_edge["a"]
    sbx, sby = street_edge["b"]
    sl = math.hypot(sbx - sax, sby - say) or 1.0
    sx, sy = (sbx - sax) / sl, (sby - say) / sl
    nx, ny = core.street_inward_normal(street_edge, polygon)

    for point in access_points:
        access = core.as_tuple(point)
        target = core.driveway_throat_target(access, street_edge, polygon, outer, inner)
        if not target:
            # Fallback: fixed depth along the street inward normal.
            depth = 0.5 * (layout.get("ring_outer", 0.0) + layout.get("ring_inner", core.RING_WIDTH))
            if depth < 1.0:
                depth = core.RING_WIDTH
            target = (access[0] + nx * depth, access[1] + ny * depth)

        tx, ty = target
        center = rs.AddLine((access[0], access[1], z), (tx, ty, z))
        if center:
            rs.ObjectLayer(center, LAYERS["circulation"])
            created.append(center)

        # Driveway width edges stay parallel to the street (true curb-cut throat).
        half = clear * 0.5
        for sign in (-1.0, 1.0):
            ox, oy = sx * half * sign, sy * half * sign
            edge = rs.AddLine(
                (access[0] + ox, access[1] + oy, z),
                (tx + ox, ty + oy, z),
            )
            if edge:
                rs.ObjectLayer(edge, LAYERS["circulation"])
                created.append(edge)

    return created


def draw_layout(boundary_id, street_edge, setback):
    polygon, z = core.boundary_polygon(boundary_id, rs)
    if not polygon:
        rs.MessageBox("Could not read the selected available area.", 16, "Parking Layout")
        return None

    access_points = core.access_points_on_street_edge(street_edge)
    layout = core.best_layout(polygon, z, setback, access_points, street_edge=street_edge)
    if not layout:
        rs.MessageBox(
            "No parking bay fits inside the perimeter drive. Try a smaller setback or a larger site.",
            48,
            "Parking Layout",
        )
        return None

    setup_layers()
    created = []

    reference_copy = rs.CopyObject(boundary_id)
    if reference_copy:
        rs.ObjectLayer(reference_copy, LAYERS["boundary"])
        created.append(reference_copy)

    created.extend(draw_ring(polygon, z, layout))
    created.extend(draw_street_edge(street_edge, z))
    created.extend(draw_access(polygon, z, layout, access_points, street_edge))
    created.extend(draw_curbs(polygon, z, layout, setback, street_edge))

    for aisle in layout["aisles"]:
        object_id = add_polyline(aisle, LAYERS["aisles"])
        if object_id:
            created.append(object_id)

    for island in layout.get("islands", []):
        object_id = add_polyline(island, LAYERS["islands"])
        if object_id:
            created.append(object_id)

    for stall in layout["stalls"]:
        object_id = add_polyline(stall, LAYERS["stalls"])
        if object_id:
            created.append(object_id)

    if created:
        group = rs.AddGroup("Parking Layout")
        if group:
            rs.AddObjectsToGroup(created, group)

    rs.Redraw()
    return layout


def main():
    boundary_id = rs.GetObject(
        "Select the closed available area curve for the parking layout",
        rs.filter.curve,
        preselect=True,
    )
    if not boundary_id:
        return

    if not rs.IsCurveClosed(boundary_id):
        rs.MessageBox("Use a closed available area curve for the automatic layout.", 48, "Parking Layout")
        return

    polygon, z = core.boundary_polygon(boundary_id, rs)
    if not polygon:
        rs.MessageBox("Could not read the selected available area.", 16, "Parking Layout")
        return

    street_edge = pick_street_edge(boundary_id, polygon, z)
    if not street_edge:
        return

    setback = get_number("Setback from the property line in feet", DEFAULT_SETBACK, 0.0)
    if setback is None:
        return

    layout = draw_layout(boundary_id, street_edge, setback)
    if layout:
        rs.MessageBox(
            "Stalls: %s (%s on the perimeter)\n"
            "Parking: %s degree %s bays\n"
            "Aisle orientation: %.0f degrees\n"
            "Street frontage length: %.0f ft\n"
            "Accessible stalls required: %s including %s van" % (
                layout["stall_count"],
                layout["perimeter_stalls"],
                layout["park_angle"],
                layout["flow"],
                layout["angle"],
                street_edge["length"],
                layout["ada"]["accessible"],
                layout["ada"]["van"],
            ),
            64,
            "Parking Layout",
        )


if __name__ == "__main__":
    main()
