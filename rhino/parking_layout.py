"""Create quick parking layouts directly inside Rhino.

Run with Rhino's Python editor or the RunPythonScript command. Pick a usable
area curve plus entrance and exit points, set a few parking parameters, and the
script draws parking stalls, circulation guides, labels, and a summary in the
current Rhino file.

The script intentionally uses rhinoscriptsyntax so it works in Rhino 7
IronPython and Rhino 8 Python.
"""

import math

import rhinoscriptsyntax as rs


DEFAULTS = {
    "stall_width": 2.5,
    "stall_depth": 5.0,
    "aisle_width": 6.0,
    "drive_width": 6.0,
    "angle": 90.0,
    "margin": 1.5,
    "max_rows": 12,
}

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


def get_integer(prompt, default, minimum=None, maximum=None):
    value = rs.GetInteger(prompt, default)
    if value is None:
        return None

    if minimum is not None and value < minimum:
        rs.MessageBox("%s must be at least %s." % (prompt, minimum), 48, "Parking Layout")
        return get_integer(prompt, default, minimum, maximum)

    if maximum is not None and value > maximum:
        rs.MessageBox("%s must be at most %s." % (prompt, maximum), 48, "Parking Layout")
        return get_integer(prompt, default, minimum, maximum)

    return value


def collect_settings():
    stall_width = get_number("Parking stall width", DEFAULTS["stall_width"], 1.8)
    if stall_width is None:
        return None

    stall_depth = get_number("Parking stall depth", DEFAULTS["stall_depth"], 3.5)
    if stall_depth is None:
        return None

    aisle_width = get_number("Drive aisle width", DEFAULTS["aisle_width"], 3.0)
    if aisle_width is None:
        return None

    drive_width = get_number("Entrance-to-exit clear drive width", DEFAULTS["drive_width"], 3.0)
    if drive_width is None:
        return None

    angle = get_number("Parking angle in degrees", DEFAULTS["angle"], 30.0, 90.0)
    if angle is None:
        return None

    margin = get_number("Setback from boundary bounding box", DEFAULTS["margin"], 0.0)
    if margin is None:
        return None

    max_rows = get_integer("Maximum rows to draw", DEFAULTS["max_rows"], 1, 100)
    if max_rows is None:
        return None

    return {
        "stall_width": stall_width,
        "stall_depth": stall_depth,
        "aisle_width": aisle_width,
        "drive_width": drive_width,
        "angle": angle,
        "margin": margin,
        "max_rows": max_rows,
    }


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
        "z": z,
    }


def as_tuple(point):
    if hasattr(point, "X"):
        return (point.X, point.Y, point.Z)
    return (point[0], point[1], point[2] if len(point) > 2 else 0.0)


def distance_2d(a, b):
    ax, ay, _ = as_tuple(a)
    bx, by, _ = as_tuple(b)
    return math.sqrt((bx - ax) ** 2 + (by - ay) ** 2)


def make_basis(entry_point, exit_point):
    entry = as_tuple(entry_point)
    exit = as_tuple(exit_point)
    length = distance_2d(entry, exit)
    if length <= 0.001:
        return None

    ux = ((exit[0] - entry[0]) / length, (exit[1] - entry[1]) / length)
    uy = (-ux[1], ux[0])

    return {
        "origin": entry,
        "u": ux,
        "v": uy,
        "length": length,
        "z": entry[2],
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


def sample_curve_points(curve_id):
    points = []
    divided = rs.DivideCurve(curve_id, 96, create_points=False)
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
        "z": basis["z"],
    }


