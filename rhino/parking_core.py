"""Shared surface parking layout logic for the Rhino parking tools.

Dimensions follow published parking geometrics rather than ad-hoc numbers.
The module table below reproduces Iowa SUDAS Design Manual 8B-1 Table
8B-1.02, which is adapted from ULI / NPA "The Dimensions of Parking":

    https://www.iowasudas.org/wp-content/uploads/sites/15/2020/03/8B-1.pdf

The relationships used here are the standard closed forms:

    WP (stall width along the aisle) = stall width / sin(angle)
    SP (stall projection, row depth) = stall stripe length * sin(angle)
    M1 (double-loaded module)        = 2 * SP + aisle
    M2 (single-loaded module)        = SP + aisle
    interlock reduction              = stall width * cos(angle) / 2

The layout pipeline is the same offset-and-stripe approach used by the
open-source parking generators (Feasibility, BarnacleParking, ParkSolver):

1. hold a setback from the property line
2. place perimeter stall rows that back onto the setback line
3. run a continuous perimeter ring drive that serves those rows
4. stripe the remaining interior with parking modules
5. discard bay runs that are too short or cannot reach the ring

Because interior parking is limited to the region inside the ring, every
aisle reaches the ring, and the ring reaches the entrance and exit.
"""

import math


STALL_WIDTH = 9.0
STALL_STRIPE = 18.0
STALL_DEPTH = STALL_STRIPE
AISLE_WIDTH = 24.0
DOUBLE_LOADED_MODULE = 2 * STALL_STRIPE + AISLE_WIDTH
RING_WIDTH = 24.0
MIN_RUN_COLUMNS = 3
# Edge-aligned directions dominate in practice, so the sweep is only a
# fallback for sites with no long straight edge.
ANGLE_STEP_DEG = 45.0

# Aisle width in feet by park angle and traffic flow, from Iowa SUDAS
# Table 8B-1.02. Angles outside this table are not generated because
# 76 to 89 degrees lets drivers back out and leave the wrong way.
AISLE_WIDTHS = {
    (90, "two-way"): 24.0,
    (60, "two-way"): 25.833,
    (60, "one-way"): 20.333,
    (45, "two-way"): 29.667,
    (45, "one-way"): 21.5,
}

PARK_CONFIGS = [
    (90, "two-way"),
    (60, "one-way"),
    (60, "two-way"),
    (45, "one-way"),
    (45, "two-way"),
]

# Chrest, "Parking Structures", flags layouts above this as inefficient.
EFFICIENCY_TARGET_SF_PER_STALL = 330.0


def module_geometry(park_angle, flow, stall_width=STALL_WIDTH):
    """Return the standard module dimensions for one park angle."""
    radians = math.radians(park_angle)
    sin_a = math.sin(radians)
    cos_a = math.cos(radians)
    row_depth = STALL_STRIPE * sin_a
    aisle = AISLE_WIDTHS[(park_angle, flow)]

    return {
        "park_angle": park_angle,
        "flow": flow,
        "stall_width": stall_width,
        "row_depth": row_depth,
        "aisle": aisle,
        "stall_pitch": stall_width / sin_a,
        "interlock": stall_width * cos_a / 2.0,
        "double_module": 2.0 * row_depth + aisle,
        "single_module": row_depth + aisle,
        "lean": (-cos_a, sin_a),
    }


def ada_stall_count(total_stalls):
    """Accessible stall counts from the 2010 ADA Standards Table 208.2."""
    if total_stalls <= 0:
        return {"accessible": 0, "van": 0}

    thresholds = [
        (25, 1), (50, 2), (75, 3), (100, 4), (150, 5),
        (200, 6), (300, 7), (400, 8), (500, 9),
    ]

    accessible = None
    for limit, count in thresholds:
        if total_stalls <= limit:
            accessible = count
            break

    if accessible is None:
        if total_stalls <= 1000:
            accessible = int(math.ceil(total_stalls * 0.02))
        else:
            accessible = 20 + int(math.ceil((total_stalls - 1000) / 100.0))

    return {"accessible": accessible, "van": int(math.ceil(accessible / 6.0))}


