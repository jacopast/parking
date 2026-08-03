"""Shared surface parking layout logic for the Rhino parking tools.

The generator follows normal surface parking practice:

1. hold a setback from the property line
2. run a continuous perimeter ring drive inside that setback
3. fill only the region inside the ring with double-loaded bays
   (18 ft stall + 24 ft aisle + 18 ft stall)
4. keep a bay run only when it is long enough to be usable, so every
   aisle ends on the ring drive at both ends

Because parking is limited to the region inside the ring, every aisle
reaches the ring, and the ring reaches the entrance and exit.
"""

import math


STALL_WIDTH = 9.0
STALL_DEPTH = 18.0
AISLE_WIDTH = 24.0
DOUBLE_LOADED_MODULE = STALL_DEPTH + AISLE_WIDTH + STALL_DEPTH
RING_WIDTH = 24.0
MIN_RUN_COLUMNS = 3
ANGLE_STEP_DEG = 15.0


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


def block_fits(polygon, basis, u0, v0, width, depth, clearance):
    """Test a local-space rectangle against the buildable region."""
    steps = max(int(math.ceil(depth / 6.0)), 2)
    for column in (0.0, 0.5, 1.0):
        u = u0 + width * column
        for step in range(steps + 1):
            v = v0 + depth * (float(step) / steps)
            x, y = to_world(basis, u, v)
            if not has_clearance(polygon, x, y, clearance):
                return False
    return True


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
        if distance_to_polygon(polygon, x, y) <= clearance + STALL_WIDTH * 1.5:
            return True
    return False


def stripe_runs(polygon, basis, v, depth, clearance, min_u, max_u):
    """Find contiguous 9 ft column runs where a full bay depth fits."""
    columns = []
    u = min_u
    while u + STALL_WIDTH <= max_u + 0.001:
        columns.append((u, block_fits(polygon, basis, u, v, STALL_WIDTH, depth, clearance)))
        u += STALL_WIDTH

    runs = []
    run_start = None
    for index in range(len(columns) + 1):
        fits = columns[index][1] if index < len(columns) else False
        if fits and run_start is None:
            run_start = index
        elif not fits and run_start is not None:
            length = index - run_start
            left_u = columns[run_start][0]
            right_u = left_u + length * STALL_WIDTH
            connected = (
                touches_ring(polygon, basis, left_u, v, depth, clearance)
                or touches_ring(polygon, basis, right_u, v, depth, clearance)
            )
            if length >= MIN_RUN_COLUMNS and connected:
                runs.append((left_u, length))
            run_start = None

    return runs


def layout_for_angle(polygon, basis, clearance, z):
    """Fill the region inside the perimeter ring with parking bays."""
    min_u, max_u, min_v, max_v = local_bounds(polygon, basis)

    single_loaded_depth = STALL_DEPTH + AISLE_WIDTH
    slide_step = 3.0

    stalls = []
    aisles = []
    run_count = 0
    v = min_v

    while v + single_loaded_depth <= max_v + 0.001:
        placed_depth = None

        for depth, rows in ((DOUBLE_LOADED_MODULE, 2), (single_loaded_depth, 1)):
            if v + depth > max_v + 0.001:
                continue

            runs = stripe_runs(polygon, basis, v, depth, clearance, min_u, max_u)
            if not runs:
                continue

            for left_u, length in runs:
                for column in range(length):
                    column_u = left_u + column * STALL_WIDTH
                    stalls.append(rectangle_world(basis, column_u, v, STALL_WIDTH, STALL_DEPTH, z))
                    if rows == 2:
                        stalls.append(rectangle_world(
                            basis,
                            column_u,
                            v + STALL_DEPTH + AISLE_WIDTH,
                            STALL_WIDTH,
                            STALL_DEPTH,
                            z,
                        ))
                aisles.append(rectangle_world(
                    basis,
                    left_u,
                    v + STALL_DEPTH,
                    length * STALL_WIDTH,
                    AISLE_WIDTH,
                    z,
                ))
                run_count += 1

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


def perimeter_row(polygon, z, base_offset, placed=None):
    """Place a ring of stalls whose backs sit on the given offset line."""
    stalls = []
    placed = placed if placed is not None else []
    count = len(polygon)

    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        edge_length = math.hypot(bx - ax, by - ay)
        if edge_length < STALL_WIDTH * 2:
            continue

        dx = (bx - ax) / edge_length
        dy = (by - ay) / edge_length
        nx, ny = -dy, dx
        mid_x = 0.5 * (ax + bx)
        mid_y = 0.5 * (ay + by)
        if not point_inside(polygon, mid_x + nx, mid_y + ny):
            nx, ny = -nx, -ny

        stall_count = int((edge_length - 0.001) / STALL_WIDTH)
        margin = (edge_length - stall_count * STALL_WIDTH) * 0.5

        for slot in range(stall_count):
            t = margin + slot * STALL_WIDTH
            base_x = ax + dx * t + nx * base_offset
            base_y = ay + dy * t + ny * base_offset
            corners = [
                (base_x, base_y),
                (base_x + dx * STALL_WIDTH, base_y + dy * STALL_WIDTH),
                (base_x + dx * STALL_WIDTH + nx * STALL_DEPTH, base_y + dy * STALL_WIDTH + ny * STALL_DEPTH),
                (base_x + nx * STALL_DEPTH, base_y + ny * STALL_DEPTH),
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


def build_variants(polygon, z, setback):
    """Plan options that differ in how many stall rows the ring drive serves."""
    outer_row, placed = perimeter_row(polygon, z, setback)
    ring_outer = setback + STALL_DEPTH
    inner_row, _ = perimeter_row(polygon, z, ring_outer + RING_WIDTH, list(placed))

    variants = []
    if outer_row and inner_row:
        variants.append((
            outer_row + inner_row,
            ring_outer,
            ring_outer + RING_WIDTH + STALL_DEPTH,
        ))
    if outer_row:
        variants.append((outer_row, ring_outer, ring_outer + RING_WIDTH))
    variants.append(([], setback, setback + RING_WIDTH))
    return variants


def best_layout(polygon, z, setback, access_points=None):
    origin = polygon[0]
    angles = candidate_angles(polygon, access_points)
    best = None

    for perimeter_stalls, ring_outer, clearance in build_variants(polygon, z, setback):
        for angle in angles:
            basis = make_basis(origin, angle)
            interior = layout_for_angle(polygon, basis, clearance, z)
            interior_stalls = interior["stalls"] if interior else []

            total = len(perimeter_stalls) + len(interior_stalls)
            if total == 0:
                break

            candidate = {
                "stalls": perimeter_stalls + interior_stalls,
                "aisles": interior["aisles"] if interior else [],
                "stall_count": total,
                "run_count": interior["run_count"] if interior else 0,
                "angle": interior["angle"] if interior else 0.0,
                "ring_outer": ring_outer,
                "ring_inner": ring_outer + RING_WIDTH,
                "perimeter_stalls": len(perimeter_stalls),
            }

            if best is None or candidate["stall_count"] > best["stall_count"]:
                best = candidate

            if not interior:
                break

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
