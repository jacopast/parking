"""Create quick parking layouts directly inside Rhino.

Run with Rhino's Python editor or the RunPythonScript command. Pick a usable
area curve plus entrance and exit points, then the script searches a small set
of orientations and draws a double-loaded surface parking layout:

    stall row (18 ft) + drive aisle (24 ft) + stall row (18 ft)

No per-stall labels are drawn. Geometry and one summary note are placed on
Rhino layers.
"""

import math

import rhinoscriptsyntax as rs


STALL_WIDTH = 9.0
STALL_DEPTH = 18.0
AISLE_WIDTH = 24.0
DOUBLE_LOADED_MODULE = STALL_DEPTH + AISLE_WIDTH + STALL_DEPTH
DEFAULT_SETBACK = 3.0
ANGLE_STEP_DEG = 15.0

LAYERS = {
    "root": "Parking Layout",
    "stalls": "Parking Layout::Stalls",
    "aisles": "Parking Layout::Aisles",
    "circulation": "Parking Layout::Circulation",
    "labels": "Parking Layout::Labels",
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
    ensure_layer(LAYERS["labels"], (20, 30, 45))
    ensure_layer(LAYERS["boundary"], (239, 71, 111))


def get_number(prompt, default, minimum=None, maximum=None):
    value = rs.GetReal(prompt, default)
    if value is None:
        return None

    if minimum is not None and value < minimum:
        rs.MessageBox("%s must be at least %s." % (prompt, minimum), 48, "Parking Layout")
        return get_number(prompt, default, minimum, maximum)

    if maximum is not None and value > maximum:
        rs.MessageBox("%s must be at most %s." % (prompt, maximum), 48, "Parking Layout")
        return get_number(prompt, default, minimum, maximum)

    return value


def as_tuple(point):
    if hasattr(point, "X"):
        return (point.X, point.Y, point.Z)
    return (point[0], point[1], point[2] if len(point) > 2 else 0.0)


def distance_2d(a, b):
    ax, ay, _ = as_tuple(a)
    bx, by, _ = as_tuple(b)
    return math.sqrt((bx - ax) ** 2 + (by - ay) ** 2)


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


def sample_curve_points(curve_id, count=128):
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


def world_bounding_rect(curve_id):
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
        "z": box[0].Z,
    }


def point_in_boundary(point, boundary_id):
    if not rs.IsCurveClosed(boundary_id):
        return True
    return bool(rs.PointInPlanarClosedCurve(point, boundary_id))


def polygon_inside_boundary(points, boundary_id):
    for point in points:
        if not point_in_boundary(point, boundary_id):
            return False
    return True


def rectangle_points(x, y, width, depth, z):
    return [
        (x, y, z),
        (x + width, y, z),
        (x + width, y + depth, z),
        (x, y + depth, z),
    ]


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


def add_marker(point, label, radius, layer):
    created = []
    marker_plane = rs.PlaneFromNormal(point, (0, 0, 1))
    circle_id = rs.AddCircle(marker_plane, radius)
    if circle_id:
        rs.ObjectLayer(circle_id, layer)
        created.append(circle_id)

    text_point = (point[0], point[1] + radius * 1.8, point[2])
    text_id = add_text(label, text_point, max(radius * 0.8, 2.0), layer)
    if text_id:
        created.append(text_id)

    return created


def normalize_angle(angle_deg):
    value = angle_deg % 180.0
    if value < 0:
        value += 180.0
    return value


def candidate_angles(entry_point, exit_point, boundary_id):
    angles = []
    entry = as_tuple(entry_point)
    exit_pt = as_tuple(exit_point)

    if distance_2d(entry, exit_pt) > 0.001:
        access_angle = math.degrees(math.atan2(exit_pt[1] - entry[1], exit_pt[0] - entry[0]))
        angles.extend([access_angle, access_angle + 90.0])

    points = sample_curve_points(boundary_id, 32)
    for index in range(len(points)):
        a = as_tuple(points[index])
        b = as_tuple(points[(index + 1) % len(points)])
        if distance_2d(a, b) < 1.0:
            continue
        edge_angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        angles.extend([edge_angle, edge_angle + 90.0])

    step = 0.0
    while step < 180.0:
        angles.append(step)
        step += ANGLE_STEP_DEG

    unique = []
    seen = set()
    for angle in angles:
        key = round(normalize_angle(angle), 1)
        if key in seen:
            continue
        seen.add(key)
        unique.append(key)
    return unique


