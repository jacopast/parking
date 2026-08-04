"""Create a driveable surface parking layout inside Rhino.

Run with Rhino's RunPythonScript command. Pick one OR MORE closed usable-area
curves. Each site is designed independently: for every boundary you pick its
own street frontage edge, then a layout is generated for that parcel alone.

The layout uses circulation-first packing and the land-use model:

    non-drivable greens (end-caps / tip / setback) create residual 24 ft aisles
    standing = stalls
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
        "https://github.com/jacopast/parking/archive/refs/heads/cursor/complete-bay-island-layout-e880.zip"
    )


DEFAULT_SETBACK = 5.0

LAYERS = {
    "root": "Parking Layout",
    "stalls": "Parking Layout::Stalls",
    "non_drivable": "Parking Layout::NonDrivable",
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
    ensure_layer(LAYERS["non_drivable"], (76, 140, 84))
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


def pick_street_edge(boundary_id, polygon, z, site_label=None):
    """Select one existing side of this site — nothing new to draw."""
    title = "Parking Layout"
    if site_label:
        title = "Parking Layout — %s" % site_label
        rs.Prompt("Street frontage for %s: pick an existing boundary edge." % site_label)
    street_edge = core.pick_street_edge(polygon, z, rs, boundary_id)
    if not street_edge:
        rs.MessageBox(
            "Select one existing edge of this site boundary that fronts the street.\n"
            "You do not need to draw a new line.",
            48,
            title,
        )
        return None
    return street_edge


def draw_ring(polygon, z, layout):
    """Ring faces are emitted through build_curb_polylines (already filleted)."""
    return []


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
            depth = 0.5 * (layout.get("ring_outer", 0.0) + layout.get("ring_inner", core.RING_WIDTH))
            if depth < 1.0:
                depth = core.RING_WIDTH
            target = (access[0] + nx * depth, access[1] + ny * depth)

        tx, ty = target
        center = rs.AddLine((access[0], access[1], z), (tx, ty, z))
        if center:
            rs.ObjectLayer(center, LAYERS["circulation"])
            created.append(center)

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


def choose_option(layout, site_label=None, auto_best=False):
    """Pick among the three developed aisle options for this site alone."""
    options = core.option_layouts(layout)
    if not options:
        return layout
    if len(options) < 2 or auto_best:
        return options[0]

    labels = []
    for index, option in enumerate(options):
        labels.append("%s. %.0f deg aisles - %s stalls" % (
            index + 1, option["angle"], option["stall_count"],
        ))

    title = "Parking Layout"
    prompt = "Three aisle orientations were developed. Which one should be drawn?"
    if site_label:
        title = "Parking Layout — %s" % site_label
        prompt = "%s\n%s" % (site_label, prompt)

    picked = rs.ListBox(labels, prompt, title, labels[0])
    if not picked:
        return options[0]
    for label, option in zip(labels, options):
        if label == picked:
            return option
    return options[0]


def draw_layout(boundary_id, street_edge, setback, site_label=None, auto_best=False):
    """Generate geometry for ONE site. Call once per selected boundary."""
    polygon, z = core.boundary_polygon(boundary_id, rs)
    if not polygon:
        return None, "Could not read the selected available area."

    access_points = core.access_points_on_street_edge(street_edge)
    layout = core.best_layout(polygon, z, setback, access_points, street_edge=street_edge)
    if layout:
        layout = choose_option(layout, site_label=site_label, auto_best=auto_best)
    if not layout:
        return None, "No parking bay fits inside the perimeter drive."

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

    land = core.layout_land_use(polygon, layout, setback, street_edge, z)
    for region in land["non_drivable"]:
        object_id = add_polyline(region, LAYERS["non_drivable"])
        if object_id:
            created.append(object_id)

    for stall in land["standing"]:
        object_id = add_polyline(stall, LAYERS["stalls"])
        if object_id:
            created.append(object_id)

    group_name = "Parking Layout"
    if site_label:
        group_name = "Parking Layout — %s" % site_label
    if created:
        group = rs.AddGroup(group_name)
        if group:
            rs.AddObjectsToGroup(created, group)

    rs.Redraw()
    return layout, None


def collect_site_curves():
    """One or more closed usable-area curves. Each becomes its own design."""
    preselected = rs.SelectedObjects() or []
    candidates = [
        object_id for object_id in preselected
        if rs.IsCurve(object_id) and rs.IsCurveClosed(object_id)
    ]
    if candidates:
        return candidates

    selected = rs.GetObjects(
        "Select one or more closed available-area curves (each site is designed separately)",
        rs.filter.curve,
        preselect=True,
        select=True,
    )
    if not selected:
        return []
    return list(selected)


def main():
    boundary_ids = collect_site_curves()
    if not boundary_ids:
        return

    sites = []
    for index, boundary_id in enumerate(boundary_ids):
        if not rs.IsCurveClosed(boundary_id):
            rs.MessageBox(
                "Site %s is not a closed curve and will be skipped." % (index + 1),
                48,
                "Parking Layout",
            )
            continue
        polygon, z = core.boundary_polygon(boundary_id, rs)
        if not polygon:
            rs.MessageBox(
                "Site %s could not be read and will be skipped." % (index + 1),
                48,
                "Parking Layout",
            )
            continue
        sites.append({
            "id": boundary_id,
            "polygon": polygon,
            "z": z,
            "label": "Site %s" % (index + 1),
        })

    if not sites:
        rs.MessageBox("No valid closed site curves were selected.", 48, "Parking Layout")
        return

    setback = get_number("Setback from the property line in feet (applied to every site)", DEFAULT_SETBACK, 0.0)
    if setback is None:
        return

    auto_best = False
    if len(sites) > 1:
        answer = rs.MessageBox(
            "%s sites selected.\n\n"
            "Yes = automatically draw the highest-stall option for each site\n"
            "No  = choose the aisle orientation separately for each site" % len(sites),
            4 | 32,  # Yes/No + Question
            "Parking Layout",
        )
        # 6 = Yes, 7 = No
        auto_best = (answer == 6)

    results = []
    for site in sites:
        # Isolate selection so street-edge picking targets this boundary.
        try:
            rs.UnselectAllObjects()
            rs.SelectObject(site["id"])
        except Exception:
            pass

        street_edge = pick_street_edge(
            site["id"], site["polygon"], site["z"], site_label=site["label"],
        )
        if not street_edge:
            results.append({
                "label": site["label"],
                "ok": False,
                "message": "Street frontage not selected — skipped.",
            })
            continue

        layout, error = draw_layout(
            site["id"],
            street_edge,
            setback,
            site_label=site["label"],
            auto_best=auto_best,
        )
        if error or not layout:
            results.append({
                "label": site["label"],
                "ok": False,
                "message": error or "No layout.",
            })
            continue

        results.append({
            "label": site["label"],
            "ok": True,
            "layout": layout,
            "street_edge": street_edge,
        })

    try:
        rs.UnselectAllObjects()
    except Exception:
        pass

    if not results:
        return

    lines = []
    total_stalls = 0
    for result in results:
        if not result["ok"]:
            lines.append("%s: %s" % (result["label"], result["message"]))
            continue
        layout = result["layout"]
        street_edge = result["street_edge"]
        total_stalls += layout["stall_count"]
        lines.append(
            "%s: %s stalls (%s perimeter), %.0f deg aisles, street %.0f ft, ADA %s/%s" % (
                result["label"],
                layout["stall_count"],
                layout["perimeter_stalls"],
                layout["angle"],
                street_edge["length"],
                layout["ada"]["accessible"],
                layout["ada"]["van"],
            )
        )

    ok_count = sum(1 for result in results if result["ok"])
    header = "Designed %s of %s site(s). Combined stalls: %s\n\n" % (
        ok_count, len(sites), total_stalls,
    )
    rs.MessageBox(header + "\n".join(lines), 64, "Parking Layout")


if __name__ == "__main__":
    main()
