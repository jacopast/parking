"""Shared surface parking layout logic for the Rhino parking tools.

Geometry engine
---------------
Surface lots are planned the way practitioners iterate a sketch:

1. Try aisle / grid orientations aligned to long site edges (and access).
2. For each orientation, try to seat an orthogonal circulation loop
   (a rectangular "racetrack" in that frame) so the drive has no oblique
   corners relative to the parking grid.
3. Load only the outside of the loop with a perimeter stall row (backs to
   the setback / property edge). The inside of the loop is not single-loaded
   again — that would waste a module edge.
4. Fill everything inside the ring with a 90 degree double-loaded module
   grid, searching lattice phase (tile-and-trim).
5. Keep the candidate with the most driveable stalls. Prefer orthogonal
   rings over site-offset rings when counts tie.

If no orthogonal racetrack fits (awkward pockets), fall back to a ring that
offsets the site boundary. Diagonal 60/45 modules are last-resort only when
90 degree search returns nothing.

Module widths come from Iowa SUDAS 8B-1 Table 8B-1.02 (ULI/NPA). They set
the lattice period; trial-and-error over orientation and ring geometry
chooses the layout.
"""

import math


STALL_WIDTH = 9.0
STALL_STRIPE = 18.0
STALL_DEPTH = STALL_STRIPE
AISLE_WIDTH = 24.0
DOUBLE_LOADED_MODULE = 2 * STALL_STRIPE + AISLE_WIDTH
RING_WIDTH = 24.0
MIN_RUN_COLUMNS = 3
MIN_CORE_SPAN = 40.0

AISLE_WIDTHS = {
    (90, "two-way"): 24.0,
    (60, "two-way"): 25.833,
    (60, "one-way"): 20.333,
    (45, "two-way"): 29.667,
    (45, "one-way"): 21.5,
}

PARK_CONFIGS = [
    (90, "two-way"),
]

DIAGONAL_FALLBACK_CONFIGS = [
    (60, "one-way"),
    (45, "one-way"),
    (60, "two-way"),
    (45, "two-way"),
]

EFFICIENCY_TARGET_SF_PER_STALL = 330.0

# Trial-and-error resolution for lattice phase and ortho ring nudges.
V_PHASE_STEPS = 4
U_PHASE_STEPS = 3
RING_SHIFT_STEPS = 3
MAX_ORIENTATIONS = 8


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


def polygon_centroid(polygon):
    area = 0.0
    cx = 0.0
    cy = 0.0
    count = len(polygon)
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        cross = ax * by - bx * ay
        area += cross
        cx += (ax + bx) * cross
        cy += (ay + by) * cross
    area *= 0.5
    if abs(area) < 1e-9:
        return (
            sum(point[0] for point in polygon) / float(count),
            sum(point[1] for point in polygon) / float(count),
        )
    return (cx / (6.0 * area), cy / (6.0 * area))


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


def angle_key(angle_deg, precision=1.0):
    return round(normalize_angle(angle_deg) / precision) * precision


def nearest_edge_index(polygon, point, max_distance=None):
    """Return the polygon edge index closest to a world point, or None."""
    px, py = as_tuple(point)[0], as_tuple(point)[1]
    best = None
    count = len(polygon)
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        distance = distance_to_segment(px, py, ax, ay, bx, by)
        if best is None or distance < best[0]:
            best = (distance, index)

    if best is None:
        return None
    if max_distance is not None and best[0] > max_distance:
        return None
    return best[1]


def street_edge_from_pick(polygon, point, max_distance=12.0):
    """Resolve a pick on/near the boundary to the street-frontage edge.

    Returns dict with index, endpoints a/b, length, and mid point, or None.
    """
    index = nearest_edge_index(polygon, point, max_distance)
    if index is None:
        # Fall back to nearest edge even if the pick was a bit off the curve.
        index = nearest_edge_index(polygon, point, None)
    if index is None:
        return None

    a = polygon[index]
    b = polygon[(index + 1) % len(polygon)]
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length < 1.0:
        return None

    return {
        "index": index,
        "a": a,
        "b": b,
        "length": length,
        "mid": (0.5 * (a[0] + b[0]), 0.5 * (a[1] + b[1])),
    }


