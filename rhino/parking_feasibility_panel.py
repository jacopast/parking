"""Rhino parking feasibility generator.

Run with Rhino's RunPythonScript command. The script opens a compact dialog
that lets users pick a usable site curve, optionally pick access points, enter
the required stall count, and generate schematic surface and garage parking
geometry in Rhino layers.

This is intentionally a feasibility-level tool:
- fixed 9 x 18 stalls and 24 ft aisles
- perimeter ring drive so surface bays are reachable
- surface parking first
- garage options only when the surface count is short
- garage sizing by bay count, footprint, levels, and sf/stall assumptions
"""

import math
import os
import sys

import rhinoscriptsyntax as rs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import parking_core as core

try:
    import Rhino
    import Eto.Drawing as drawing
    import Eto.Forms as forms
except Exception:
    Rhino = None
    drawing = None
    forms = None


STALL_WIDTH = core.STALL_WIDTH
STALL_DEPTH = core.STALL_DEPTH
AISLE_WIDTH = core.AISLE_WIDTH
DOUBLE_LOADED_MODULE = core.DOUBLE_LOADED_MODULE
DEFAULT_SETBACK = 5.0
DEFAULT_MAX_LEVELS = 5
DEFAULT_REQUIRED_STALLS = 300
GARAGE_RAMP_CORE_LOSS = 0.15

LAYERS = {
    "root": "Parking Feasibility",
    "site": "Parking Feasibility::Site",
    "surface": "Parking Feasibility::Surface",
    "garage": "Parking Feasibility::Garage Options",
    "access": "Parking Feasibility::Access",
}


def ensure_layer(name, color):
    if not rs.IsLayer(name):
        rs.AddLayer(name, color=color)
    return name


def setup_layers():
    ensure_layer(LAYERS["root"], (35, 35, 35))
    ensure_layer(LAYERS["site"], (239, 71, 111))
    ensure_layer(LAYERS["surface"], (255, 183, 3))
    ensure_layer(LAYERS["garage"], (61, 90, 128))
    ensure_layer(LAYERS["access"], (17, 138, 178))


def safe_float(value, fallback):
    try:
        return float(value)
    except Exception:
        return fallback


def safe_int(value, fallback):
    try:
        return int(float(value))
    except Exception:
        return fallback


def bounding_rect(curve_id):
    box = rs.BoundingBox(curve_id)
    if not box:
        return None

    xs = [point.X for point in box]
    ys = [point.Y for point in box]
    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
        "width": max(xs) - min(xs),
        "depth": max(ys) - min(ys),
        "z": box[0].Z,
    }


def add_polyline(points, layer, close=True):
    draw_points = list(points)
    if close and draw_points and draw_points[0] != draw_points[-1]:
        draw_points.append(draw_points[0])

    object_id = rs.AddPolyline(draw_points)
    if object_id:
        rs.ObjectLayer(object_id, layer)
    return object_id


def rectangle_points(x, y, width, depth, z):
    return [
        (x, y, z),
        (x + width, y, z),
        (x + width, y + depth, z),
        (x, y + depth, z),
    ]


def garage_capacity(length, bay_count):
    raw = math.floor(length / STALL_WIDTH) * 2 * bay_count
    return int(math.floor(raw * (1.0 - GARAGE_RAMP_CORE_LOSS)))


def garage_footprint_points(rect, setback, bay_count, orientation, target_stalls, max_levels):
    usable_width = max(rect["width"] - setback * 2, 0.0)
    usable_depth = max(rect["depth"] - setback * 2, 0.0)
    module_depth = DOUBLE_LOADED_MODULE * bay_count

    if orientation == "horizontal":
        available_length = usable_width
        available_depth = usable_depth
    else:
        available_length = usable_depth
        available_depth = usable_width

    if module_depth > available_depth or available_length < STALL_WIDTH * 4:
        return None

    min_length_for_max_levels = math.ceil(
        float(target_stalls) / max(max_levels, 1) / max(2 * bay_count, 1) / (1.0 - GARAGE_RAMP_CORE_LOSS)
    ) * STALL_WIDTH
    length = min(max(72.0, min_length_for_max_levels), available_length)
    stalls_per_level = garage_capacity(length, bay_count)
    levels = max(int(math.ceil(float(target_stalls) / max(stalls_per_level, 1))), 1)
    total_stalls = stalls_per_level * levels

    x = rect["min_x"] + setback
    y = rect["min_y"] + setback
    z = rect["z"]

    if orientation == "horizontal":
        footprint = rectangle_points(x, y, length, module_depth, z)
    else:
        footprint = rectangle_points(x, y, module_depth, length, z)

    return {
        "bay_count": bay_count,
        "orientation": orientation,
        "footprint": footprint,
        "length": length,
        "depth": module_depth,
        "levels": levels,
        "fits_levels": levels <= max_levels,
        "stalls_per_level": stalls_per_level,
        "total_stalls": total_stalls,
        "sf_per_stall": (length * module_depth * levels) / max(total_stalls, 1),
    }