def as_tuple(point):
    if hasattr(point, "X"):
        return (point.X, point.Y, point.Z)
    return (point[0], point[1], point[2] if len(point) > 2 else 0.0)


def boundary_polygon(curve_id, rs):
    """Return the boundary as a flat list of 2D points plus its elevation."""
    points = None
    if rs.IsPolyline(curve_id):
        points = rs.PolylineVertices(curve_id)
    if not points:
        points = rs.DivideCurve(curve_id, 160, False)
    if not points:
        return None, 0.0

    flat = [as_tuple(point) for point in points]
    z = flat[0][2]
    polygon = [(point[0], point[1]) for point in flat]

    if len(polygon) > 1 and abs(polygon[0][0] - polygon[-1][0]) < 1e-9 and abs(polygon[0][1] - polygon[-1][1]) < 1e-9:
        polygon = polygon[:-1]

    if len(polygon) < 3:
        return None, z
    return polygon, z


def point_inside(polygon, x, y):
    inside = False
    count = len(polygon)
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        if (ay > y) != (by > y):
            crossing_x = (bx - ax) * (y - ay) / (by - ay) + ax
            if x < crossing_x:
                inside = not inside
    return inside


def distance_to_segment(px, py, ax, ay, bx, by):
    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(px - ax, py - ay)

    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_to_polygon(polygon, x, y):
    best = None
    count = len(polygon)
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        distance = distance_to_segment(x, y, ax, ay, bx, by)
        if best is None or distance < best:
            best = distance
    return best if best is not None else 0.0


def has_clearance(polygon, x, y, clearance):
    if not point_inside(polygon, x, y):
        return False
    return distance_to_polygon(polygon, x, y) >= clearance


def make_basis(origin, angle_deg):
    radians = math.radians(angle_deg)
    return {
        "origin": (origin[0], origin[1]),
        "u": (math.cos(radians), math.sin(radians)),
        "v": (-math.sin(radians), math.cos(radians)),
        "angle": angle_deg,
    }


def to_world(basis, u, v):
    ox, oy = basis["origin"]
    ux, uy = basis["u"]
    vx, vy = basis["v"]
    return (ox + u * ux + v * vx, oy + u * uy + v * vy)


def to_local(basis, x, y):
    ox, oy = basis["origin"]
    ux, uy = basis["u"]
    vx, vy = basis["v"]
    dx = x - ox
    dy = y - oy
    return (dx * ux + dy * uy, dx * vx + dy * vy)


def local_bounds(polygon, basis):
    locals_ = [to_local(basis, x, y) for x, y in polygon]
    us = [point[0] for point in locals_]
    vs = [point[1] for point in locals_]
    return min(us), max(us), min(vs), max(vs)


def normalize_angle(angle_deg):
    value = angle_deg % 180.0
    if value < 0:
        value += 180.0
    return value


def candidate_angles(polygon, access_points=None):
    angles = []

    access_points = access_points or []
    if len(access_points) >= 2:
        a = as_tuple(access_points[0])
        b = as_tuple(access_points[1])
        if math.hypot(b[0] - a[0], b[1] - a[1]) > 1.0:
            access_angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            angles.extend([access_angle, access_angle + 90.0])

    count = len(polygon)
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        if math.hypot(bx - ax, by - ay) < 5.0:
            continue
        edge_angle = math.degrees(math.atan2(by - ay, bx - ax))
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