def access_points_on_street_edge(street_edge):
    """Place curb-cut centers along the street edge (entry / exit)."""
    if not street_edge:
        return []

    ax, ay = street_edge["a"]
    bx, by = street_edge["b"]
    # One-third and two-thirds along the frontage; collapses to mid on short edges.
    if street_edge["length"] < STALL_WIDTH * 4:
        return [street_edge["mid"]]

    return [
        (ax + (bx - ax) / 3.0, ay + (by - ay) / 3.0),
        (ax + 2.0 * (bx - ax) / 3.0, ay + 2.0 * (by - ay) / 3.0),
    ]


def candidate_orientations(polygon, access_points=None, street_edge=None):
    """Edge-aligned grid directions, weighted by edge length.

    A designated street-frontage edge is weighted strongly so aisles prefer
    to run parallel / perpendicular to the public road.
    """
    weights = {}

    def add_angle(angle_deg, weight):
        key = angle_key(angle_deg)
        weights[key] = weights.get(key, 0.0) + weight

    if street_edge:
        ax, ay = street_edge["a"]
        bx, by = street_edge["b"]
        length = street_edge["length"]
        street_angle = math.degrees(math.atan2(by - ay, bx - ax))
        # Prefer aisles perpendicular to the street (cars face the road edge)
        # and keep the street-parallel option as a strong alternate.
        add_angle(street_angle + 90.0, length * 4.0)
        add_angle(street_angle, length * 3.0)

    access_points = access_points or []
    if not street_edge and len(access_points) >= 2:
        a = as_tuple(access_points[0])
        b = as_tuple(access_points[1])
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length > 1.0:
            access_angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            add_angle(access_angle, length * 2.0)
            add_angle(access_angle + 90.0, length)

    count = len(polygon)
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        length = math.hypot(bx - ax, by - ay)
        if length < 5.0:
            continue
        edge_angle = math.degrees(math.atan2(by - ay, bx - ax))
        add_angle(edge_angle, length)
        add_angle(edge_angle + 90.0, length * 0.75)

    add_angle(0.0, 1.0)
    add_angle(90.0, 1.0)

    ranked = sorted(weights.items(), key=lambda item: (-item[1], item[0]))
    return [angle for angle, _weight in ranked[:MAX_ORIENTATIONS]]


def candidate_angles(polygon, access_points=None, street_edge=None):
    return candidate_orientations(polygon, access_points, street_edge)


def local_polygon_fits(polygon, basis, points, clearance, edge_midpoints=True):
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


def rect_world_polygon(basis, u0, u1, v0, v1, z=None):
    corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    if z is None:
        return [to_world(basis, u, v) for u, v in corners]
    return [(to_world(basis, u, v)[0], to_world(basis, u, v)[1], z) for u, v in corners]


def rect_polygon_2d(basis, u0, u1, v0, v1):
    return [to_world(basis, u, v) for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]


def rectangle_inside_polygon(polygon, basis, u0, u1, v0, v1, margin=0.0):
    """Sample the rectangle boundary / midlines; all samples must stay inside."""
    if u1 - u0 < 1.0 or v1 - v0 < 1.0:
        return False

    samples = [
        (u0, v0), (u1, v0), (u1, v1), (u0, v1),
        (0.5 * (u0 + u1), v0), (0.5 * (u0 + u1), v1),
        (u0, 0.5 * (v0 + v1)), (u1, 0.5 * (v0 + v1)),
        (0.5 * (u0 + u1), 0.5 * (v0 + v1)),
    ]
    # Extra edge samples so skewed site edges do not clip a long side.
    for t in (0.25, 0.75):
        samples.extend([
            (u0 + (u1 - u0) * t, v0),
            (u0 + (u1 - u0) * t, v1),
            (u0, v0 + (v1 - v0) * t),
            (u1, v0 + (v1 - v0) * t),
        ])

    for u, v in samples:
        x, y = to_world(basis, u, v)
        if not has_clearance(polygon, x, y, margin):
            return False
    return True