def generate_garage_options(rect, setback, deficit, max_levels):
    options = []
    if deficit <= 0 or not rect:
        return options

    for bay_count in (1, 2, 3):
        for orientation in ("horizontal", "vertical"):
            option = garage_footprint_points(rect, setback, bay_count, orientation, deficit, max_levels)
            if option:
                options.append(option)

    options.sort(key=lambda item: (
        0 if item["fits_levels"] else 1,
        item["levels"],
        item["sf_per_stall"],
        item["bay_count"],
    ))
    return options[:3]


def draw_ring(polygon, z, layout):
    created = []
    outer, inner = core.ring_band_points(polygon, z, layout["ring_outer"], layout["ring_inner"])

    for band in (outer, inner):
        if not band:
            continue
        valid = [point for point in band if core.point_inside(polygon, point[0], point[1])]
        if len(valid) < 3:
            continue
        object_id = add_polyline(valid, LAYERS["access"])
        if object_id:
            created.append(object_id)

    return created


def draw_surface(layout):
    created = []
    for aisle in layout["aisles"]:
        object_id = add_polyline(aisle, LAYERS["access"])
        if object_id:
            created.append(object_id)
    for stall in layout["stalls"]:
        object_id = add_polyline(stall, LAYERS["surface"])
        if object_id:
            created.append(object_id)
    return created


def draw_garage_option(option, base_y_offset):
    footprint = [(p[0], p[1] + base_y_offset, p[2]) for p in option["footprint"]]
    object_id = add_polyline(footprint, LAYERS["garage"])
    return [object_id] if object_id else []


def draw_access(access_points, z):
    created = []
    for point in access_points:
        access = core.as_tuple(point)
        marker_plane = rs.PlaneFromNormal((access[0], access[1], z), (0, 0, 1))
        circle = rs.AddCircle(marker_plane, 4.0)
        if circle:
            rs.ObjectLayer(circle, LAYERS["access"])
            created.append(circle)
    return created


def run_feasibility(site_curve_id, access_points, required_stalls, setback, max_levels):
    if not site_curve_id:
        rs.MessageBox("Select a site curve before generating options.", 48, "Parking Feasibility")
        return None

    polygon, z = core.boundary_polygon(site_curve_id, rs)
    if not polygon:
        rs.MessageBox("Could not read the selected site curve.", 16, "Parking Feasibility")
        return None

    setup_layers()
    created = []

    site_copy = rs.CopyObject(site_curve_id)
    if site_copy:
        rs.ObjectLayer(site_copy, LAYERS["site"])
        created.append(site_copy)

    created.extend(draw_access(access_points, z))

    surface = core.best_layout(polygon, z, setback, access_points)
    surface_stalls = 0
    if surface:
        created.extend(draw_ring(polygon, z, surface))
        created.extend(draw_surface(surface))
        surface_stalls = surface["stall_count"]

    rect = bounding_rect(site_curve_id)
    deficit = max(required_stalls - surface_stalls, 0)
    garage_options = generate_garage_options(rect, setback, deficit, max_levels)

    base_offset = rect["depth"] + AISLE_WIDTH * 2.0 if rect else 0.0
    for index, option in enumerate(garage_options):
        created.extend(draw_garage_option(option, base_offset * (index + 1)))

    ada = core.ada_stall_count(max(surface_stalls, required_stalls))
    accessible_note = " ADA: %s accessible including %s van." % (ada["accessible"], ada["van"])

    recommendation = "Surface parking is sufficient with %s stalls." % surface_stalls
    if deficit > 0 and garage_options:
        best = garage_options[0]
        recommendation = "Surface %s stalls. Garage needed: %s levels for about %s stalls." % (
            surface_stalls,
            best["levels"],
            best["total_stalls"],
        )
    elif deficit > 0:
        recommendation = "Surface %s stalls. Garage needed, but no option fits the current limits." % surface_stalls

    recommendation += accessible_note

    if created:
        group = rs.AddGroup("Parking Feasibility")
        if group:
            rs.AddObjectsToGroup(created, group)

    rs.Redraw()
    return {
        "surface_stalls": surface_stalls,
        "deficit": deficit,
        "garage_options": garage_options,
        "recommendation": recommendation,
    }


