"""Create quick parking layouts directly inside Rhino.

Run with Rhino's Python editor or the RunPythonScript command. Pick a site
boundary curve, set a few parking parameters, and the script draws parking
stalls, aisle guides, labels, and a summary in the current Rhino file.

The script intentionally uses rhinoscriptsyntax so it works in Rhino 7
IronPython and Rhino 8 Python.
"""

import math

import rhinoscriptsyntax as rs


DEFAULTS = {
    "stall_width": 2.5,
    "stall_depth": 5.0,
    "aisle_width": 6.0,
    "angle": 90.0,
    "margin": 1.5,
    "max_rows": 12,
}

LAYERS = {
    "root": "Parking Layout",
    "stalls": "Parking Layout::Stalls",
    "aisles": "Parking Layout::Aisles",
    "labels": "Parking Layout::Labels",
    "boundary": "Parking Layout::Reference Boundary",
}


def ensure_layer(name, color):
    if not rs.IsLayer(name):
        rs.AddLayer(name, color=color)
    return name


def setup_layers():
    ensure_layer(LAYERS["root"], (40, 40, 40))
    ensure_layer(LAYERS["stalls"], (255, 183, 3))
    ensure_layer(LAYERS["aisles"], (61, 90, 128))
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


def draw_layout(boundary_id, settings):
    rect = bounding_rect(boundary_id)
    if not rect:
        rs.MessageBox("Could not read the selected boundary.", 16, "Parking Layout")
        return None

    setup_layers()

    reference_copy = rs.CopyObject(boundary_id)
    if reference_copy:
        rs.ObjectLayer(reference_copy, LAYERS["boundary"])

    margin = settings["margin"]
    min_x = rect["min_x"] + margin
    max_x = rect["max_x"] - margin
    min_y = rect["min_y"] + margin
    max_y = rect["max_y"] - margin
    z = rect["z"]

    usable_width = max_x - min_x
    usable_depth = max_y - min_y
    if usable_width <= 0 or usable_depth <= 0:
        rs.MessageBox("The margin is larger than the selected boundary.", 16, "Parking Layout")
        return None

    radians = math.radians(settings["angle"])
    stall_width = settings["stall_width"]
    stall_depth = settings["stall_depth"]
    aisle_width = settings["aisle_width"]
    horizontal_shift = math.cos(radians) * stall_depth
    bay_depth = max(math.sin(radians) * stall_depth, stall_depth * 0.5)
    row_pitch = bay_depth + aisle_width

    row_count = min(settings["max_rows"], int((usable_depth + aisle_width) / row_pitch))
    if row_count < 1:
        rs.MessageBox("No rows fit inside the selected boundary.", 48, "Parking Layout")
        return None

    stall_count = 0
    created = []

    for row in range(row_count):
        direction = 1 if row % 2 == 0 else -1
        shift = horizontal_shift * direction
        row_min_x = min_x + (abs(shift) if shift < 0 else 0)
        y = min_y + row * row_pitch
        columns = int((usable_width - abs(shift)) / stall_width)

        if columns < 1 or y + bay_depth > max_y:
            continue

        aisle_y = y + bay_depth
        aisle_points = [
            (min_x, aisle_y, z),
            (max_x, aisle_y, z),
            (max_x, min(aisle_y + aisle_width, max_y), z),
            (min_x, min(aisle_y + aisle_width, max_y), z),
        ]
        if aisle_y < max_y:
            aisle_id = add_polyline(aisle_points, LAYERS["aisles"])
            if aisle_id:
                created.append(aisle_id)

        for column in range(columns):
            x = row_min_x + column * stall_width
            stall_points = [
                (x, y, z),
                (x + stall_width, y, z),
                (x + stall_width + shift, y + bay_depth, z),
                (x + shift, y + bay_depth, z),
            ]

            if not polygon_inside_boundary(stall_points, boundary_id):
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
        "Angle: %.0f deg" % settings["angle"],
    ]
    summary_point = (rect["min_x"], rect["max_y"] + max(stall_depth, aisle_width) * 0.8, z)
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
        "Select a closed site boundary curve for the parking layout",
        rs.filter.curve,
        preselect=True,
    )
    if not boundary_id:
        return

    settings = collect_settings()
    if not settings:
        return

    result = draw_layout(boundary_id, settings)
    if result:
        rs.MessageBox(
            "Created %s parking stalls across %s row(s)." % (result["stalls"], result["rows"]),
            64,
            "Parking Layout",
        )


if __name__ == "__main__":
    main()