def local_polygon_fits(polygon, basis, points, clearance, edge_midpoints=True):
    """Test a local-space convex shape against the buildable region."""
    count = len(points)
    samples = list(points)
    if edge_midpoints:
        for index in range(count):
            au, av = points[index]
            bu, bv = points[(index + 1) % count]
            samples.append((0.5 * (au + bu), 0.5 * (av + bv)))
    samples.append((
        sum(point[0] for point in points) / count,
        sum(point[1] for point in points) / count,
    ))

    for u, v in samples:
        x, y = to_world(basis, u, v)
        if not has_clearance(polygon, x, y, clearance):
            return False
    return True


def stall_shape(u, front_v, geometry, lean_sign):
    """Stall outline as a parallelogram leaning at the park angle."""
    pitch = geometry["stall_pitch"]
    lean_u, lean_v = geometry["lean"]
    back_u = lean_u * STALL_STRIPE
    back_v = lean_v * STALL_STRIPE * lean_sign

    return [
        (u, front_v),
        (u + pitch, front_v),
        (u + pitch + back_u, front_v + back_v),
        (u + back_u, front_v + back_v),
    ]


def rectangle_world(basis, u0, v0, width, depth, z):
    corners = [
        (u0, v0),
        (u0 + width, v0),
        (u0 + width, v0 + depth),
        (u0, v0 + depth),
    ]
    return [(to_world(basis, u, v)[0], to_world(basis, u, v)[1], z) for u, v in corners]


def touches_ring(polygon, basis, u, v0, depth, clearance):
    """A usable aisle must end at the inner ring edge."""
    for step in (0.0, 0.5, 1.0):
        x, y = to_world(basis, u, v0 + depth * step)
        if not point_inside(polygon, x, y):
            return True
        if distance_to_polygon(polygon, x, y) <= clearance + STALL_WIDTH * 2.0:
            return True
    return False


def bay_columns(polygon, basis, v, geometry, rows, clearance, min_u, max_u):
    """Test each stall pitch across one bay and return the shapes that fit."""
    pitch = geometry["stall_pitch"]
    row_depth = geometry["row_depth"]
    aisle = geometry["aisle"]

    columns = []
    u = min_u
    while u + pitch <= max_u + 0.001:
        shapes = [stall_shape(u, v + row_depth, geometry, -1.0)]
        if rows == 2:
            shapes.append(stall_shape(u, v + row_depth + aisle, geometry, 1.0))

        aisle_cell = [
            (u, v + row_depth),
            (u + pitch, v + row_depth),
            (u + pitch, v + row_depth + aisle),
            (u, v + row_depth + aisle),
        ]

        fits = local_polygon_fits(polygon, basis, aisle_cell, clearance)
        if fits:
            for shape in shapes:
                if not local_polygon_fits(polygon, basis, shape, clearance, False):
                    fits = False
                    break

        columns.append((u, fits, shapes))
        u += pitch

    return columns


def layout_for_angle(polygon, basis, clearance, z, geometry):
    """Stripe the region inside the perimeter ring with parking modules."""
    min_u, max_u, min_v, max_v = local_bounds(polygon, basis)
    slide_step = 3.0

    stalls = []
    aisles = []
    run_count = 0
    v = min_v

    while v + geometry["single_module"] <= max_v + 0.001:
        placed_depth = None

        for depth, rows in (
            (geometry["double_module"], 2),
            (geometry["single_module"], 1),
        ):
            if v + depth > max_v + 0.001:
                continue

            columns = bay_columns(polygon, basis, v, geometry, rows, clearance, min_u, max_u)
            runs = []
            run_start = None
            for index in range(len(columns) + 1):
                fits = columns[index][1] if index < len(columns) else False
                if fits and run_start is None:
                    run_start = index
                elif not fits and run_start is not None:
                    runs.append((run_start, index))
                    run_start = None

            placed_any = False
            for start, end in runs:
                if end - start < MIN_RUN_COLUMNS:
                    continue

                left_u = columns[start][0]
                right_u = columns[end - 1][0] + geometry["stall_pitch"]
                connected = (
                    touches_ring(polygon, basis, left_u, v, depth, clearance)
                    or touches_ring(polygon, basis, right_u, v, depth, clearance)
                )
                if not connected:
                    continue

                for index in range(start, end):
                    for shape in columns[index][2]:
                        stalls.append([
                            (to_world(basis, su, sv)[0], to_world(basis, su, sv)[1], z)
                            for su, sv in shape
                        ])

                aisles.append(rectangle_world(
                    basis,
                    left_u,
                    v + geometry["row_depth"],
                    right_u - left_u,
                    geometry["aisle"],
                    z,
                ))
                run_count += 1
                placed_any = True

            if placed_any:
                placed_depth = depth
                break

        # Slide upward until a bay fits so bays are not locked to a fixed grid.
        v += placed_depth if placed_depth else slide_step

    if not stalls:
        return None

    return {
        "stalls": stalls,
        "aisles": aisles,
        "stall_count": len(stalls),
        "run_count": run_count,
        "angle": basis["angle"],
        "park_angle": geometry["park_angle"],
        "flow": geometry["flow"],
    }