def make_control(control_type, text=None):
    """Create Eto controls without relying on keyword constructor support."""
    try:
        control = control_type()
    except TypeError:
        control = control_type(text or "")

    if text is not None and hasattr(control, "Text"):
        control.Text = text

    return control


class ParkingFeasibilityDialog(forms.Dialog[bool] if forms else object):
    def __init__(self):
        if not forms:
            return
        forms.Dialog[bool].__init__(self)
        self.Title = "Parking Feasibility"
        self.Padding = drawing.Padding(12)
        self.Resizable = False
        self.site_curve_id = None
        self.access_points = []

        self.site_label = make_control(forms.Label, "No site curve selected")
        self.access_label = make_control(forms.Label, "No access points selected")
        self.required_box = make_control(forms.TextBox, str(DEFAULT_REQUIRED_STALLS))
        self.setback_box = make_control(forms.TextBox, str(DEFAULT_SETBACK))
        self.max_levels_box = make_control(forms.TextBox, str(DEFAULT_MAX_LEVELS))
        self.status_label = make_control(forms.Label, "Ready")

        pick_site = make_control(forms.Button, "Pick Site Curve")
        pick_site.Click += self.on_pick_site
        pick_access = make_control(forms.Button, "Pick Access Points")
        pick_access.Click += self.on_pick_access
        generate = make_control(forms.Button, "Generate Feasibility Geometry")
        generate.Click += self.on_generate
        close = make_control(forms.Button, "Close")
        close.Click += self.on_close

        layout = forms.DynamicLayout()
        layout.Spacing = drawing.Size(6, 6)
        layout.AddRow(make_control(forms.Label, "Site"), self.site_label, pick_site)
        layout.AddRow(make_control(forms.Label, "Access"), self.access_label, pick_access)
        layout.AddRow(make_control(forms.Label, "Required stalls"), self.required_box)
        layout.AddRow(make_control(forms.Label, "Setback"), self.setback_box)
        layout.AddRow(make_control(forms.Label, "Max garage levels"), self.max_levels_box)
        layout.AddRow(None)
        layout.AddRow(generate)
        layout.AddRow(self.status_label)
        layout.AddRow(close)
        self.Content = layout

    def on_pick_site(self, sender, event):
        curve_id = rs.GetObject("Select a closed usable site curve", rs.filter.curve, preselect=True)
        if curve_id:
            self.site_curve_id = curve_id
            self.site_label.Text = str(curve_id)
            self.status_label.Text = "Site curve selected"

    def on_pick_access(self, sender, event):
        points = rs.GetPoints(True, False, "Pick optional vehicle access points. Press Enter when done.")
        if points:
            self.access_points = points
            self.access_label.Text = "%s point(s)" % len(points)
            self.status_label.Text = "Access points selected"

    def on_generate(self, sender, event):
        required = safe_int(self.required_box.Text, DEFAULT_REQUIRED_STALLS)
        setback = safe_float(self.setback_box.Text, DEFAULT_SETBACK)
        max_levels = safe_int(self.max_levels_box.Text, DEFAULT_MAX_LEVELS)
        result = run_feasibility(self.site_curve_id, self.access_points, required, setback, max_levels)
        if result:
            self.status_label.Text = result["recommendation"]

    def on_close(self, sender, event):
        self.Close(True)


def run_prompt_fallback():
    curve_id = rs.GetObject("Select a closed usable site curve", rs.filter.curve, preselect=True)
    if not curve_id:
        return

    access_points = rs.GetPoints(True, False, "Pick optional vehicle access points. Press Enter when done.") or []
    required = rs.GetInteger("Required parking stalls", DEFAULT_REQUIRED_STALLS, 1)
    if required is None:
        return

    setback = rs.GetReal("Setback", DEFAULT_SETBACK, 0.0)
    if setback is None:
        return

    max_levels = rs.GetInteger("Maximum garage levels", DEFAULT_MAX_LEVELS, 1)
    if max_levels is None:
        return

    result = run_feasibility(curve_id, access_points, required, setback, max_levels)
    if result:
        rs.MessageBox(result["recommendation"], 64, "Parking Feasibility")


def main():
    if forms and Rhino:
        dialog = ParkingFeasibilityDialog()
        dialog.ShowModal(Rhino.UI.RhinoEtoApp.MainWindow)
    else:
        run_prompt_fallback()


if __name__ == "__main__":
    main()
