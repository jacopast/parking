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


def as_tuple(point):
    if hasattr(point, "X"):
        return (point.X, point.Y, point.Z)
    return (point[0], point[1], point[2] if len(point) > 2 else 0.0)


def make_basis(origin_point, angle_deg):
    origin = as_tuple(origin_point)
    radians = math.radians(angle_deg)
    ux = (math.cos(radians), math.sin(radians))
    uy = (-ux[1], ux[0])
    return {
        "origin": origin,
        "u": ux,
        "v": uy,
        "angle": angle_deg,
        "z": origin[2],
    }


def world_to_local(point, basis):
    x, y, _ = as_tuple(point)
    ox, oy, _ = basis["origin"]
    dx = x - ox
    dy = y - oy
    return (
        dx * basis["u"][0] + dy * basis["u"][1],
        dx * basis["v"][0] + dy * basis["v"][1],
        basis["z"],
    )


def local_to_world(point, basis):
    u, v, _ = as_tuple(point)
    ox, oy, oz = basis["origin"]
    return (
        ox + u * basis["u"][0] + v * basis["v"][0],
        oy + u * basis["u"][1] + v * basis["v"][1],
        oz,
    )


def transform_points(points, basis):
    return [local_to_world(point, basis) for point in points]


def sample_curve_points(curve_id, count=96):
    points = []
    divided = rs.DivideCurve(curve_id, count, create_points=False)
    if divided:
        points.extend(divided)
    box = rs.BoundingBox(curve_id)
    if box:
        points.extend(box)
    return points


def local_bounding_rect(curve_id, basis):
    points = sample_curve_points(curve_id)
    if not points:
        return None
    local_points = [world_to_local(point, basis) for point in points]
    us = [point[0] for point in local_points]
    vs = [point[1] for point in local_points]
    return {
        "min_x": min(us),
        "max_x": max(us),
        "min_y": min(vs),
        "max_y": max(vs),
        "width": max(us) - min(us),
        "depth": max(vs) - min(vs),
        "z": basis["z"],
    }


def normalize_angle(angle_deg):
    value = angle_deg % 180.0
    if value < 0:
        value += 180.0
    return value


def candidate_surface_angles(boundary_id, access_points):
    angles = [0.0, 90.0]
    if access_points and len(access_points) >= 2:
        a = as_tuple(access_points[0])
        b = as_tuple(access_points[1])
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        if abs(dx) + abs(dy) > 0.001:
            access_angle = math.degrees(math.atan2(dy, dx))
            angles.extend([access_angle, access_angle + 90.0])

    points = sample_curve_points(boundary_id, 24)
    for index in range(len(points)):
        a = as_tuple(points[index])
        b = as_tuple(points[(index + 1) % len(points)])
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        if abs(dx) + abs(dy) < 1.0:
            continue
        edge_angle = math.degrees(math.atan2(dy, dx))
        angles.extend([edge_angle, edge_angle + 90.0])

    unique = []
    seen = set()
    for angle in angles:
        key = round(normalize_angle(angle), 1)
        if key in seen:
            continue
        seen.add(key)
        unique.append(key)
    return unique


def drive_band_world(local_rect, basis, boundary_id):
    left, right, bottom, top, z = local_rect
    if right - left < 1.0 or top - bottom < 1.0:
        return None
    points_local = rectangle_points(left, bottom, right - left, top - bottom, z)
    points_world = transform_points(points_local, basis)
    if not polygon_inside_boundary(points_world, boundary_id):
        return None
    return points_world


