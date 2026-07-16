"""Rhino panel-style parking feasibility generator.

Run with Rhino's RunPythonScript command. The script opens a compact dialog
that lets users pick a usable site curve, optionally pick access points, enter
the required stall count, and generate schematic surface/garage parking
geometry in Rhino layers.

This is intentionally a feasibility-level tool:
- fixed 9 x 18 stalls
- fixed 24 drive aisles
- surface parking first
- garage options only when the surface count is short
- garage sizing by bay count, footprint, levels, and sf/stall assumptions
"""

import math

import rhinoscriptsyntax as rs

try:
    import Rhino
    import Eto.Drawing as drawing
    import Eto.Forms as forms
except Exception:
    Rhino = None
    drawing = None
    forms = None


STALL_WIDTH = 9.0
STALL_DEPTH = 18.0
AISLE_WIDTH = 24.0
DOUBLE_LOADED_MODULE = STALL_DEPTH + AISLE_WIDTH + STALL_DEPTH
DEFAULT_SETBACK = 6.0
DEFAULT_MAX_LEVELS = 5
DEFAULT_REQUIRED_STALLS = 300
GARAGE_RAMP_CORE_LOSS = 0.15

LAYERS = {
    "root": "Parking Feasibility",
    "site": "Parking Feasibility::Site",
    "surface": "Parking Feasibility::Surface",
    "garage": "Parking Feasibility::Garage Options",
    "access": "Parking Feasibility::Access",
    "stats": "Parking Feasibility::Stats",
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
    ensure_layer(LAYERS["stats"], (20, 30, 45))


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
    z = box[0].Z
    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
        "width": max(xs) - min(xs),
        "depth": max(ys) - min(ys),
        "z": z,
    }


def point_in_boundary(point, boundary_id):
    if not boundary_id or not rs.IsCurveClosed(boundary_id):
        return True
    return bool(rs.PointInPlanarClosedCurve(point, boundary_id))


def polygon_inside_boundary(points, boundary_id):
    for point in points:
        if not point_in_boundary(point, boundary_id):
            return False
    return True


def add_polyline(points, layer, close=True):
    draw_points = list(points)
    if close and draw_points and draw_points[0] != draw_points[-1]:
        draw_points.append(draw_points[0])

    object_id = rs.AddPolyline(draw_points)
    if object_id:
        rs.ObjectLayer(object_id, layer)
    return object_id


def add_text(text, point, height, layer):
    text_id = rs.AddText(text, point, height=height)
    if text_id:
        rs.ObjectLayer(text_id, layer)
    return text_id


def rectangle_points(x, y, width, depth, z):
    return [
        (x, y, z),
        (x + width, y, z),
        (x + width, y + depth, z),
        (x, y + depth, z),
    ]


def center_of(points):
    count = float(len(points))
    return (
        sum(point[0] for point in points) / count,
        sum(point[1] for point in points) / count,
        sum(point[2] for point in points) / count,
    )