def generate_double_loaded_layout(boundary_id, basis, setback):
    rect = local_bounding_rect(boundary_id, basis)
    if not rect:
        return None

    min_x = rect["min_x"] + setback
    max_x = rect["max_x"] - setback
    min_y = rect["min_y"] + setback
    max_y = rect["max_y"] - setback
    z = rect["z"]

    usable_width = max_x - min_x
    usable_depth = max_y - min_y
    if usable_width < STALL_WIDTH or usable_depth < DOUBLE_LOADED_MODULE:
        return None

    stalls = []
    aisles = []
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

        x = min_x
        while x + STALL_WIDTH <= max_x + 0.001:
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

            # Keep only complete double-loaded pairs with their shared aisle.
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
                aisle_local = rectangle_points(left, bottom, right - left, top - bottom, aisle_z)
                aisles.append(transform_points(aisle_local, basis))

        stalls.extend(stripe_stalls)
        stripe += 1
        y = min_y + stripe * DOUBLE_LOADED_MODULE

    if not stalls:
        return None

    return {
        "stalls": stalls,
        "aisles": aisles,
        "stall_count": len(stalls),
        "module_count": stripe,
        "angle": basis["angle"],
        "rect": rect,
    }


def choose_best_layout(boundary_id, entry_point, exit_point, setback):
    origin = as_tuple(entry_point)
    best = None

    for angle in candidate_angles(entry_point, exit_point, boundary_id):
        basis = make_basis(origin, angle)
        layout = generate_double_loaded_layout(boundary_id, basis, setback)
        if not layout:
            continue

        if best is None or layout["stall_count"] > best["stall_count"]:
            best = layout
            best["basis"] = basis

    return best


def draw_layout(boundary_id, entry_point, exit_point, setback):
    world_rect = world_bounding_rect(boundary_id)
    if not world_rect:
        rs.MessageBox("Could not read the selected available area.", 16, "Parking Layout")
        return None

    layout = choose_best_layout(boundary_id, entry_point, exit_point, setback)
    if not layout:
        rs.MessageBox("No double-loaded parking module fits this site with the current setback.", 48, "Parking Layout")
        return None

    setup_layers()
    created = []

    reference_copy = rs.CopyObject(boundary_id)
    if reference_copy:
        rs.ObjectLayer(reference_copy, LAYERS["boundary"])
        created.append(reference_copy)

    entry = as_tuple(entry_point)
    exit_pt = as_tuple(exit_point)

    created.extend(add_marker(entry, "IN", 4.0, LAYERS["circulation"]))
    created.extend(add_marker(exit_pt, "OUT", 4.0, LAYERS["circulation"]))

    if distance_2d(entry, exit_pt) > 0.001:
        access_line = rs.AddLine(entry, exit_pt)
        if access_line:
            rs.ObjectLayer(access_line, LAYERS["circulation"])
            created.append(access_line)

    for aisle in layout["aisles"]:
        aisle_id = add_polyline(aisle, LAYERS["aisles"])
        if aisle_id:
            created.append(aisle_id)

    for stall in layout["stalls"]:
        stall_id = add_polyline(stall, LAYERS["stalls"])
        if stall_id:
            created.append(stall_id)

    summary = (
        "Parking layout\n"
        "Stalls: %s\n"
        "Orientation: %.0f deg\n"
        "Module: 18 + 24 + 18 ft"
    ) % (layout["stall_count"], layout["angle"])
    summary_point = (
        world_rect["min_x"],
        world_rect["max_y"] + 12.0,
        world_rect["z"],
    )
    summary_id = add_text(summary, summary_point, 4.0, LAYERS["labels"])
    if summary_id:
        created.append(summary_id)

    if created:
        group = rs.AddGroup("Parking Layout")
        if group:
            rs.AddObjectsToGroup(created, group)

    rs.Redraw()
    return {
        "stalls": layout["stall_count"],
        "angle": layout["angle"],
        "modules": layout["module_count"],
    }


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

    entry_point = rs.GetPoint("Pick the entrance center point")
    if not entry_point:
        return

    exit_point = rs.GetPoint("Pick the exit center point")
    if not exit_point:
        return

    setback = get_number("Setback from available area edge in feet", DEFAULT_SETBACK, 0.0)
    if setback is None:
        return

    result = draw_layout(boundary_id, entry_point, exit_point, setback)
    if result:
        rs.MessageBox(
            "Created %s stalls at %.0f degrees using double-loaded 18/24/18 modules." % (
                result["stalls"],
                result["angle"],
            ),
            64,
            "Parking Layout",
        )


if __name__ == "__main__":
    main()