def fitted_ortho_rect(polygon, basis, margin=0.0):
    """Largest axis-aligned rectangle in the parking frame that fits the site.

    Uniform inset of the oriented bounding box, found by binary search.
    This is the racetrack footprint that keeps circulation orthogonal to the
    stall grid (no oblique ring corners in UV).
    """
    min_u, max_u, min_v, max_v = local_bounds(polygon, basis)
    width = max_u - min_u
    height = max_v - min_v
    if width < MIN_CORE_SPAN or height < MIN_CORE_SPAN:
        return None

    lo = 0.0
    hi = 0.5 * min(width, height)
    best = None

    for _ in range(28):
        mid = 0.5 * (lo + hi)
        u0 = min_u + mid
        u1 = max_u - mid
        v0 = min_v + mid
        v1 = max_v - mid
        if u1 - u0 < MIN_CORE_SPAN or v1 - v0 < MIN_CORE_SPAN:
            hi = mid
            continue
        if rectangle_inside_polygon(polygon, basis, u0, u1, v0, v1, margin):
            best = (u0, u1, v0, v1)
            hi = mid
        else:
            lo = mid

    return best


def ortho_rect_candidates(polygon, basis, setback, stall_width):
    """Trial racetrack footprints: fitted rect plus pitch-aligned nudges."""
    fitted = fitted_ortho_rect(polygon, basis, setback)
    if not fitted:
        return []

    u0, u1, v0, v1 = fitted
    pitch = stall_width
    candidates = [(u0, u1, v0, v1)]

    for step in range(1, RING_SHIFT_STEPS):
        du = pitch * step / float(RING_SHIFT_STEPS)
        dv = DOUBLE_LOADED_MODULE * step / float(RING_SHIFT_STEPS)
        for trial in (
            (u0 + du, u1, v0, v1),
            (u0, u1 - du, v0, v1),
            (u0, u1, v0 + dv, v1),
            (u0, u1, v0, v1 - dv),
            (u0 + du, u1 - du, v0 + dv, v1 - dv),
        ):
            tu0, tu1, tv0, tv1 = trial
            if tu1 - tu0 < MIN_CORE_SPAN or tv1 - tv0 < MIN_CORE_SPAN:
                continue
            if rectangle_inside_polygon(polygon, basis, tu0, tu1, tv0, tv1, setback):
                candidates.append(trial)

    # De-duplicate roughly.
    unique = []
    seen = set()
    for rect in candidates:
        key = tuple(round(value, 2) for value in rect)
        if key in seen:
            continue
        seen.add(key)
        unique.append(rect)
    return unique


def touches_ring(polygon, basis, u, v0, depth, clearance):
    for step in (0.0, 0.5, 1.0):
        x, y = to_world(basis, u, v0 + depth * step)
        if not point_inside(polygon, x, y):
            return True
        if distance_to_polygon(polygon, x, y) <= clearance + STALL_WIDTH * 2.0:
            return True
    return False


def bay_columns(polygon, basis, v, geometry, rows, clearance, min_u, max_u, u_phase=0.0):
    pitch = geometry["stall_pitch"]
    row_depth = geometry["row_depth"]
    aisle = geometry["aisle"]
    if pitch <= 1e-9:
        return []

    start_u = min_u + (u_phase % pitch)
    while start_u > min_u + 1e-9:
        start_u -= pitch

    columns = []
    u = start_u
    while u + pitch <= max_u + 0.001:
        if u + pitch < min_u - 0.001:
            u += pitch
            continue

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


def place_bay_runs(polygon, basis, v, geometry, rows, depth, clearance, min_u, max_u, u_phase, z):
    columns = bay_columns(polygon, basis, v, geometry, rows, clearance, min_u, max_u, u_phase)
    runs = []
    run_start = None
    for index in range(len(columns) + 1):
        fits = columns[index][1] if index < len(columns) else False
        if fits and run_start is None:
            run_start = index
        elif not fits and run_start is not None:
            runs.append((run_start, index))
            run_start = None

    stalls = []
    aisles = []
    run_count = 0

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

    return stalls, aisles, run_count