def generate_oriented_surface_layout(boundary_id, basis, setback, access_points=None):
    access_points = access_points or []
    rect = local_bounding_rect(boundary_id, basis)
    if not rect:
        return None

    min_x = rect["min_x"] + setback
    max_x = rect["max_x"] - setback
    min_y = rect["min_y"] + setback
    max_y = rect["max_y"] - setback
    z = rect["z"]
    if max_x - min_x < STALL_WIDTH + AISLE_WIDTH or max_y - min_y < DOUBLE_LOADED_MODULE:
        return None

    if access_points:
        access_u = world_to_local(access_points[0], basis)[0]
    else:
        access_u = min_x
    mid_u = 0.5 * (min_x + max_x)
    prefer_left = access_u <= mid_u

    if prefer_left:
        spine_left, spine_right = min_x, min_x + AISLE_WIDTH
        park_min_x, park_max_x = min_x + AISLE_WIDTH, max_x
        spine_side = "left"
    else:
        spine_left, spine_right = max_x - AISLE_WIDTH, max_x
        park_min_x, park_max_x = min_x, max_x - AISLE_WIDTH
        spine_side = "right"

    if park_max_x - park_min_x < STALL_WIDTH:
        return None

    stalls = []
    aisles = []
    aisle_cells_all = []
    stripe = 0
    y = min_y

    while y + DOUBLE_LOADED_MODULE <= max_y + 0.001:
        aisle_y = y + STALL_DEPTH
        row_specs = [
            (y, STALL_DEPTH),
            (y + STALL_DEPTH + AISLE_WIDTH, STALL_DEPTH),
        ]
        aisle_cells = []
        stripe_stalls = []

        x = park_min_x
        while x + STALL_WIDTH <= park_max_x + 0.001:
            aisle_local = rectangle_points(x, aisle_y, STALL_WIDTH, AISLE_WIDTH, z)
            aisle_world = transform_points(aisle_local, basis)
            if not polygon_inside_boundary(aisle_world, boundary_id):
                x += STALL_WIDTH
                continue

            placed_pair = []
            valid_pair = True
            for row_y, row_depth in row_specs:
                stall_local = rectangle_points(x, row_y, STALL_WIDTH, row_depth, z)
                stall_world = transform_points(stall_local, basis)
                if not polygon_inside_boundary(stall_world, boundary_id):
                    valid_pair = False
                    break
                placed_pair.append(stall_world)

            if valid_pair:
                stripe_stalls.extend(placed_pair)
                aisle_cells.append((x, x + STALL_WIDTH, aisle_y, aisle_y + AISLE_WIDTH, z))

            x += STALL_WIDTH

        if aisle_cells:
            merged = []
            current = None
            for left, right, bottom, top, aisle_z in aisle_cells:
                if current is None:
                    current = [left, right, bottom, top, aisle_z]
                    continue
                if abs(left - current[1]) <= 0.001 and abs(bottom - current[2]) <= 0.001 and abs(top - current[3]) <= 0.001:
                    current[1] = right
                else:
                    merged.append(current)
                    current = [left, right, bottom, top, aisle_z]
            if current is not None:
                merged.append(current)

            for left, right, bottom, top, aisle_z in merged:
                band = drive_band_world((left, right, bottom, top, aisle_z), basis, boundary_id)
                if band:
                    aisles.append(band)
                    aisle_cells_all.append((left, right, bottom, top, aisle_z))

        stalls.extend(stripe_stalls)
        stripe += 1
        y = min_y + stripe * DOUBLE_LOADED_MODULE

    if not aisle_cells_all:
        return None

    aisle_min_y = min(cell[2] for cell in aisle_cells_all)
    aisle_max_y = max(cell[3] for cell in aisle_cells_all)
    aisle_min_x = min(cell[0] for cell in aisle_cells_all)
    aisle_max_x = max(cell[1] for cell in aisle_cells_all)

    if spine_side == "left":
        spine_rect = (spine_left, max(spine_right, aisle_min_x), aisle_min_y, aisle_max_y, z)
    else:
        spine_rect = (min(spine_left, aisle_max_x), spine_right, aisle_min_y, aisle_max_y, z)

    spine_world = drive_band_world(spine_rect, basis, boundary_id)
    circulation = []
    if spine_world:
        circulation.append(spine_world)

    return {
        "stalls": stalls,
        "aisles": aisles + circulation,
        "rect": bounding_rect(boundary_id),
        "angle": basis["angle"],
    }


def generate_surface_layout(boundary_id, setback, access_points=None):
    access_points = access_points or []
    world_rect = bounding_rect(boundary_id)
    if not world_rect:
        return {"stalls": [], "aisles": [], "rect": None, "angle": 0.0}

    if access_points:
        origin = as_tuple(access_points[0])
    else:
        origin = (world_rect["min_x"], world_rect["min_y"], world_rect["z"])

    best = None
    for angle in candidate_surface_angles(boundary_id, access_points):
        basis = make_basis(origin, angle)
        layout = generate_oriented_surface_layout(boundary_id, basis, setback, access_points)
        if not layout:
            continue
        if best is None or len(layout["stalls"]) > len(best["stalls"]):
            best = layout

    if best is None:
        return {"stalls": [], "aisles": [], "rect": world_rect, "angle": 0.0}
    return best


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
    for stall in layout["stalls"]:
        object_id = add_polyline(stall, LAYERS["surface"])
        if object_id:
            created.append(object_id)
    return created


def draw_garage_option(option, index, base_y_offset):
    created = []
    footprint = [(p[0], p[1] + base_y_offset, p[2]) for p in option["footprint"]]
    object_id = add_polyline(footprint, LAYERS["garage"])
    if object_id:
        created.append(object_id)
    return created


def draw_access(access_points):
    created = []
    for point in access_points:
        circle = rs.AddCircle(point, 4.0)
        if circle:
            rs.ObjectLayer(circle, LAYERS["access"])
            created.append(circle)

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

    surface = generate_surface_layout(site_curve_id, setback, access_points)
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
