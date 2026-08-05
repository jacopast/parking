"""Create a driveable surface parking layout inside Rhino.

Run with Rhino's RunPythonScript command. Pick one OR MORE closed usable-area
curves. Human inputs are collected first (sites, setback, orientation policy,
street frontage per site); the solver runs only after those picks are done.
Each site is designed independently.
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

_ACTIVE_CREATED = []


def escape_requested(reset=False):
    """Non-throwing Rhino ESC check, compatible with Rhino 7/8 signatures."""
    try:
        return bool(rs.EscapeTest(False, reset))
    except TypeError:
        try:
            return bool(rs.EscapeTest(False))
        except TypeError:
            try:
                return bool(rs.EscapeTest())
            except Exception:
                return True
    except Exception:
        # EscapeTest may throw Rhino's cancellation exception on older builds.
        return True


def clear_escape():
    try:
        rs.EscapeTest(False, True)
    except Exception:
        pass


def cancellation_checkpoint(created=None):
    """Cancel immediately and remove geometry created by the active draw."""
    if not escape_requested():
        return
    targets = list(_ACTIVE_CREATED or created or [])
    if targets:
        try:
            rs.DeleteObjects(targets)
        except Exception:
            pass
    _ACTIVE_CREATED[:] = []
    raise core.LayoutCancelled("Parking layout cancelled.")


def track_created(object_ids):
    ids = [object_id for object_id in (object_ids or []) if object_id]
    _ACTIVE_CREATED.extend(ids)
    return ids


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
        if escape_requested():
            raise core.LayoutCancelled("Parking layout cancelled.")
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
        raise core.LayoutCancelled("Parking layout cancelled.")
    for label, option in zip(labels, options):
        if label == picked:
            return option
    return options[0]


def compute_layout(boundary_id, street_edge, setback, progress=None,
                   cancel=escape_requested):
    """Run the solver only — no Rhino prompts, no drawing."""
    polygon, z = core.boundary_polygon(boundary_id, rs)
    if not polygon:
        return None, "Could not read the selected available area."

    access_points = core.access_points_on_street_edge(street_edge)
    layout = core.best_layout(
        polygon, z, setback, access_points,
        street_edge=street_edge, progress=progress, cancel=cancel,
    )
    if not layout:
        return None, "No parking bay fits inside the perimeter drive."
    return layout, None


def draw_layout_geometry(boundary_id, street_edge, setback, layout, site_label=None):
    """Draw one already-computed layout into layers / a named group."""
    polygon, z = core.boundary_polygon(boundary_id, rs)
    if not polygon:
        return None, "Could not read the selected available area."

    access_points = core.access_points_on_street_edge(street_edge)
    setup_layers()
    created = []
    cancellation_checkpoint(created)

    reference_copy = rs.CopyObject(boundary_id)
    if reference_copy:
        rs.ObjectLayer(reference_copy, LAYERS["boundary"])
        created.append(reference_copy)
        track_created([reference_copy])

    new_objects = draw_ring(polygon, z, layout)
    created.extend(track_created(new_objects))
    cancellation_checkpoint(created)
    new_objects = draw_street_edge(street_edge, z)
    created.extend(track_created(new_objects))
    new_objects = draw_access(polygon, z, layout, access_points, street_edge)
    created.extend(track_created(new_objects))
    new_objects = draw_curbs(polygon, z, layout, setback, street_edge)
    created.extend(track_created(new_objects))
    cancellation_checkpoint(created)

    land = core.layout_land_use(polygon, layout, setback, street_edge, z)
    for index, region in enumerate(land["non_drivable"]):
        if index % 16 == 0:
            cancellation_checkpoint(created)
        object_id = add_polyline(region, LAYERS["non_drivable"])
        if object_id:
            created.append(object_id)
            track_created([object_id])

    for index, stall in enumerate(land["standing"]):
        if index % 32 == 0:
            cancellation_checkpoint(created)
        object_id = add_polyline(stall, LAYERS["stalls"])
        if object_id:
            created.append(object_id)
            track_created([object_id])

    group_name = "Parking Layout"
    if site_label:
        group_name = "Parking Layout — %s" % site_label
    if created:
        cancellation_checkpoint(created)
        group = rs.AddGroup(group_name)
        if group:
            rs.AddObjectsToGroup(created, group)

    return layout, None


def draw_layout(boundary_id, street_edge, setback, site_label=None, auto_best=False):
    """Legacy one-shot helper: compute, pick option, draw."""
    layout, error = compute_layout(boundary_id, street_edge, setback)
    if error or not layout:
        return None, error or "No layout."
    layout = choose_option(layout, site_label=site_label, auto_best=auto_best)
    drawn, error = draw_layout_geometry(
        boundary_id, street_edge, setback, layout, site_label=site_label,
    )
    if not error:
        rs.Redraw()
    return drawn, error


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


def collect_street_edges(sites):
    """Pick every site's street frontage before any solver work starts."""
    jobs = []
    for site in sites:
        try:
            rs.UnselectAllObjects()
            rs.SelectObject(site["id"])
        except Exception:
            pass

        if len(sites) > 1:
            rs.Prompt(
                "Inputs first — pick street frontage for %s, then the rest."
                % site["label"]
            )
        else:
            rs.Prompt("Pick the street frontage edge, then layout will compute.")

        street_edge = pick_street_edge(
            site["id"], site["polygon"], site["z"], site_label=site["label"],
        )
        if not street_edge:
            jobs.append({
                "site": site,
                "street_edge": None,
                "ok": False,
                "message": "Street frontage not selected — skipped.",
            })
            continue
        jobs.append({
            "site": site,
            "street_edge": street_edge,
            "ok": True,
        })
    try:
        rs.UnselectAllObjects()
    except Exception:
        pass
    return jobs