def layout_for_phase(polygon, basis, clearance, z, geometry, u_phase, v_phase):
    min_u, max_u, min_v, max_v = local_bounds(polygon, basis)
    period = geometry["double_module"]
    if period <= 1e-9:
        return None

    stalls = []
    aisles = []
    run_count = 0

    start_v = min_v + (v_phase % period)
    while start_v > min_v + 1e-9:
        start_v -= period

    v = start_v
    while v <= max_v + 0.001:
        if v + geometry["double_module"] <= max_v + 0.001:
            bay_stalls, bay_aisles, bay_runs = place_bay_runs(
                polygon, basis, v, geometry, 2, geometry["double_module"],
                clearance, min_u, max_u, u_phase, z,
            )
            if bay_runs:
                stalls.extend(bay_stalls)
                aisles.extend(bay_aisles)
                run_count += bay_runs
                v += geometry["double_module"]
                continue

        if v + geometry["single_module"] <= max_v + 0.001:
            bay_stalls, bay_aisles, bay_runs = place_bay_runs(
                polygon, basis, v, geometry, 1, geometry["single_module"],
                clearance, min_u, max_u, u_phase, z,
            )
            if bay_runs:
                stalls.extend(bay_stalls)
                aisles.extend(bay_aisles)
                run_count += bay_runs
                v += geometry["single_module"]
                continue

        v += geometry["row_depth"] if geometry["row_depth"] > 1.0 else 6.0

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
        "u_phase": u_phase,
        "v_phase": v_phase,
    }


def layout_for_angle(polygon, basis, clearance, z, geometry):
    pitch = geometry["stall_pitch"]
    period = geometry["double_module"]
    best = None

    for v_step in range(V_PHASE_STEPS):
        v_phase = period * v_step / float(V_PHASE_STEPS)
        for u_step in range(U_PHASE_STEPS):
            u_phase = pitch * u_step / float(U_PHASE_STEPS)
            candidate = layout_for_phase(
                polygon, basis, clearance, z, geometry, u_phase, v_phase,
            )
            if candidate is None:
                continue
            if best is None or candidate["stall_count"] > best["stall_count"]:
                best = candidate

    return best


def convex_overlap(poly_a, poly_b):
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


def stall_corners_world(base_x, base_y, dx, dy, nx, ny, stall_width, depth):
    return [
        (base_x, base_y),
        (base_x + dx * stall_width, base_y + dy * stall_width),
        (base_x + dx * stall_width + nx * depth, base_y + dy * stall_width + ny * depth),
        (base_x + nx * depth, base_y + ny * depth),
    ]


def stalls_along_world_edge(
    ax, ay, bx, by, extend_x, extend_y, site_polygon, z,
    stall_width=STALL_WIDTH, depth=STALL_STRIPE, occupied=None, min_clearance=0.0,
):
    """Place a 90 degree stall row along a world-space edge."""
    occupied = occupied if occupied is not None else []
    edge_length = math.hypot(bx - ax, by - ay)
    if edge_length < stall_width * MIN_RUN_COLUMNS:
        return [], occupied

    dx = (bx - ax) / edge_length
    dy = (by - ay) / edge_length
    extend_len = math.hypot(extend_x, extend_y)
    if extend_len < 1e-9:
        return [], occupied
    nx = extend_x / extend_len
    ny = extend_y / extend_len

    count = int((edge_length - 0.001) / stall_width)
    margin = (edge_length - count * stall_width) * 0.5
    stalls = []

    for slot in range(count):
        t = margin + slot * stall_width
        base_x = ax + dx * t
        base_y = ay + dy * t
        corners = stall_corners_world(base_x, base_y, dx, dy, nx, ny, stall_width, depth)

        usable = True
        for corner_x, corner_y in corners:
            if not has_clearance(site_polygon, corner_x, corner_y, min_clearance):
                usable = False
                break
        if not usable:
            continue
        if any(convex_overlap(corners, other) for other in occupied):
            continue

        occupied.append(corners)
        stalls.append([(x, y, z) for x, y in corners])

    return stalls, occupied


def perimeter_row(polygon, z, base_offset, placed=None, stall_width=STALL_WIDTH):
    """Offset-ring helper: stalls backing onto a constant-offset contour."""
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
            corners = stall_corners_world(
                base_x, base_y, dx, dy, nx, ny, stall_width, STALL_STRIPE,
            )

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