def generate_surface_layout(boundary_id, setback):
    rect = bounding_rect(boundary_id)
    if not rect:
        return {"stalls": [], "aisles": [], "rect": None}

    min_x = rect["min_x"] + setback
    max_x = rect["max_x"] - setback
    min_y = rect["min_y"] + setback
    max_y = rect["max_y"] - setback
    z = rect["z"]
    stalls = []
    aisles = []
    stripe = 0
    y = min_y

    while y + DOUBLE_LOADED_MODULE <= max_y + 0.001:
        aisle = rectangle_points(min_x, y + STALL_DEPTH, max_x - min_x, AISLE_WIDTH, z)
        if polygon_inside_boundary(aisle, boundary_id):
            aisles.append(aisle)

        row_specs = [
            (y, STALL_DEPTH),
            (y + STALL_DEPTH + AISLE_WIDTH, STALL_DEPTH),
        ]

        for row_y, row_depth in row_specs:
            x = min_x
            while x + STALL_WIDTH <= max_x + 0.001:
                stall = rectangle_points(x, row_y, STALL_WIDTH, row_depth, z)
                if polygon_inside_boundary(stall, boundary_id):
                    stalls.append(stall)
                x += STALL_WIDTH

        stripe += 1
        y = min_y + stripe * DOUBLE_LOADED_MODULE

    return {"stalls": stalls, "aisles": aisles, "rect": rect}


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

    min_length_for_max_levels = math.ceil(float(target_stalls) / max(max_levels, 1) / max(2 * bay_count, 1) / (1.0 - GARAGE_RAMP_CORE_LOSS)) * STALL_WIDTH
    length = min(max(72.0, min_length_for_max_levels), available_length)
    stalls_per_level = garage_capacity(length, bay_count)
    levels = int(math.ceil(float(target_stalls) / max(stalls_per_level, 1)))
    levels = max(levels, 1)
    total_stalls = stalls_per_level * levels
    fits_levels = levels <= max_levels

    x = rect["min_x"] + setback
    y = rect["min_y"] + setback
    z = rect["z"]

    if orientation == "horizontal":
        footprint = rectangle_points(x, y, length, module_depth, z)
        draw_length = length
        draw_depth = module_depth
    else:
        footprint = rectangle_points(x, y, module_depth, length, z)
        draw_length = length
        draw_depth = module_depth

    return {
        "bay_count": bay_count,
        "orientation": orientation,
        "footprint": footprint,
        "length": draw_length,
        "depth": draw_depth,
        "levels": levels,
        "fits_levels": fits_levels,
        "stalls_per_level": stalls_per_level,
        "total_stalls": total_stalls,
        "gross_area": draw_length * draw_depth * levels,
        "sf_per_stall": (draw_length * draw_depth * levels) / max(total_stalls, 1),
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


def draw_surface(layout):
    created = []
    for aisle in layout["aisles"]:
        object_id = add_polyline(aisle, LAYERS["access"])
        if object_id:
            created.append(object_id)
    for index, stall in enumerate(layout["stalls"]):
        object_id = add_polyline(stall, LAYERS["surface"])
        if object_id:
            created.append(object_id)
            if index % 20 == 0:
                center = center_of(stall)
                label = add_text(str(index + 1), center, 2.0, LAYERS["stats"])
                if label:
                    created.append(label)
    return created


def draw_garage_option(option, index, base_y_offset):
    created = []
    label = "Garage Option %s" % chr(ord("A") + index)
    footprint = [(p[0], p[1] + base_y_offset, p[2]) for p in option["footprint"]]
    object_id = add_polyline(footprint, LAYERS["garage"])
    if object_id:
        created.append(object_id)

    center = center_of(footprint)
    text = (
        "%s\n"
        "Bays: %s\n"
        "Levels: %s%s\n"
        "Stalls / level: %s\n"
        "Total stalls: %s\n"
        "Footprint: %.0f x %.0f\n"
        "Efficiency: %.0f sf/stall"
    ) % (
        label,
        option["bay_count"],
        option["levels"],
        "" if option["fits_levels"] else " (over limit)",
        option["stalls_per_level"],
        option["total_stalls"],
        option["length"],
        option["depth"],
        option["sf_per_stall"],
    )
    text_id = add_text(text, (center[0], center[1], center[2]), 6.0, LAYERS["stats"])
    if text_id:
        created.append(text_id)

    return created


def draw_access(access_points):
    created = []
    for index, point in enumerate(access_points):
        circle = rs.AddCircle(point, 6.0)
        if circle:
            rs.ObjectLayer(circle, LAYERS["access"])
            created.append(circle)
        label = add_text("Access %s" % (index + 1), (point.X, point.Y + 8.0, point.Z), 4.0, LAYERS["stats"])
        if label:
            created.append(label)

    if len(access_points) >= 2:
        line = rs.AddLine(access_points[0], access_points[1])
        if line:
            rs.ObjectLayer(line, LAYERS["access"])
            created.append(line)

    return created


def run_feasibility(site_curve_id, access_points, required_stalls, setback, max_levels):
    if not site_curve_id:
        rs.MessageBox("Select a site curve before generating options.", 48, "Parking Feasibility")
        return None

    setup_layers()
    created = []

    site_copy = rs.CopyObject(site_curve_id)
    if site_copy:
        rs.ObjectLayer(site_copy, LAYERS["site"])
        created.append(site_copy)

    created.extend(draw_access(access_points))

    surface = generate_surface_layout(site_curve_id, setback)
    created.extend(draw_surface(surface))

    surface_stalls = len(surface["stalls"])
    deficit = max(required_stalls - surface_stalls, 0)
    garage_options = generate_garage_options(surface["rect"], setback, deficit, max_levels)

    base_offset = 0.0
    if surface["rect"]:
        base_offset = surface["rect"]["depth"] + AISLE_WIDTH * 2.0

    for index, option in enumerate(garage_options):
        created.extend(draw_garage_option(option, index, base_offset * (index + 1)))

    recommendation = "Surface parking is sufficient."
    if deficit > 0 and garage_options:
        best = garage_options[0]
        recommendation = "Garage required: use %s levels for approximately %s stalls." % (
            best["levels"],
            best["total_stalls"],
        )
    elif deficit > 0:
        recommendation = "Garage required, but no schematic option fits the current limits."

    summary_y = surface["rect"]["max_y"] + AISLE_WIDTH if surface["rect"] else 0.0
    summary_x = surface["rect"]["min_x"] if surface["rect"] else 0.0
    summary = (
        "Parking Feasibility Summary\n"
        "Required stalls: %s\n"
        "Surface stalls: %s\n"
        "Deficit: %s\n"
        "Setback: %.1f\n"
        "Max garage levels: %s\n"
        "%s"
    ) % (required_stalls, surface_stalls, deficit, setback, max_levels, recommendation)
    summary_id = add_text(summary, (summary_x, summary_y, 0), 7.0, LAYERS["stats"])
    if summary_id:
        created.append(summary_id)

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

        self.site_label = forms.Label(Text="No site curve selected")
        self.access_label = forms.Label(Text="No access points selected")
        self.required_box = forms.TextBox(Text=str(DEFAULT_REQUIRED_STALLS))
        self.setback_box = forms.TextBox(Text=str(DEFAULT_SETBACK))
        self.max_levels_box = forms.TextBox(Text=str(DEFAULT_MAX_LEVELS))
        self.status_label = forms.Label(Text="Ready")

        pick_site = forms.Button(Text="Pick Site Curve")
        pick_site.Click += self.on_pick_site
        pick_access = forms.Button(Text="Pick Access Points")
        pick_access.Click += self.on_pick_access
        generate = forms.Button(Text="Generate Feasibility Geometry")
        generate.Click += self.on_generate
        close = forms.Button(Text="Close")
        close.Click += self.on_close

        layout = forms.DynamicLayout()
        layout.Spacing = drawing.Size(6, 6)
        layout.AddRow(forms.Label(Text="Site"), self.site_label, pick_site)
        layout.AddRow(forms.Label(Text="Access"), self.access_label, pick_access)
        layout.AddRow(forms.Label(Text="Required stalls"), self.required_box)
        layout.AddRow(forms.Label(Text="Setback"), self.setback_box)
        layout.AddRow(forms.Label(Text="Max garage levels"), self.max_levels_box)
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