def cross_2d(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def point_on_segment(point, a, b):
    if abs(cross_2d(a, b, point)) > 0.000001:
        return False

    return (
        min(a[0], b[0]) - 0.000001 <= point[0] <= max(a[0], b[0]) + 0.000001
        and min(a[1], b[1]) - 0.000001 <= point[1] <= max(a[1], b[1]) + 0.000001
    )


def segments_intersect(a, b, c, d):
    ab_c = cross_2d(a, b, c)
    ab_d = cross_2d(a, b, d)
    cd_a = cross_2d(c, d, a)
    cd_b = cross_2d(c, d, b)

    if ab_c * ab_d < 0 and cd_a * cd_b < 0:
        return True

    return (
        point_on_segment(c, a, b)
        or point_on_segment(d, a, b)
        or point_on_segment(a, c, d)
        or point_on_segment(b, c, d)
    )


def point_in_polygon(point, polygon):
    x = point[0]
    y = point[1]
    inside = False
    count = len(polygon)

    for index in range(count):
        a = polygon[index]
        b = polygon[(index + 1) % count]

        if point_on_segment(point, a, b):
            return True

        crosses = (a[1] > y) != (b[1] > y)
        if crosses:
            intersection_x = (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]
            if x < intersection_x:
                inside = not inside

    return inside


def polygon_overlaps(poly_a, poly_b):
    if not poly_a or not poly_b:
        return False

    for index_a in range(len(poly_a)):
        a1 = poly_a[index_a]
        a2 = poly_a[(index_a + 1) % len(poly_a)]

        for index_b in range(len(poly_b)):
            b1 = poly_b[index_b]
            b2 = poly_b[(index_b + 1) % len(poly_b)]
            if segments_intersect(a1, a2, b1, b2):
                return True

    if point_in_polygon(poly_a[0], poly_b):
        return True

    if point_in_polygon(poly_b[0], poly_a):
        return True

    return False


def build_drive_corridor(basis, width):
    half_width = width / 2.0
    extension = width * 0.5
    start = -extension
    end = basis["length"] + extension

    return [
        (start, -half_width, basis["z"]),
        (end, -half_width, basis["z"]),
        (end, half_width, basis["z"]),
        (start, half_width, basis["z"]),
    ]


def transform_points(points, basis):
    return [local_to_world(point, basis) for point in points]


def point_in_boundary(point, boundary_id):
    if not rs.IsCurveClosed(boundary_id):
        return True
    return bool(rs.PointInPlanarClosedCurve(point, boundary_id))


def polygon_inside_boundary(points, boundary_id):
    for point in points:
        if not point_in_boundary(point, boundary_id):
            return False
    return True


def add_polyline(points, layer, close=True):
    draw_points = list(points)
    if close and draw_points[0] != draw_points[-1]:
        draw_points.append(draw_points[0])
    object_id = rs.AddPolyline(draw_points)
    if object_id:
        rs.ObjectLayer(object_id, layer)
    return object_id


def add_centered_text(text, point, height, layer):
    text_id = rs.AddText(text, point, height=height, justification=2)
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

    text_point = (point[0], point[1] + radius * 1.6, point[2])
    text_id = add_centered_text(label, text_point, radius * 0.9, layer)
    if text_id:
        created.append(text_id)

    return created


def draw_layout(boundary_id, entry_point, exit_point, settings):
    basis = make_basis(entry_point, exit_point)
    if not basis:
        rs.MessageBox("Entrance and exit points are too close together.", 16, "Parking Layout")
        return None

    rect = local_bounding_rect(boundary_id, basis)
    world_rect = bounding_rect(boundary_id)
    if not rect or not world_rect:
        rs.MessageBox("Could not read the selected available area.", 16, "Parking Layout")
        return None

    setup_layers()

    reference_copy = rs.CopyObject(boundary_id)
    if reference_copy:
        rs.ObjectLayer(reference_copy, LAYERS["boundary"])

    entry = as_tuple(entry_point)
    exit = as_tuple(exit_point)

    margin = settings["margin"]
    min_x = rect["min_x"] + margin
    max_x = rect["max_x"] - margin
    min_y = rect["min_y"] + margin
    max_y = rect["max_y"] - margin
    z = rect["z"]

    usable_width = max_x - min_x
    usable_depth = max_y - min_y
    if usable_width <= 0 or usable_depth <= 0:
        rs.MessageBox("The margin is larger than the selected available area.", 16, "Parking Layout")
        return None

    radians = math.radians(settings["angle"])
    stall_width = settings["stall_width"]
    stall_depth = settings["stall_depth"]
    aisle_width = settings["aisle_width"]
    drive_width = settings["drive_width"]
    horizontal_shift = math.cos(radians) * stall_depth
    bay_depth = max(math.sin(radians) * stall_depth, stall_depth * 0.5)
    row_pitch = bay_depth + aisle_width
    drive_corridor = build_drive_corridor(basis, drive_width)
    drive_corridor_world = transform_points(drive_corridor, basis)

    row_count = min(settings["max_rows"], int((usable_depth + aisle_width) / row_pitch))
    if row_count < 1:
        rs.MessageBox("No rows fit inside the selected available area.", 48, "Parking Layout")
        return None

    stall_count = 0
    created = []

    drive_id = add_polyline(drive_corridor_world, LAYERS["circulation"])
    if drive_id:
        created.append(drive_id)

    centerline_id = rs.AddLine(entry, exit)
    if centerline_id:
        rs.ObjectLayer(centerline_id, LAYERS["circulation"])
        created.append(centerline_id)

    created.extend(add_marker(entry, "IN", max(drive_width * 0.18, 0.8), LAYERS["circulation"]))
    created.extend(add_marker(exit, "OUT", max(drive_width * 0.18, 0.8), LAYERS["circulation"]))

    for row in range(row_count):
        direction = 1 if row % 2 == 0 else -1
        shift = horizontal_shift * direction
        row_min_x = min_x + (abs(shift) if shift < 0 else 0)
        y = min_y + row * row_pitch
        columns = int((usable_width - abs(shift)) / stall_width)

        if columns < 1 or y + bay_depth > max_y:
            continue

        aisle_y = y + bay_depth
        aisle_points_local = [
            (min_x, aisle_y, z),
            (max_x, aisle_y, z),
            (max_x, min(aisle_y + aisle_width, max_y), z),
            (min_x, min(aisle_y + aisle_width, max_y), z),
        ]
        if aisle_y < max_y:
            aisle_points = transform_points(aisle_points_local, basis)
            if polygon_inside_boundary(aisle_points, boundary_id):
                aisle_id = add_polyline(aisle_points, LAYERS["aisles"])
                if aisle_id:
                    created.append(aisle_id)

        for column in range(columns):
            x = row_min_x + column * stall_width
            stall_points_local = [
                (x, y, z),
                (x + stall_width, y, z),
                (x + stall_width + shift, y + bay_depth, z),
                (x + shift, y + bay_depth, z),
            ]
            stall_points = transform_points(stall_points_local, basis)

            if not polygon_inside_boundary(stall_points, boundary_id):
                continue

            if polygon_overlaps(stall_points_local, drive_corridor):
                continue

            stall_id = add_polyline(stall_points, LAYERS["stalls"])
            if stall_id:
                created.append(stall_id)
                stall_count += 1

                center_x = sum(point[0] for point in stall_points) / 4.0
                center_y = sum(point[1] for point in stall_points) / 4.0
                label = "%02d-%02d" % (row + 1, column + 1)
                label_id = add_centered_text(label, (center_x, center_y, z), stall_width * 0.35, LAYERS["labels"])
                if label_id:
                    created.append(label_id)

    summary = [
        "Parking layout generated",
        "Stalls: %s" % stall_count,
        "Rows: %s" % row_count,
        "Stall: %.2fm x %.2fm" % (stall_width, stall_depth),
        "Aisle: %.2fm" % aisle_width,
        "Entrance-exit drive: %.2fm" % drive_width,
        "Angle: %.0f deg" % settings["angle"],
    ]
    summary_point = (
        world_rect["min_x"],
        world_rect["max_y"] + max(stall_depth, aisle_width, drive_width) * 0.8,
        world_rect["z"],
    )
    summary_id = add_centered_text("\n".join(summary), summary_point, max(stall_width * 0.45, 1.0), LAYERS["labels"])
    if summary_id:
        created.append(summary_id)

    if created:
        group_name = "Parking Layout %s" % rs.DocumentName()
        group = rs.AddGroup(group_name)
        if group:
            rs.AddObjectsToGroup(created, group)

    rs.Redraw()
    return {
        "stalls": stall_count,
        "rows": row_count,
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
        rs.MessageBox("A closed available area curve gives the best automatic layout result.", 48, "Parking Layout")

    entry_point = rs.GetPoint("Pick the entrance center point")
    if not entry_point:
        return

    exit_point = rs.GetPoint("Pick the exit center point")
    if not exit_point:
        return

    if rs.IsCurveClosed(boundary_id):
        if not point_in_boundary(entry_point, boundary_id) or not point_in_boundary(exit_point, boundary_id):
            rs.MessageBox(
                "Entrance or exit is outside the available area. The script will still keep that access route clear.",
                48,
                "Parking Layout",
            )

    settings = collect_settings()
    if not settings:
        return

    result = draw_layout(boundary_id, entry_point, exit_point, settings)
    if result:
        rs.MessageBox(
            "Created %s parking stalls across %s row(s)." % (result["stalls"], result["rows"]),
            64,
            "Parking Layout",
        )


if __name__ == "__main__":
    main()