def ortho_ring_stalls(basis, site_polygon, z, ru0, ru1, rv0, rv1,
                      stall_width, setback):
    """Perimeter stalls on the outside of an orthogonal racetrack only."""
    occupied = []
    stalls = []

    outer_edges = [
        ((ru0, rv0), (ru1, rv0), (0.0, -1.0)),
        ((ru1, rv0), (ru1, rv1), (1.0, 0.0)),
        ((ru1, rv1), (ru0, rv1), (0.0, 1.0)),
        ((ru0, rv1), (ru0, rv0), (-1.0, 0.0)),
    ]

    for (a, b, local_out) in outer_edges:
        ax, ay = to_world(basis, a[0], a[1])
        bx, by = to_world(basis, b[0], b[1])
        out_x = basis["u"][0] * local_out[0] + basis["v"][0] * local_out[1]
        out_y = basis["u"][1] * local_out[0] + basis["v"][1] * local_out[1]
        row, occupied = stalls_along_world_edge(
            ax, ay, bx, by, out_x, out_y,
            site_polygon, z, stall_width, STALL_STRIPE, occupied, setback,
        )
        stalls.extend(row)

    return stalls


def compose_candidate(ring_stalls, interior, basis, geometry, ring_meta):
    interior_stalls = interior["stalls"] if interior else []
    total = len(ring_stalls) + len(interior_stalls)
    if total == 0:
        return None

    return {
        "stalls": list(ring_stalls) + interior_stalls,
        "aisles": interior["aisles"] if interior else [],
        "stall_count": total,
        "run_count": interior["run_count"] if interior else 0,
        "angle": basis["angle"],
        "park_angle": geometry["park_angle"],
        "flow": geometry["flow"],
        "u_phase": interior.get("u_phase", 0.0) if interior else 0.0,
        "v_phase": interior.get("v_phase", 0.0) if interior else 0.0,
        "perimeter_stalls": len(ring_stalls),
        "ring_mode": ring_meta["ring_mode"],
        "ring_outer": ring_meta.get("ring_outer", 0.0),
        "ring_inner": ring_meta.get("ring_inner", 0.0),
        "ring_outer_poly": ring_meta.get("ring_outer_poly"),
        "ring_inner_poly": ring_meta.get("ring_inner_poly"),
        "ortho_bonus": 1 if ring_meta["ring_mode"] == "ortho" else 0,
    }


def candidate_score(candidate):
    """Stall count first; small bonus for orthogonal (no oblique) circulation."""
    if candidate is None:
        return -1
    return candidate["stall_count"] + (5 if candidate.get("ortho_bonus") else 0)


def better_candidate(current, challenger):
    if challenger is None:
        return current
    if current is None:
        return challenger
    if candidate_score(challenger) > candidate_score(current):
        return challenger
    return current


def try_ortho_layouts(polygon, basis, z, setback, geometry, stall_width):
    """Trial orthogonal racetracks: outer stalls on the ring, grid inside."""
    best = None
    rects = ortho_rect_candidates(polygon, basis, setback, stall_width)

    for u0, u1, v0, v1 in rects:
        # Pad edge -> outer stall band -> ring -> interior grid core.
        for use_outer in (True, False):
            outer_band = STALL_STRIPE if use_outer else 0.0
            ru0 = u0 + outer_band
            ru1 = u1 - outer_band
            rv0 = v0 + outer_band
            rv1 = v1 - outer_band
            if ru1 - ru0 < RING_WIDTH * 2 + MIN_CORE_SPAN:
                continue
            if rv1 - rv0 < RING_WIDTH * 2 + MIN_CORE_SPAN:
                continue

            iu0 = ru0 + RING_WIDTH
            iu1 = ru1 - RING_WIDTH
            iv0 = rv0 + RING_WIDTH
            iv1 = rv1 - RING_WIDTH

            # Interior starts at the inner curb — no second perimeter row.
            cu0, cu1, cv0, cv1 = iu0, iu1, iv0, iv1

            ring_stalls = []
            if use_outer:
                ring_stalls = ortho_ring_stalls(
                    basis, polygon, z,
                    ru0, ru1, rv0, rv1,
                    stall_width, setback,
                )

            interior = None
            if cu1 - cu0 >= MIN_CORE_SPAN and cv1 - cv0 >= geometry["single_module"]:
                core_poly = rect_polygon_2d(basis, cu0, cu1, cv0, cv1)
                interior = layout_for_angle(core_poly, basis, 0.0, z, geometry)

            ring_meta = {
                "ring_mode": "ortho",
                "ring_outer_poly": rect_world_polygon(basis, ru0, ru1, rv0, rv1, z),
                "ring_inner_poly": rect_world_polygon(basis, iu0, iu1, iv0, iv1, z),
                "ring_outer": 0.0,
                "ring_inner": RING_WIDTH,
            }
            candidate = compose_candidate(ring_stalls, interior, basis, geometry, ring_meta)
            best = better_candidate(best, candidate)

    return best