def convex_overlap(poly_a, poly_b):
    """Separating axis test for two convex polygons."""
    for polygon in (poly_a, poly_b):
        count = len(polygon)
        for index in range(count):
            ax, ay = polygon[index]
            bx, by = polygon[(index + 1) % count]
            axis_x, axis_y = -(by - ay), bx - ax
            length = math.hypot(axis_x, axis_y)
            if length < 1e-9:
                continue
            axis_x /= length
            axis_y /= length

            a_values = [x * axis_x + y * axis_y for x, y in poly_a]
            b_values = [x * axis_x + y * axis_y for x, y in poly_b]
            if max(a_values) <= min(b_values) + 0.01 or max(b_values) <= min(a_values) + 0.01:
                return False
    return True


def perimeter_row(polygon, z, base_offset, placed=None, stall_width=STALL_WIDTH):
    """Place a ring of 90 degree stalls backing onto the given offset line."""
    stalls = []
    placed = placed if placed is not None else []
    count = len(polygon)

    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        edge_length = math.hypot(bx - ax, by - ay)
        if edge_length < stall_width * 2:
            continue

        dx = (bx - ax) / edge_length
        dy = (by - ay) / edge_length
        nx, ny = -dy, dx
        mid_x = 0.5 * (ax + bx)
        mid_y = 0.5 * (ay + by)
        if not point_inside(polygon, mid_x + nx, mid_y + ny):
            nx, ny = -nx, -ny

        stall_count = int((edge_length - 0.001) / stall_width)
        margin = (edge_length - stall_count * stall_width) * 0.5

        for slot in range(stall_count):
            t = margin + slot * stall_width
            base_x = ax + dx * t + nx * base_offset
            base_y = ay + dy * t + ny * base_offset
            corners = [
                (base_x, base_y),
                (base_x + dx * stall_width, base_y + dy * stall_width),
                (base_x + dx * stall_width + nx * STALL_STRIPE, base_y + dy * stall_width + ny * STALL_STRIPE),
                (base_x + nx * STALL_STRIPE, base_y + ny * STALL_STRIPE),
            ]

            usable = True
            for corner_x, corner_y in corners:
                if not has_clearance(polygon, corner_x, corner_y, base_offset - 0.05):
                    usable = False
                    break
            if not usable:
                continue

            if any(convex_overlap(corners, other) for other in placed):
                continue

            placed.append(corners)
            stalls.append([(x, y, z) for x, y in corners])

    return stalls, placed


