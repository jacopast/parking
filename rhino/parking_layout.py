"""Create quick parking layouts directly inside Rhino.

Run with Rhino's RunPythonScript command. Pick a usable area curve plus
entrance and exit points. The script searches orientations and draws a
driveable double-loaded surface parking layout:

    stall row (18 ft) + drive aisle (24 ft) + stall row (18 ft)

Every parking aisle must connect into an end spine drive, and the entrance
and exit must connect into that circulation network. No text objects are
drawn in the model.
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


def add_marker(point, radius, layer):
    marker_plane = rs.PlaneFromNormal(point, (0, 0, 1))
    circle_id = rs.AddCircle(marker_plane, radius)
    if circle_id:
        rs.ObjectLayer(circle_id, layer)
    return circle_id


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
        # Aisles usually run roughly parallel or perpendicular to access.
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


def merge_intervals(cells):
    if not cells:
        return []

    merged = []
    current = None
    for left, right, bottom, top, z in cells:
        if current is None:
            current = [left, right, bottom, top, z]
            continue
        if abs(left - current[1]) <= 0.001 and abs(bottom - current[2]) <= 0.001 and abs(top - current[3]) <= 0.001:
            current[1] = right
        else:
            merged.append(tuple(current))
            current = [left, right, bottom, top, z]
    if current is not None:
        merged.append(tuple(current))
    return merged


def drive_band_world(local_rect, basis, boundary_id):
    left, right, bottom, top, z = local_rect
    if right - left < 1.0 or top - bottom < 1.0:
        return None
    points_local = rectangle_points(left, bottom, right - left, top - bottom, z)
    points_world = transform_points(points_local, basis)
    if not polygon_inside_boundary(points_world, boundary_id):
        return None
    return points_world


def build_access_connector(access_local, spine_local, z, basis, boundary_id):
    """Connect an access point into the nearest spine with a 24 ft drive band."""
    ax, ay, _ = access_local
    left, right, bottom, top, _ = spine_local

    # Snap to the nearest point on the spine rectangle.
    target_x = min(max(ax, left), right)
    target_y = min(max(ay, bottom), top)

    connectors = []

    # Horizontal leg.
    if abs(ax - target_x) > 0.5:
        x0 = min(ax, target_x)
        x1 = max(ax, target_x)
        y0 = ay - AISLE_WIDTH * 0.5
        band = drive_band_world((x0, x1, y0, y0 + AISLE_WIDTH, z), basis, boundary_id)
        if band:
            connectors.append(band)

    # Vertical leg.
    if abs(ay - target_y) > 0.5:
        y0 = min(ay, target_y)
        y1 = max(ay, target_y)
        x0 = target_x - AISLE_WIDTH * 0.5
        band = drive_band_world((x0, x0 + AISLE_WIDTH, y0, y1, z), basis, boundary_id)
        if band:
            connectors.append(band)

    return connectors


def generate_double_loaded_layout(boundary_id, basis, setback, entry_point, exit_point):
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
    if usable_width < STALL_WIDTH + AISLE_WIDTH or usable_depth < DOUBLE_LOADED_MODULE:
        return None

    entry_local = world_to_local(entry_point, basis)
    exit_local = world_to_local(exit_point, basis)

    # Prefer the end spine closer to access so cars can enter the bay system.
    access_u = 0.5 * (entry_local[0] + exit_local[0])
    mid_u = 0.5 * (min_x + max_x)
    prefer_left = access_u <= mid_u

    spine_options = []
    if prefer_left:
        spine_options.extend(["left", "right", "both"])
    else:
        spine_options.extend(["right", "left", "both"])

    best = None

    for spine_mode in spine_options:
        park_min_x = min_x
        park_max_x = max_x
        spines_local = []

        if spine_mode in ("left", "both"):
            spines_local.append(("left", min_x, min_x + AISLE_WIDTH))
            park_min_x = min_x + AISLE_WIDTH
        if spine_mode in ("right", "both"):
            spines_local.append(("right", max_x - AISLE_WIDTH, max_x))
            park_max_x = max_x - AISLE_WIDTH

        if park_max_x - park_min_x < STALL_WIDTH:
            continue

        stalls = []
        aisle_bands = []
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

                # Only keep complete double-loaded pairs that share aisle access.
                if valid_pair:
                    stripe_stalls.extend(placed_pair)
                    aisle_cells.append((x, x + STALL_WIDTH, aisle_y, aisle_y + AISLE_WIDTH, z))

                x += STALL_WIDTH

            merged = merge_intervals(aisle_cells)
            for cell in merged:
                band = drive_band_world(cell, basis, boundary_id)
                if band:
                    aisle_bands.append(band)
                    aisle_cells_all.append(cell)

            stalls.extend(stripe_stalls)
            stripe += 1
            y = min_y + stripe * DOUBLE_LOADED_MODULE

        if not aisle_cells_all:
            continue

        aisle_min_y = min(cell[2] for cell in aisle_cells_all)
        aisle_max_y = max(cell[3] for cell in aisle_cells_all)
        aisle_min_x = min(cell[0] for cell in aisle_cells_all)
        aisle_max_x = max(cell[1] for cell in aisle_cells_all)

        circulation = []
        spine_rects = []

        for side, spine_left, spine_right in spines_local:
            # Stretch the spine across all aisle elevations and touch the aisle ends.
            if side == "left":
                spine_rect = (spine_left, max(spine_right, aisle_min_x), aisle_min_y, aisle_max_y, z)
            else:
                spine_rect = (min(spine_left, aisle_max_x), spine_right, aisle_min_y, aisle_max_y, z)

            spine_world = drive_band_world(spine_rect, basis, boundary_id)
            if spine_world:
                circulation.append(spine_world)
                spine_rects.append(spine_rect)

        if not spine_rects:
            continue

        # Optional base collector between entry and exit when they sit on the same end.
        base_y = min(entry_local[1], exit_local[1]) - AISLE_WIDTH * 0.5
        if min_y - setback <= base_y <= min_y + AISLE_WIDTH:
            collector = (
                min(entry_local[0], exit_local[0]) - AISLE_WIDTH * 0.5,
                max(entry_local[0], exit_local[0]) + AISLE_WIDTH * 0.5,
                max(base_y, min_y - setback),
                max(base_y, min_y - setback) + AISLE_WIDTH,
                z,
            )
            collector_world = drive_band_world(collector, basis, boundary_id)
            if collector_world:
                circulation.append(collector_world)
                spine_rects.append(collector)

        primary_spine = spine_rects[0]
        entry_links = build_access_connector(entry_local, primary_spine, z, basis, boundary_id)
        exit_links = build_access_connector(exit_local, primary_spine, z, basis, boundary_id)
        circulation.extend(entry_links)
        circulation.extend(exit_links)

        # Reject layouts that cannot connect both access points into circulation.
        if not entry_links and not point_in_boundary(
            local_to_world((entry_local[0], primary_spine[2], z), basis),
            boundary_id,
        ):
            continue
        if not exit_links and not point_in_boundary(
            local_to_world((exit_local[0], primary_spine[2], z), basis),
            boundary_id,
        ):
            continue

        candidate = {
            "stalls": stalls,
            "aisles": aisle_bands,
            "circulation": circulation,
            "stall_count": len(stalls),
            "module_count": stripe,
            "angle": basis["angle"],
            "spine_mode": spine_mode,
            "rect": rect,
        }

        if best is None or candidate["stall_count"] > best["stall_count"]:
            best = candidate

    return best


def choose_best_layout(boundary_id, entry_point, exit_point, setback):
    origin = as_tuple(entry_point)
    best = None

    for angle in candidate_angles(entry_point, exit_point, boundary_id):
        basis = make_basis(origin, angle)
        layout = generate_double_loaded_layout(boundary_id, basis, setback, entry_point, exit_point)
        if not layout:
            continue

        if best is None or layout["stall_count"] > best["stall_count"]:
            best = layout
            best["basis"] = basis

    return best


def draw_layout(boundary_id, entry_point, exit_point, setback):
    layout = choose_best_layout(boundary_id, entry_point, exit_point, setback)
    if not layout:
        rs.MessageBox(
            "No driveable double-loaded layout fits this site. Try a smaller setback or a larger usable area.",
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

    entry = as_tuple(entry_point)
    exit_pt = as_tuple(exit_point)

    marker = add_marker(entry, 3.0, LAYERS["circulation"])
    if marker:
        created.append(marker)
    marker = add_marker(exit_pt, 3.0, LAYERS["circulation"])
    if marker:
        created.append(marker)

    for band in layout["circulation"]:
        object_id = add_polyline(band, LAYERS["circulation"])
        if object_id:
            created.append(object_id)

    for aisle in layout["aisles"]:
        aisle_id = add_polyline(aisle, LAYERS["aisles"])
        if aisle_id:
            created.append(aisle_id)

    for stall in layout["stalls"]:
        stall_id = add_polyline(stall, LAYERS["stalls"])
        if stall_id:
            created.append(stall_id)

    if created:
        group = rs.AddGroup("Parking Layout")
        if group:
            rs.AddObjectsToGroup(created, group)

    rs.Redraw()
    return {
        "stalls": layout["stall_count"],
        "angle": layout["angle"],
        "modules": layout["module_count"],
        "spine_mode": layout["spine_mode"],
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
            "Created %s driveable stalls.\nOrientation: %.0f deg\nEnd spine: %s" % (
                result["stalls"],
                result["angle"],
                result["spine_mode"],
            ),
            64,
            "Parking Layout",
        )


if __name__ == "__main__":
    main()