def run_layout():
    _ACTIVE_CREATED[:] = []
    # ── Phase 1: gather every human input that does not need a solve ──
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
            "polygon": polygon,  # already simplified for curve / dense polylines
            "z": z,
            "label": "Site %s" % (index + 1),
            "vertex_count": len(polygon),
        })

    if not sites:
        rs.MessageBox("No valid closed site curves were selected.", 48, "Parking Layout")
        return

    setback = get_number(
        "Setback from the property line in feet (applied to every site)",
        DEFAULT_SETBACK,
        0.0,
    )
    if setback is None:
        return

    # Ask orientation policy up front (single- or multi-site).
    answer = rs.MessageBox(
        "%s site(s) selected.\n\n"
        "Yes = automatically draw the highest-stall option for each site\n"
        "No  = after computing, choose the aisle orientation per site\n\n"
        "Street edges are picked next; calculation starts only after that."
        % len(sites),
        4 | 32,  # Yes/No + Question
        "Parking Layout",
    )
    if answer not in (6, 7):
        return
    auto_best = (answer == 6)

    jobs = collect_street_edges(sites)
    ready = [job for job in jobs if job["ok"]]
    if not ready:
        rs.MessageBox("No street frontages were selected.", 48, "Parking Layout")
        return

    # ── Phase 2: compute every site (no drawing yet) ──
    total = len(ready)
    for index, job in enumerate(ready, start=1):
        site = job["site"]
        prefix = "Computing %s (%d of %d)" % (site["label"], index, total)

        def report(message, prefix=prefix):
            # Rhino only repaints the prompt line, so keep it short and live.
            rs.Prompt("%s — %s…" % (prefix, message))

        report("reading boundary")
        layout, error = compute_layout(
            site["id"], job["street_edge"], setback, progress=report,
        )
        if error or not layout:
            job["ok"] = False
            job["message"] = error or "No layout."
            job["layout"] = None
            rs.Prompt("%s — no layout" % prefix)
        else:
            job["layout"] = layout
            rs.Prompt("%s — %d stalls" % (prefix, layout["stall_count"]))

    # ── Phase 3: orientation picks (only input that needs solve scores) ──
    if not auto_best:
        for job in ready:
            if not job.get("ok") or not job.get("layout"):
                continue
            job["layout"] = choose_option(
                job["layout"],
                site_label=job["site"]["label"],
                auto_best=False,
            )
    else:
        for job in ready:
            if job.get("ok") and job.get("layout"):
                job["layout"] = choose_option(
                    job["layout"],
                    site_label=job["site"]["label"],
                    auto_best=True,
                )

    # ── Phase 4: draw everything ──
    rs.Prompt("Drawing %d parking layout(s)…" % len(ready))
    setup_layers()
    results = []
    for job in jobs:
        if not job["ok"]:
            results.append({
                "label": job["site"]["label"],
                "ok": False,
                "message": job.get("message") or "Skipped.",
            })
            continue
        if not job.get("layout"):
            results.append({
                "label": job["site"]["label"],
                "ok": False,
                "message": job.get("message") or "No layout.",
            })
            continue

        rs.Prompt("Drawing %s…" % job["site"]["label"])
        layout, error = draw_layout_geometry(
            job["site"]["id"],
            job["street_edge"],
            setback,
            job["layout"],
            site_label=job["site"]["label"],
        )
        if error or not layout:
            results.append({
                "label": job["site"]["label"],
                "ok": False,
                "message": error or "Draw failed.",
            })
            continue
        results.append({
            "label": job["site"]["label"],
            "ok": True,
            "layout": layout,
            "street_edge": job["street_edge"],
        })

    rs.Redraw()

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
            "%s: %s stalls (%s perimeter), %s interior field(s), "
            "%.0f deg aisles, street %.0f ft, ADA %s/%s" % (
                result["label"],
                layout["stall_count"],
                layout["perimeter_stalls"],
                layout.get("interior_field_count", 0),
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
    # Keep completed geometry, but stop treating it as rollback state.
    _ACTIVE_CREATED[:] = []


def main():
    clear_escape()
    try:
        run_layout()
    except core.LayoutCancelled:
        try:
            rs.UnselectAllObjects()
            rs.Redraw()
        except Exception:
            pass
        clear_escape()
        rs.Prompt("Parking layout cancelled.")


if __name__ == "__main__":
    main()