def build_variants(polygon, z, setback, stall_width=STALL_WIDTH):
    """Plan options that differ in how many stall rows the ring drive serves."""
    outer_row, placed = perimeter_row(polygon, z, setback, None, stall_width)
    ring_outer = setback + STALL_STRIPE
    inner_row, _ = perimeter_row(polygon, z, ring_outer + RING_WIDTH, list(placed), stall_width)

    variants = []
    if outer_row and inner_row:
        variants.append((
            outer_row + inner_row,
            ring_outer,
            ring_outer + RING_WIDTH + STALL_STRIPE,
        ))
    if outer_row:
        variants.append((outer_row, ring_outer, ring_outer + RING_WIDTH))
    variants.append(([], setback, setback + RING_WIDTH))
    return variants


def best_layout(polygon, z, setback, access_points=None, stall_width=STALL_WIDTH):
    origin = polygon[0]
    angles = candidate_angles(polygon, access_points)
    geometries = [module_geometry(angle, flow, stall_width) for angle, flow in PARK_CONFIGS]
    best = None

    for perimeter_stalls, ring_outer, clearance in build_variants(polygon, z, setback, stall_width):
        for angle in angles:
            basis = make_basis(origin, angle)

            for geometry in geometries:
                interior = layout_for_angle(polygon, basis, clearance, z, geometry)
                interior_stalls = interior["stalls"] if interior else []

                total = len(perimeter_stalls) + len(interior_stalls)
                if total == 0:
                    continue

                candidate = {
                    "stalls": perimeter_stalls + interior_stalls,
                    "aisles": interior["aisles"] if interior else [],
                    "stall_count": total,
                    "run_count": interior["run_count"] if interior else 0,
                    "angle": interior["angle"] if interior else 0.0,
                    "park_angle": interior["park_angle"] if interior else 90,
                    "flow": interior["flow"] if interior else "two-way",
                    "ring_outer": ring_outer,
                    "ring_inner": ring_outer + RING_WIDTH,
                    "perimeter_stalls": len(perimeter_stalls),
                }

                if best is None or candidate["stall_count"] > best["stall_count"]:
                    best = candidate

    if best:
        best["ada"] = ada_stall_count(best["stall_count"])

    return best


def ring_band_points(polygon, z, outer_distance, inner_distance):
    """Return the ring drive as (outer, inner) closed point lists."""
    outer = offset_polygon(polygon, outer_distance)
    inner = offset_polygon(polygon, inner_distance)
    if not outer or not inner:
        return None, None

    outer_points = [(x, y, z) for x, y in outer]
    inner_points = [(x, y, z) for x, y in inner]
    return outer_points, inner_points


def offset_polygon(polygon, distance):
    """Offset a closed polygon inward using vertex bisectors."""
    if distance <= 0:
        return list(polygon)

    count = len(polygon)
    if count < 3:
        return None

    area = 0.0
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        area += ax * by - bx * ay
    orientation = 1.0 if area > 0 else -1.0

    result = []
    for index in range(count):
        prev_x, prev_y = polygon[index - 1]
        cur_x, cur_y = polygon[index]
        next_x, next_y = polygon[(index + 1) % count]

        in_dx, in_dy = cur_x - prev_x, cur_y - prev_y
        out_dx, out_dy = next_x - cur_x, next_y - cur_y
        in_len = math.hypot(in_dx, in_dy)
        out_len = math.hypot(out_dx, out_dy)
        if in_len < 1e-9 or out_len < 1e-9:
            continue

        in_nx, in_ny = -in_dy / in_len * orientation, in_dx / in_len * orientation
        out_nx, out_ny = -out_dy / out_len * orientation, out_dx / out_len * orientation

        bisector_x = in_nx + out_nx
        bisector_y = in_ny + out_ny
        bisector_len = math.hypot(bisector_x, bisector_y)
        if bisector_len < 1e-9:
            continue

        bisector_x /= bisector_len
        bisector_y /= bisector_len
        cos_half = max(bisector_x * in_nx + bisector_y * in_ny, 0.2)
        scale = distance / cos_half
        result.append((cur_x + bisector_x * scale, cur_y + bisector_y * scale))

    if len(result) < 3:
        return None
    return result