def build_offset_variants(polygon, z, setback, stall_width=STALL_WIDTH):
    """Site-following ring: outer stalls only, then grid inside the ring."""
    outer_row, _placed = perimeter_row(polygon, z, setback, None, stall_width)
    ring_outer = setback + STALL_STRIPE

    variants = []
    if outer_row:
        # Clearance = inner curb of the ring; interior grid fills from there in.
        variants.append((outer_row, ring_outer, ring_outer + RING_WIDTH))
    variants.append(([], setback, setback + RING_WIDTH))
    return variants


def try_offset_layouts(polygon, basis, z, setback, geometry, stall_width):
    best = None
    for perimeter_stalls, ring_outer, clearance in build_offset_variants(
        polygon, z, setback, stall_width,
    ):
        interior = layout_for_angle(polygon, basis, clearance, z, geometry)
        outer_poly, inner_poly = ring_band_points(polygon, z, ring_outer, ring_outer + RING_WIDTH)
        ring_meta = {
            "ring_mode": "offset",
            "ring_outer": ring_outer,
            "ring_inner": ring_outer + RING_WIDTH,
            "ring_outer_poly": outer_poly,
            "ring_inner_poly": inner_poly,
        }
        candidate = compose_candidate(perimeter_stalls, interior, basis, geometry, ring_meta)
        best = better_candidate(best, candidate)
    return best


def _search_layouts(polygon, z, setback, access_points, stall_width, park_configs, street_edge=None):
    """Trial-and-error over orientation, orthogonal ring, then offset ring."""
    origin = polygon_centroid(polygon)
    orientations = candidate_orientations(polygon, access_points, street_edge)
    geometries = [module_geometry(angle, flow, stall_width) for angle, flow in park_configs]
    best = None

    for angle in orientations:
        basis = make_basis(origin, angle)
        for geometry in geometries:
            ortho = try_ortho_layouts(polygon, basis, z, setback, geometry, stall_width)
            best = better_candidate(best, ortho)

            offset = try_offset_layouts(polygon, basis, z, setback, geometry, stall_width)
            best = better_candidate(best, offset)

    return best


def best_layout(polygon, z, setback, access_points=None, stall_width=STALL_WIDTH, street_edge=None):
    """Pack with 90 degree stalls; diagonal only if perpendicular finds nothing."""
    if street_edge and not access_points:
        access_points = access_points_on_street_edge(street_edge)

    best = _search_layouts(
        polygon, z, setback, access_points, stall_width, PARK_CONFIGS, street_edge,
    )

    if best is None:
        best = _search_layouts(
            polygon, z, setback, access_points, stall_width, DIAGONAL_FALLBACK_CONFIGS, street_edge,
        )

    if best:
        best["ada"] = ada_stall_count(best["stall_count"])
        if street_edge:
            best["street_edge"] = street_edge

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


def layout_ring_polylines(layout, site_polygon, z):
    """Prefer explicit ring polygons from the chosen candidate."""
    outer = layout.get("ring_outer_poly")
    inner = layout.get("ring_inner_poly")
    if outer and inner:
        return outer, inner
    return ring_band_points(
        site_polygon, z, layout.get("ring_outer", 0.0), layout.get("ring_inner", RING_WIDTH),
    )


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
