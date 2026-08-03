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
   grid, searching lattice phase (tile-and-trim). Island aisles prefer the
   long axis of the core so each bay run is as long as possible.
5. Reject any stall in an acute corner, and reject any stall that does not
   have a clear 24 ft maneuvering aisle in front (SUDAS / ULI 90 degree rule).
6. Circulation may follow the site. Obtuse aisle corners are fine; only
   acute drive corners (< 90 deg) are forbidden. Sharp tips on an offset
   ring are chamfered so the drive stays driveable without cutting the
   whole site down to a tiny rectangle.
7. Leave clear entry/exit openings on the street frontage — no stalls in
   the driveway throats.
8. Reserve terminal (end-cap) landscape islands at both ends of every
   parking row so the turn into the cross aisle stays clear. Long runs
   also get interior islands so no more than ten stalls sit in a row
   without a break.
9. Keep the candidate with the most driveable stalls. Long-axis island
   aisles get a small tie-break bonus.

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
MIN_CORE_SPAN = 42.0  # at least one single-loaded module depth
# Smallest orthogonal racetrack footprint worth seating in a site.
MIN_RACETRACK_SPAN = RING_WIDTH * 2 + STALL_STRIPE + 24.0
DRIVEWAY_CLEAR = 28.0
# Interior angles sharper than this cannot host 90 degree stalls.
ACUTE_CORNER_DEG = 80.0
# Keep stalls this far from an acute vertex (stall depth + aisle throat).
ACUTE_KEEP_OUT = STALL_STRIPE + AISLE_WIDTH * 0.5
# Drive aisles / ring loops must not ask for a turn sharper than a right angle.
MIN_DRIVE_CORNER_DEG = 90.0
# End-cap / terminal islands replace the last stall column(s) at each row
# end so cars can turn at the aisle intersection. Width tracks the stall
# pitch (codes often cite 6x6 min or ~11 ft landscape islands).
TERMINAL_ISLAND_COLUMNS = 1
# Maximum consecutive stalls between landscape islands in a run.
MAX_STALLS_BETWEEN_ISLANDS = 10

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
V_PHASE_STEPS = 5
U_PHASE_STEPS = 4
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


def signed_area(polygon):
    area = 0.0
    count = len(polygon)
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        area += ax * by - bx * ay
    return 0.5 * area


def interior_angle_deg(polygon, index):
    """Interior angle at polygon[index] in degrees."""
    count = len(polygon)
    ax, ay = polygon[(index - 1) % count]
    bx, by = polygon[index]
    cx, cy = polygon[(index + 1) % count]
    in_x, in_y = bx - ax, by - ay
    out_x, out_y = cx - bx, cy - by
    turn = math.atan2(in_x * out_y - in_y * out_x, in_x * out_x + in_y * out_y)
    # CCW boundary: positive turn is left; interior = 180 - turn_deg.
    if signed_area(polygon) >= 0:
        interior = 180.0 - math.degrees(turn)
    else:
        interior = 180.0 + math.degrees(turn)
    while interior < 0:
        interior += 360.0
    while interior >= 360.0:
        interior -= 360.0
    return interior


def acute_vertices(polygon, threshold_deg=ACUTE_CORNER_DEG):
    """Vertices too sharp for 90 degree parking access."""
    result = []
    for index in range(len(polygon)):
        angle = interior_angle_deg(polygon, index)
        if angle < threshold_deg:
            result.append((index, angle, polygon[index]))
    return result


def as_xy_polygon(points):
    """Normalize ring polylines that may carry a Z component."""
    if not points:
        return []
    return [(point[0], point[1]) for point in points]


def polygon_min_interior_angle(polygon):
    if not polygon or len(polygon) < 3:
        return 0.0
    return min(interior_angle_deg(polygon, index) for index in range(len(polygon)))


def drive_path_has_sharp_turn(polygon, min_corner_deg=MIN_DRIVE_CORNER_DEG):
    """True when a corner is acute. Obtuse / right-angle turns are allowed."""
    poly = as_xy_polygon(polygon)
    if len(poly) < 3:
        return True
    return polygon_min_interior_angle(poly) < min_corner_deg - 0.5


def chamfer_acute_corners(polygon, min_corner_deg=MIN_DRIVE_CORNER_DEG, max_passes=10):
    """Attenuates acute tips so a drive loop never turns sharper than 90 deg.

    Obtuse corners are left alone. This lets site-following rings keep most of
    the parcel instead of collapsing to a tiny inscribed rectangle.
    """
    poly = [(p[0], p[1]) for p in as_xy_polygon(polygon)]
    if len(poly) < 3:
        return poly

    for _ in range(max_passes):
        n = len(poly)
        acute = [
            index for index in range(n)
            if interior_angle_deg(poly, index) < min_corner_deg - 0.5
        ]
        if not acute:
            return poly

        acute_set = set(acute)
        new_poly = []
        for index in range(n):
            curr = poly[index]
            if index not in acute_set:
                new_poly.append(curr)
                continue

            prev = poly[(index - 1) % n]
            nxt = poly[(index + 1) % n]
            d_prev = math.hypot(curr[0] - prev[0], curr[1] - prev[1])
            d_next = math.hypot(nxt[0] - curr[0], nxt[1] - curr[1])
            if d_prev < 2.0 or d_next < 2.0:
                continue

            # Cut far enough to blunt the tip, but never more than ~40% of an edge.
            cut = min(d_prev, d_next, max(RING_WIDTH * 0.75, min(d_prev, d_next) * 0.3))
            cut = min(cut, d_prev * 0.4, d_next * 0.4)
            if cut < 1.0:
                new_poly.append(curr)
                continue

            p1 = (
                curr[0] + (prev[0] - curr[0]) * (cut / d_prev),
                curr[1] + (prev[1] - curr[1]) * (cut / d_prev),
            )
            p2 = (
                curr[0] + (nxt[0] - curr[0]) * (cut / d_next),
                curr[1] + (nxt[1] - curr[1]) * (cut / d_next),
            )
            new_poly.append(p1)
            new_poly.append(p2)

        if len(new_poly) < 3:
            return poly
        poly = new_poly

    return poly


def ring_drive_is_acceptable(ring_outer_poly, ring_inner_poly=None):
    """Circulation may be obtuse or square; acute aisle corners are rejected."""
    if not ring_outer_poly:
        return False
    if drive_path_has_sharp_turn(ring_outer_poly):
        return False
    if ring_inner_poly and drive_path_has_sharp_turn(ring_inner_poly):
        return False
    return True


def near_acute_corner(x, y, polygon, keep_out=ACUTE_KEEP_OUT):
    for _index, _angle, (vx, vy) in acute_vertices(polygon):
        if math.hypot(x - vx, y - vy) <= keep_out:
            return True
    return False


def point_in_stall_xy(x, y, stall):
    return point_inside([(p[0], p[1]) for p in stall], x, y)


def stall_has_maneuvering_aisle(stall, site_polygon, occupied_stalls=None, aisle_ft=AISLE_WIDTH):
    """90 degree stalls need a full aisle width clear in front of the stall.

    SUDAS / ULI: perpendicular parking uses a 24 ft two-way aisle as the
    backout / maneuvering depth in front of the stall stripe.
    """
    occupied_stalls = occupied_stalls or []
    pts = [(p[0], p[1]) for p in stall]
    if len(pts) < 4:
        return False

    cx = sum(p[0] for p in pts) / 4.0
    cy = sum(p[1] for p in pts) / 4.0

    # The stall front is a short (stall-width) edge. Probe outward from each.
    best_ok = False
    for index in range(4):
        e0 = pts[index]
        e1 = pts[(index + 1) % 4]
        edge_len = math.hypot(e1[0] - e0[0], e1[1] - e0[1])
        if edge_len < STALL_WIDTH * 0.6 or edge_len > STALL_WIDTH * 1.4:
            continue

        mx = 0.5 * (e0[0] + e1[0])
        my = 0.5 * (e0[1] + e1[1])
        dx = e1[0] - e0[0]
        dy = e1[1] - e0[1]
        length = math.hypot(dx, dy)
        nx, ny = -dy / length, dx / length
        if (mx - cx) * nx + (my - cy) * ny < 0:
            nx, ny = -nx, -ny

        ok = True
        steps = 6
        for step in range(1, steps + 1):
            dist = aisle_ft * step / float(steps)
            x = mx + nx * dist
            y = my + ny * dist
            if not point_inside(site_polygon, x, y):
                ok = False
                break
            # Maneuvering depth may not be blocked by another stall.
            blocked = False
            for other in occupied_stalls:
                if other is stall:
                    continue
                if point_in_stall_xy(x, y, other):
                    blocked = True
                    break
            if blocked:
                ok = False
                break
        if ok:
            best_ok = True
            break

    return best_ok


def filter_driveable_stalls(stalls, site_polygon):
    """Drop stalls in acute tips or without a 24 ft clear aisle in front."""
    remaining = list(stalls)
    changed = True
    # Iterate: removing one stall can free aisle space for others, and also
    # reveal that a neighbor was only "clear" because we were wrong about tips.
    while changed:
        changed = False
        kept = []
        for stall in remaining:
            cx = sum(p[0] for p in stall) / 4.0
            cy = sum(p[1] for p in stall) / 4.0
            if near_acute_corner(cx, cy, site_polygon):
                changed = True
                continue
            others = [other for other in remaining if other is not stall]
            if not stall_has_maneuvering_aisle(stall, site_polygon, others):
                changed = True
                continue
            kept.append(stall)
        remaining = kept
    return remaining


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


def street_edge_from_index(polygon, index):
    """Build a street-edge record from a polygon edge index."""
    if index is None or index < 0 or index >= len(polygon):
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


def street_edge_from_pick(polygon, point, max_distance=12.0):
    """Resolve a pick on/near the boundary to the street-frontage edge."""
    index = nearest_edge_index(polygon, point, max_distance)
    if index is None:
        index = nearest_edge_index(polygon, point, None)
    return street_edge_from_index(polygon, index)


def pick_street_edge(polygon, z, rs, boundary_id=None):
    """Select one existing edge of the already-chosen site geometry.

    Temporary segment curves are offered for picking so the user does not
    draw anything new. Temps are deleted afterward.
    """
    temps = []
    index_by_id = {}

    try:
        for index in range(len(polygon)):
            a = polygon[index]
            b = polygon[(index + 1) % len(polygon)]
            line_id = rs.AddLine((a[0], a[1], z), (b[0], b[1], z))
            if not line_id:
                continue
            temps.append(line_id)
            index_by_id[str(line_id)] = index
            try:
                rs.ObjectColor(line_id, (0, 160, 255))
            except Exception:
                pass

        if not temps:
            return None

        try:
            rs.Redraw()
        except Exception:
            pass

        picked = rs.GetObject(
            "Select the street-frontage edge of the site (click one existing side — do not draw)",
            rs.filter.curve,
            False,
            False,
        )
        if not picked:
            return None

        index = index_by_id.get(str(picked))
        if index is not None:
            return street_edge_from_index(polygon, index)

        # Fallback: user clicked the original site curve instead of a temp edge.
        if boundary_id and str(picked) == str(boundary_id):
            point = rs.GetPointOnCurve(
                boundary_id,
                "Click the street-frontage side on the site boundary",
            )
            if point:
                return street_edge_from_pick(polygon, point)

        point = rs.CurveMidPoint(picked) if hasattr(rs, "CurveMidPoint") else None
        if point is None and hasattr(rs, "CurveStartPoint") and hasattr(rs, "CurveEndPoint"):
            start = rs.CurveStartPoint(picked)
            end = rs.CurveEndPoint(picked)
            if start and end:
                point = (
                    0.5 * (start[0] + end[0]),
                    0.5 * (start[1] + end[1]),
                    0.5 * (start[2] + end[2]) if len(start) > 2 else z,
                )
        if point:
            return street_edge_from_pick(polygon, point)
        return None
    finally:
        if temps:
            try:
                rs.DeleteObjects(temps)
            except Exception:
                for temp_id in temps:
                    try:
                        rs.DeleteObject(temp_id)
                    except Exception:
                        pass


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


def _ortho_rect_from_center(polygon, basis, cu, cv, max_half_u, max_half_v, margin):
    """Largest UV rectangle centered at (cu, cv) that stays inside the site."""
    best = None
    best_area = 0.0
    steps = 14
    for index in range(1, steps + 1):
        hu = max_half_u * index / float(steps)
        lo = 0.0
        hi = max_half_v
        fit_hv = None
        for _ in range(18):
            mid = 0.5 * (lo + hi)
            if rectangle_inside_polygon(
                polygon, basis, cu - hu, cu + hu, cv - mid, cv + mid, margin,
            ):
                fit_hv = mid
                lo = mid
            else:
                hi = mid
        if fit_hv is None:
            continue
        width = 2.0 * hu
        height = 2.0 * fit_hv
        if width < MIN_RACETRACK_SPAN or height < MIN_RACETRACK_SPAN:
            continue
        area = width * height
        if area > best_area:
            best_area = area
            best = (cu - hu, cu + hu, cv - fit_hv, cv + fit_hv)
    return best, best_area


def fitted_ortho_rect(polygon, basis, margin=0.0):
    """Largest axis-aligned rectangle in the parking frame that fits the site.

    Tries a uniform bbox inset first (fast path for near-rectangular lots),
    then grows rectangles from interior sample centers so triangular and
    tapered sites still get an orthogonal racetrack with only 90 degree turns.
    """
    min_u, max_u, min_v, max_v = local_bounds(polygon, basis)
    width = max_u - min_u
    height = max_v - min_v
    if width < MIN_RACETRACK_SPAN or height < MIN_RACETRACK_SPAN:
        return None

    best = None
    best_area = 0.0

    # Fast path: uniform inset of the oriented bounds.
    lo = 0.0
    hi = 0.5 * min(width, height)
    for _ in range(28):
        mid = 0.5 * (lo + hi)
        u0 = min_u + mid
        u1 = max_u - mid
        v0 = min_v + mid
        v1 = max_v - mid
        if u1 - u0 < MIN_RACETRACK_SPAN or v1 - v0 < MIN_RACETRACK_SPAN:
            hi = mid
            continue
        if rectangle_inside_polygon(polygon, basis, u0, u1, v0, v1, margin):
            best = (u0, u1, v0, v1)
            best_area = (u1 - u0) * (v1 - v0)
            hi = mid
        else:
            lo = mid

    max_half_u = 0.5 * width
    max_half_v = 0.5 * height
    centers = []
    seen = set()

    def add_center(cu, cv):
        key = (round(cu, 1), round(cv, 1))
        if key in seen:
            return
        x, y = to_world(basis, cu, cv)
        if not has_clearance(polygon, x, y, margin):
            return
        seen.add(key)
        centers.append((cu, cv))

    cwx, cwy = polygon_centroid(polygon)
    add_center(*to_local(basis, cwx, cwy))
    for fu in (0.3, 0.4, 0.5, 0.6, 0.7):
        for fv in (0.3, 0.4, 0.5, 0.6, 0.7):
            add_center(min_u + width * fu, min_v + height * fv)

    for cu, cv in centers:
        rect, area = _ortho_rect_from_center(
            polygon, basis, cu, cv, max_half_u, max_half_v, margin,
        )
        if rect and area > best_area:
            best = rect
            best_area = area

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


def aisle_slice_fits(polygon, basis, u0, u1, v_aisle, aisle_depth, clearance):
    """Test a short aisle segment so runs can be extended to the ring."""
    mid_u = 0.5 * (u0 + u1)
    samples = [
        (u0, v_aisle), (u1, v_aisle),
        (u0, v_aisle + aisle_depth), (u1, v_aisle + aisle_depth),
        (mid_u, v_aisle + 0.5 * aisle_depth),
    ]
    for u, v in samples:
        x, y = to_world(basis, u, v)
        if not has_clearance(polygon, x, y, clearance):
            return False
    return True


def extend_aisle_to_ring(polygon, basis, left_u, right_u, v_aisle, aisle_depth, clearance, min_u, max_u):
    """Grow aisle ends until they meet the ring / buildable edge."""
    step = 3.0
    u = left_u
    while u - step >= min_u - 0.001:
        if not aisle_slice_fits(polygon, basis, u - step, u, v_aisle, aisle_depth, clearance):
            break
        u -= step
    new_left = u

    u = right_u
    while u + step <= max_u + 0.001:
        if not aisle_slice_fits(polygon, basis, u, u + step, v_aisle, aisle_depth, clearance):
            break
        u += step
    new_right = u
    return new_left, new_right


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


def run_column_roles(column_count):
    """Mark terminal / interior island columns vs stalls along one bay run.

    Terminal islands sit at both ends so the cross-aisle turn stays clear.
    Interior islands break long stretches (max MAX_STALLS_BETWEEN_ISLANDS).
    Returns None when the run is too short for end-caps plus a usable bay.
    """
    ends = TERMINAL_ISLAND_COLUMNS
    if column_count < ends * 2 + MIN_RUN_COLUMNS:
        return None

    roles = ["stall"] * column_count
    for index in range(ends):
        roles[index] = "terminal"
        roles[column_count - 1 - index] = "terminal"

    stall_run = 0
    for index in range(ends, column_count - ends):
        if stall_run >= MAX_STALLS_BETWEEN_ISLANDS:
            roles[index] = "interior"
            stall_run = 0
        else:
            stall_run += 1

    if roles.count("stall") < MIN_RUN_COLUMNS:
        return None
    return roles


def island_from_local_shape(basis, shape, z):
    """World-space closed curb loop for one stall-stripe landscape island."""
    return [
        (to_world(basis, su, sv)[0], to_world(basis, su, sv)[1], z)
        for su, sv in shape
    ]


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
    islands = []
    run_count = 0
    v_aisle = v + geometry["row_depth"]
    aisle_depth = geometry["aisle"]

    for start, end in runs:
        roles = run_column_roles(end - start)
        if roles is None:
            continue

        # Stall span for aisle extent — islands still sit inside this bay.
        stall_indices = [start + offset for offset, role in enumerate(roles) if role == "stall"]
        if not stall_indices:
            continue
        left_u = columns[stall_indices[0]][0]
        right_u = columns[stall_indices[-1]][0] + geometry["stall_pitch"]
        # Keep aisle covering terminal islands too so the bay still meets the ring.
        bay_left_u = columns[start][0]
        bay_right_u = columns[end - 1][0] + geometry["stall_pitch"]
        aisle_left, aisle_right = extend_aisle_to_ring(
            polygon, basis, bay_left_u, bay_right_u, v_aisle, aisle_depth, clearance, min_u, max_u,
        )

        # A bay is usable when its aisle can reach the ring (after extension).
        connected = (
            touches_ring(polygon, basis, aisle_left, v, depth, clearance)
            or touches_ring(polygon, basis, aisle_right, v, depth, clearance)
            or touches_ring(polygon, basis, left_u, v, depth, clearance)
            or touches_ring(polygon, basis, right_u, v, depth, clearance)
        )
        if not connected:
            continue

        for offset, role in enumerate(roles):
            index = start + offset
            shapes = columns[index][2]
            if role == "stall":
                for shape in shapes:
                    stalls.append(island_from_local_shape(basis, shape, z))
            else:
                # End-cap / interior islands occupy the stall stripe only —
                # the drive aisle stays open for turning at the row end.
                for shape in shapes:
                    islands.append(island_from_local_shape(basis, shape, z))

        aisles.append(rectangle_world(
            basis,
            aisle_left,
            v_aisle,
            aisle_right - aisle_left,
            aisle_depth,
            z,
        ))
        run_count += 1

    return stalls, aisles, islands, run_count


def layout_for_phase(polygon, basis, clearance, z, geometry, u_phase, v_phase):
    min_u, max_u, min_v, max_v = local_bounds(polygon, basis)
    period = geometry["double_module"]
    if period <= 1e-9:
        return None

    stalls = []
    aisles = []
    islands = []
    run_count = 0

    start_v = min_v + (v_phase % period)
    while start_v > min_v + 1e-9:
        start_v -= period

    v = start_v
    while v <= max_v + 0.001:
        if v + geometry["double_module"] <= max_v + 0.001:
            bay_stalls, bay_aisles, bay_islands, bay_runs = place_bay_runs(
                polygon, basis, v, geometry, 2, geometry["double_module"],
                clearance, min_u, max_u, u_phase, z,
            )
            if bay_runs:
                stalls.extend(bay_stalls)
                aisles.extend(bay_aisles)
                islands.extend(bay_islands)
                run_count += bay_runs
                v += geometry["double_module"]
                continue

        if v + geometry["single_module"] <= max_v + 0.001:
            bay_stalls, bay_aisles, bay_islands, bay_runs = place_bay_runs(
                polygon, basis, v, geometry, 1, geometry["single_module"],
                clearance, min_u, max_u, u_phase, z,
            )
            if bay_runs:
                stalls.extend(bay_stalls)
                aisles.extend(bay_aisles)
                islands.extend(bay_islands)
                run_count += bay_runs
                v += geometry["single_module"]
                continue

        v += geometry["row_depth"] if geometry["row_depth"] > 1.0 else 6.0

    if not stalls:
        return None

    return {
        "stalls": stalls,
        "aisles": aisles,
        "islands": islands,
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
    min_u, max_u, min_v, max_v = local_bounds(polygon, basis)
    best = None

    v_phases = [period * step / float(V_PHASE_STEPS) for step in range(V_PHASE_STEPS)]
    # Also flush modules to the far edge of the core so leftover strips shrink.
    span = max_v - min_v
    modules = int(span / period)
    if modules >= 1:
        flush_start = max_v - modules * period
        v_phases.append((flush_start - min_v) % period)
    v_phases.append(0.0)

    u_phases = [pitch * step / float(U_PHASE_STEPS) for step in range(U_PHASE_STEPS)]
    u_phases.append(0.0)

    seen = set()
    for v_phase in v_phases:
        v_key = round(v_phase, 3)
        for u_phase in u_phases:
            key = (v_key, round(u_phase, 3))
            if key in seen:
                continue
            seen.add(key)
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


def point_on_street_frontage(px, py, street_edge, clear=DRIVEWAY_CLEAR):
    """True when a stall sits on the street edge or inside a curb-cut gap."""
    if not street_edge:
        return False
    ax, ay = street_edge["a"]
    bx, by = street_edge["b"]
    if distance_to_segment(px, py, ax, ay, bx, by) > STALL_WIDTH * 0.75:
        return False

    # Keep driveway throats open at the entry / exit stations.
    for station in access_points_on_street_edge(street_edge):
        if math.hypot(px - station[0], py - station[1]) <= clear * 0.5:
            return True
    return False


def stalls_along_world_edge(
    ax, ay, bx, by, extend_x, extend_y, site_polygon, z,
    stall_width=STALL_WIDTH, depth=STALL_STRIPE, occupied=None, min_clearance=0.0,
    street_edge=None, skip_street_edge=False,
):
    """Place a 90 degree stall row along a world-space edge.

    End-cap islands reserve the first/last column(s) for turning clearance.
    """
    occupied = occupied if occupied is not None else []
    edge_length = math.hypot(bx - ax, by - ay)
    min_slots = TERMINAL_ISLAND_COLUMNS * 2 + MIN_RUN_COLUMNS
    if edge_length < stall_width * min_slots:
        return [], occupied, []

    if skip_street_edge and street_edge:
        sa, sb = street_edge["a"], street_edge["b"]
        # Same boundary segment as the designated street frontage.
        if (
            distance_to_segment(ax, ay, sa[0], sa[1], sb[0], sb[1]) < 1.0
            and distance_to_segment(bx, by, sa[0], sa[1], sb[0], sb[1]) < 1.0
        ):
            return [], occupied, []

    dx = (bx - ax) / edge_length
    dy = (by - ay) / edge_length
    extend_len = math.hypot(extend_x, extend_y)
    if extend_len < 1e-9:
        return [], occupied, []
    nx = extend_x / extend_len
    ny = extend_y / extend_len

    count = int((edge_length - 0.001) / stall_width)
    roles = run_column_roles(count)
    if roles is None:
        return [], occupied, []

    margin = (edge_length - count * stall_width) * 0.5
    stalls = []
    islands = []

    for slot in range(count):
        t = margin + slot * stall_width
        base_x = ax + dx * t
        base_y = ay + dy * t
        corners = stall_corners_world(base_x, base_y, dx, dy, nx, ny, stall_width, depth)
        mid_x = sum(c[0] for c in corners) / 4.0
        mid_y = sum(c[1] for c in corners) / 4.0
        role = roles[slot]

        if point_on_street_frontage(mid_x, mid_y, street_edge):
            continue
        if near_acute_corner(mid_x, mid_y, site_polygon):
            continue

        usable = True
        for corner_x, corner_y in corners:
            if not has_clearance(site_polygon, corner_x, corner_y, min_clearance):
                usable = False
                break
            if near_acute_corner(corner_x, corner_y, site_polygon):
                usable = False
                break
        if not usable:
            continue
        if any(convex_overlap(corners, other) for other in occupied):
            continue

        poly = [(x, y, z) for x, y in corners]
        if role != "stall":
            occupied.append(corners)
            islands.append(poly)
            continue

        # Require the 24 ft backout aisle now, against already accepted stalls.
        occupied_stalls = [[(p[0], p[1], z) for p in quad] for quad in occupied]
        if not stall_has_maneuvering_aisle(poly, site_polygon, occupied_stalls):
            continue

        occupied.append(corners)
        stalls.append(poly)

    return stalls, occupied, islands


def perimeter_row(polygon, z, base_offset, placed=None, stall_width=STALL_WIDTH, street_edge=None):
    """Offset-ring helper: stalls backing onto a constant-offset contour."""
    stalls = []
    islands = []
    placed = placed if placed is not None else []
    count = len(polygon)
    street_index = street_edge["index"] if street_edge else None

    for index in range(count):
        # Leave the street frontage open for curb cuts into the ring.
        if street_index is not None and index == street_index:
            continue

        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        edge_length = math.hypot(bx - ax, by - ay)
        min_slots = TERMINAL_ISLAND_COLUMNS * 2 + MIN_RUN_COLUMNS
        if edge_length < stall_width * min_slots:
            continue

        dx = (bx - ax) / edge_length
        dy = (by - ay) / edge_length
        nx, ny = -dy, dx
        mid_x = 0.5 * (ax + bx)
        mid_y = 0.5 * (ay + by)
        if not point_inside(polygon, mid_x + nx, mid_y + ny):
            nx, ny = -nx, -ny

        stall_count = int((edge_length - 0.001) / stall_width)
        roles = run_column_roles(stall_count)
        if roles is None:
            continue
        margin = (edge_length - stall_count * stall_width) * 0.5

        for slot in range(stall_count):
            t = margin + slot * stall_width
            base_x = ax + dx * t + nx * base_offset
            base_y = ay + dy * t + ny * base_offset
            corners = stall_corners_world(
                base_x, base_y, dx, dy, nx, ny, stall_width, STALL_STRIPE,
            )
            mid_x = sum(c[0] for c in corners) / 4.0
            mid_y = sum(c[1] for c in corners) / 4.0
            if near_acute_corner(mid_x, mid_y, polygon):
                continue

            usable = True
            for corner_x, corner_y in corners:
                if not has_clearance(polygon, corner_x, corner_y, base_offset - 0.05):
                    usable = False
                    break
                if near_acute_corner(corner_x, corner_y, polygon):
                    usable = False
                    break
            if not usable:
                continue
            if any(convex_overlap(corners, other) for other in placed):
                continue

            poly = [(x, y, z) for x, y in corners]
            role = roles[slot]
            if role != "stall":
                placed.append(corners)
                islands.append(poly)
                continue

            occupied_stalls = [[(p[0], p[1], z) for p in quad] for quad in placed]
            if not stall_has_maneuvering_aisle(poly, polygon, occupied_stalls):
                continue

            placed.append(corners)
            stalls.append(poly)

    return stalls, placed, islands


def ortho_ring_stalls(basis, site_polygon, z, ru0, ru1, rv0, rv1,
                      stall_width, setback, street_edge=None):
    """Perimeter stalls on the outside of an orthogonal racetrack only."""
    occupied = []
    stalls = []
    islands = []

    outer_edges = [
        ((ru0, rv0), (ru1, rv0), (0.0, -1.0)),
        ((ru1, rv0), (ru1, rv1), (1.0, 0.0)),
        ((ru1, rv1), (ru0, rv1), (0.0, 1.0)),
        ((ru0, rv1), (ru0, rv0), (-1.0, 0.0)),
    ]

    # Leave the ring side nearest the street fully open for entry / exit.
    street_side = None
    if street_edge:
        best = None
        for index, (a, b, _local_out) in enumerate(outer_edges):
            ax, ay = to_world(basis, a[0], a[1])
            bx, by = to_world(basis, b[0], b[1])
            mx, my = 0.5 * (ax + bx), 0.5 * (ay + by)
            dist = distance_to_segment(
                mx, my,
                street_edge["a"][0], street_edge["a"][1],
                street_edge["b"][0], street_edge["b"][1],
            )
            if best is None or dist < best[0]:
                best = (dist, index)
        if best is not None:
            street_side = best[1]

    for index, (a, b, local_out) in enumerate(outer_edges):
        if street_side is not None and index == street_side:
            continue
        ax, ay = to_world(basis, a[0], a[1])
        bx, by = to_world(basis, b[0], b[1])
        out_x = basis["u"][0] * local_out[0] + basis["v"][0] * local_out[1]
        out_y = basis["u"][1] * local_out[0] + basis["v"][1] * local_out[1]
        row, occupied, row_islands = stalls_along_world_edge(
            ax, ay, bx, by, out_x, out_y,
            site_polygon, z, stall_width, STALL_STRIPE, occupied, setback,
            street_edge=street_edge, skip_street_edge=True,
        )
        stalls.extend(row)
        islands.extend(row_islands)

    return stalls, islands


def stall_blocks_street_access(stall, street_edge, clear=DRIVEWAY_CLEAR):
    """True when a stall sits in a street entry / exit throat."""
    if not street_edge:
        return False
    cx = sum(p[0] for p in stall) / 4.0
    cy = sum(p[1] for p in stall) / 4.0
    ax, ay = street_edge["a"]
    bx, by = street_edge["b"]
    # Only stalls near the street frontage can block access.
    if distance_to_segment(cx, cy, ax, ay, bx, by) > STALL_STRIPE + RING_WIDTH:
        return False
    for station in access_points_on_street_edge(street_edge):
        if math.hypot(cx - station[0], cy - station[1]) <= clear:
            return True
    return False


def filter_street_access_stalls(stalls, street_edge):
    if not street_edge:
        return stalls
    return [
        stall for stall in stalls
        if not stall_blocks_street_access(stall, street_edge)
    ]


def compose_candidate(
    ring_stalls, interior, basis, geometry, ring_meta, site_polygon,
    street_edge=None, ring_islands=None,
):
    # Acute drive corners are rejected; obtuse corners are fine.
    if not ring_drive_is_acceptable(
        ring_meta.get("ring_outer_poly"),
        ring_meta.get("ring_inner_poly"),
    ):
        return None

    interior_stalls = interior["stalls"] if interior else []
    combined = list(ring_stalls) + list(interior_stalls)
    driveable = filter_driveable_stalls(combined, site_polygon)
    driveable = filter_street_access_stalls(driveable, street_edge)
    if not driveable:
        return None

    driveable_set = set(id(stall) for stall in driveable)
    perimeter_kept = sum(1 for stall in ring_stalls if id(stall) in driveable_set)
    access_pts = access_points_on_street_edge(street_edge) if street_edge else []
    islands = list(ring_islands or [])
    if interior:
        islands.extend(interior.get("islands") or [])

    return {
        "stalls": driveable,
        "aisles": interior["aisles"] if interior else [],
        "islands": islands,
        "stall_count": len(driveable),
        "run_count": interior["run_count"] if interior else 0,
        "angle": basis["angle"],
        "park_angle": geometry["park_angle"],
        "flow": geometry["flow"],
        "u_phase": interior.get("u_phase", 0.0) if interior else 0.0,
        "v_phase": interior.get("v_phase", 0.0) if interior else 0.0,
        "perimeter_stalls": perimeter_kept,
        "ring_mode": ring_meta["ring_mode"],
        "ring_outer": ring_meta.get("ring_outer", 0.0),
        "ring_inner": ring_meta.get("ring_inner", 0.0),
        "ring_outer_poly": ring_meta.get("ring_outer_poly"),
        "ring_inner_poly": ring_meta.get("ring_inner_poly"),
        "ortho_bonus": 1 if ring_meta["ring_mode"] == "ortho" else 0,
        "long_aisle_bonus": 1 if ring_meta.get("long_aisle") else 0,
        "access_points": access_pts,
        "access_clear": DRIVEWAY_CLEAR,
    }


def candidate_score(candidate):
    """Stall count / site fill first. Do not reward tiny ortho cut-downs."""
    if candidate is None:
        return -1
    return (
        candidate["stall_count"]
        + (2 if candidate.get("long_aisle_bonus") else 0)
    )


def better_candidate(current, challenger):
    if challenger is None:
        return current
    if current is None:
        return challenger
    if candidate_score(challenger) > candidate_score(current):
        return challenger
    return current


def try_ortho_layouts(polygon, basis, z, setback, geometry, stall_width, street_edge=None):
    """Trial orthogonal racetracks: outer stalls on the ring, grid inside."""
    best = None
    rects = ortho_rect_candidates(polygon, basis, setback, stall_width)
    origin = basis["origin"]

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
            core_w = cu1 - cu0
            core_h = cv1 - cv0
            if core_w < MIN_CORE_SPAN or core_h < geometry["single_module"]:
                if not use_outer:
                    continue

            ring_stalls = []
            ring_islands = []
            if use_outer:
                ring_stalls, ring_islands = ortho_ring_stalls(
                    basis, polygon, z,
                    ru0, ru1, rv0, rv1,
                    stall_width, setback, street_edge,
                )

            # Island aisles should run with the long core direction first.
            # Modules stack across the short axis so each aisle is as long as possible.
            if core_w >= core_h:
                pack_angles = [basis["angle"], basis["angle"] + 90.0]
            else:
                pack_angles = [basis["angle"] + 90.0, basis["angle"]]

            for pack_angle in pack_angles:
                pack_basis = make_basis(origin, pack_angle)
                interior = None
                if core_w >= MIN_CORE_SPAN and core_h >= geometry["single_module"]:
                    core_poly = rect_polygon_2d(basis, cu0, cu1, cv0, cv1)
                    interior = layout_for_angle(core_poly, pack_basis, 0.0, z, geometry)

                long_aisle = (
                    (core_w >= core_h and abs((pack_angle - basis["angle"]) % 180.0) < 1.0)
                    or (core_h > core_w and abs((pack_angle - basis["angle"] - 90.0) % 180.0) < 1.0)
                )
                ring_meta = {
                    "ring_mode": "ortho",
                    "ring_outer_poly": rect_world_polygon(basis, ru0, ru1, rv0, rv1, z),
                    "ring_inner_poly": rect_world_polygon(basis, iu0, iu1, iv0, iv1, z),
                    "ring_outer": 0.0,
                    "ring_inner": RING_WIDTH,
                    "long_aisle": long_aisle,
                }
                candidate = compose_candidate(
                    ring_stalls, interior, pack_basis, geometry, ring_meta, polygon,
                    street_edge, ring_islands=ring_islands,
                )
                best = better_candidate(best, candidate)

    return best


def build_offset_variants(polygon, z, setback, stall_width=STALL_WIDTH, street_edge=None):
    """Site-following ring: outer stalls only, then grid inside the ring."""
    outer_row, _placed, outer_islands = perimeter_row(
        polygon, z, setback, None, stall_width, street_edge=street_edge,
    )
    ring_outer = setback + STALL_STRIPE

    variants = []
    if outer_row:
        # Clearance = inner curb of the ring; interior grid fills from there in.
        variants.append((outer_row, outer_islands, ring_outer, ring_outer + RING_WIDTH))
    variants.append(([], [], setback, setback + RING_WIDTH))
    return variants


def try_offset_layouts(polygon, basis, z, setback, geometry, stall_width, street_edge=None):
    best = None
    origin = basis["origin"]
    min_u, max_u, min_v, max_v = local_bounds(polygon, basis)
    span_u = max_u - min_u
    span_v = max_v - min_v

    for perimeter_stalls, perimeter_islands, ring_outer, clearance in build_offset_variants(
        polygon, z, setback, stall_width, street_edge,
    ):
        # Try both island directions; prefer aisles along the longer site axis.
        if span_u >= span_v:
            pack_angles = [basis["angle"], basis["angle"] + 90.0]
        else:
            pack_angles = [basis["angle"] + 90.0, basis["angle"]]

        for pack_angle in pack_angles:
            pack_basis = make_basis(origin, pack_angle)
            interior = layout_for_angle(polygon, pack_basis, clearance, z, geometry)
            outer_poly, inner_poly = ring_band_points(
                polygon, z, ring_outer, ring_outer + RING_WIDTH,
            )
            long_aisle = (
                (span_u >= span_v and abs((pack_angle - basis["angle"]) % 180.0) < 1.0)
                or (span_v > span_u and abs((pack_angle - basis["angle"] - 90.0) % 180.0) < 1.0)
            )
            ring_meta = {
                "ring_mode": "offset",
                "ring_outer": ring_outer,
                "ring_inner": ring_outer + RING_WIDTH,
                "ring_outer_poly": outer_poly,
                "ring_inner_poly": inner_poly,
                "long_aisle": long_aisle,
            }
            candidate = compose_candidate(
                perimeter_stalls, interior, pack_basis, geometry, ring_meta, polygon,
                street_edge, ring_islands=perimeter_islands,
            )
            best = better_candidate(best, candidate)
    return best


def _search_layouts(polygon, z, setback, access_points, stall_width, park_configs, street_edge=None):
    """Trial-and-error: site-following ring first, ortho only as a fallback fill."""
    origin = polygon_centroid(polygon)
    orientations = candidate_orientations(polygon, access_points, street_edge)
    geometries = [module_geometry(angle, flow, stall_width) for angle, flow in park_configs]
    best = None

    for angle in orientations:
        basis = make_basis(origin, angle)
        for geometry in geometries:
            # Prefer rings that follow the parcel so the site is not over-cut.
            offset = try_offset_layouts(
                polygon, basis, z, setback, geometry, stall_width, street_edge,
            )
            best = better_candidate(best, offset)

            ortho = try_ortho_layouts(
                polygon, basis, z, setback, geometry, stall_width, street_edge,
            )
            best = better_candidate(best, ortho)

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
    """Return the ring drive as (outer, inner) closed point lists.

    Acute tips are chamfered so the drive never asks for a sub-90 turn, while
    still following the site instead of collapsing to a tiny rectangle.
    """
    outer = offset_polygon(polygon, outer_distance)
    inner = offset_polygon(polygon, inner_distance)
    if not outer or not inner:
        return None, None

    outer = chamfer_acute_corners(outer)
    inner = chamfer_acute_corners(inner)
    if len(outer) < 3 or len(inner) < 3:
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


def _poly_xy(points):
    return [(p[0], p[1]) for p in points]


def _closed_xyz(points_xy, z):
    if not points_xy:
        return None
    pts = [(p[0], p[1], z) for p in points_xy]
    if pts[0][:2] != pts[-1][:2]:
        pts.append(pts[0])
    return pts


def _point_in_any(x, y, polys):
    for poly in polys:
        if len(poly) >= 3 and point_inside(poly, x, y):
            return True
    return False


def _segment_near_street_gap(ax, ay, bx, by, street_edge, gap=DRIVEWAY_CLEAR):
    """True if a curb segment crosses a street driveway throat."""
    if not street_edge:
        return False
    for station in access_points_on_street_edge(street_edge):
        sx, sy = station[0], station[1]
        if distance_to_segment(sx, sy, ax, ay, bx, by) <= gap * 0.55:
            return True
        mid_x = 0.5 * (ax + bx)
        mid_y = 0.5 * (ay + by)
        if math.hypot(mid_x - sx, mid_y - sy) <= gap * 0.55:
            return True
    return False


def split_closed_curb_at_street(polygon_xy, z, street_edge):
    """Turn a closed curb into open runs, leaving gaps at street curb cuts."""
    if not polygon_xy or len(polygon_xy) < 3:
        return []
    if not street_edge:
        closed = _closed_xyz(polygon_xy, z)
        return [closed] if closed else []

    count = len(polygon_xy)
    runs = []
    current = []
    for index in range(count):
        a = polygon_xy[index]
        b = polygon_xy[(index + 1) % count]
        if _segment_near_street_gap(a[0], a[1], b[0], b[1], street_edge):
            if len(current) >= 2:
                runs.append([(p[0], p[1], z) for p in current])
            current = []
            continue
        if not current:
            current.append(a)
        current.append(b)
    if len(current) >= 2:
        runs.append([(p[0], p[1], z) for p in current])
    return runs


def stall_back_edge_xy(stall, site_polygon):
    """Return the stall's back edge (non-aisle side) as two XY points."""
    pts = _poly_xy(stall)
    if len(pts) < 4:
        return None
    cx = sum(p[0] for p in pts) / 4.0
    cy = sum(p[1] for p in pts) / 4.0
    site_c = polygon_centroid(site_polygon)

    best = None
    for index in range(4):
        e0 = pts[index]
        e1 = pts[(index + 1) % 4]
        edge_len = math.hypot(e1[0] - e0[0], e1[1] - e0[1])
        # Back/front edges are the stall-width sides (~9 ft), not the 18 ft depth.
        if edge_len < STALL_WIDTH * 0.6 or edge_len > STALL_WIDTH * 1.4:
            continue
        mx = 0.5 * (e0[0] + e1[0])
        my = 0.5 * (e0[1] + e1[1])
        # Prefer the short edge farther from the site centroid (backs to landscape).
        dist = math.hypot(mx - site_c[0], my - site_c[1])
        # And whose outward normal points away from centroid.
        dx = e1[0] - e0[0]
        dy = e1[1] - e0[1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        if (mx - cx) * nx + (my - cy) * ny < 0:
            nx, ny = -nx, -ny
        away = (mx - site_c[0]) * nx + (my - site_c[1]) * ny
        score = dist + (5.0 if away > 0 else 0.0)
        if best is None or score > best[0]:
            best = (score, e0, e1)
    if best is None:
        return None
    return best[1], best[2]


def chain_collinear_segments(segments, join_tol=0.75, angle_tol_deg=8.0):
    """Join nearly collinear stall-back segments into longer curb runs."""
    unused = list(segments)
    runs = []

    def close(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1]) <= join_tol

    while unused:
        a, b = unused.pop()
        run = [a, b]
        grew = True
        while grew:
            grew = False
            index = 0
            while index < len(unused):
                c, d = unused[index]
                head, tail = run[0], run[-1]
                attached = None
                if close(tail, c):
                    attached = d
                elif close(tail, d):
                    attached = c
                elif close(head, c):
                    run = list(reversed(run))
                    attached = d
                elif close(head, d):
                    run = list(reversed(run))
                    attached = c
                if attached is None:
                    index += 1
                    continue

                if len(run) >= 2:
                    ux = run[-1][0] - run[-2][0]
                    uy = run[-1][1] - run[-2][1]
                    vx = attached[0] - run[-1][0]
                    vy = attached[1] - run[-1][1]
                    ul = math.hypot(ux, uy) or 1.0
                    vl = math.hypot(vx, vy) or 1.0
                    dot = max(-1.0, min(1.0, (ux * vx + uy * vy) / (ul * vl)))
                    ang = math.degrees(math.acos(dot))
                    if ang > angle_tol_deg and ang < 180.0 - angle_tol_deg:
                        index += 1
                        continue

                run.append(attached)
                unused.pop(index)
                grew = True
                break
        if len(run) >= 2:
            runs.append(run)
    return runs


def stall_back_curbs(stalls, site_polygon, z):
    segments = []
    for stall in stalls:
        edge = stall_back_edge_xy(stall, site_polygon)
        if edge:
            segments.append(edge)
    runs = chain_collinear_segments(segments)
    return [[(p[0], p[1], z) for p in run] for run in runs]


def interior_landscape_island_curbs(site_polygon, z, layout, cell=9.0):
    """Outline non-drive / non-stall pockets inside the ring as curb islands."""
    _outer, inner = layout_ring_polylines(layout, site_polygon, z)
    if not inner:
        return []

    inner_2d = as_xy_polygon(inner)
    if len(inner_2d) < 3:
        return []

    drive_polys = [as_xy_polygon(aisle) for aisle in layout.get("aisles", [])]
    stall_polys = [as_xy_polygon(stall) for stall in layout.get("stalls", [])]
    # Explicit terminal / interior islands already have curbs; skip leftovers there.
    island_polys = [as_xy_polygon(island) for island in layout.get("islands", [])]

    min_x = min(p[0] for p in inner_2d)
    max_x = max(p[0] for p in inner_2d)
    min_y = min(p[1] for p in inner_2d)
    max_y = max(p[1] for p in inner_2d)
    if max_x - min_x < cell * 2 or max_y - min_y < cell * 2:
        return []

    cols = int(math.ceil((max_x - min_x) / cell))
    rows = int(math.ceil((max_y - min_y) / cell))
    empty = [[False] * cols for _ in range(rows)]

    for row in range(rows):
        for col in range(cols):
            x = min_x + (col + 0.5) * cell
            y = min_y + (row + 0.5) * cell
            if not point_inside(inner_2d, x, y):
                continue
            if not point_inside(site_polygon, x, y):
                continue
            if (
                _point_in_any(x, y, drive_polys)
                or _point_in_any(x, y, stall_polys)
                or _point_in_any(x, y, island_polys)
            ):
                continue
            empty[row][col] = True

    seen = [[False] * cols for _ in range(rows)]
    islands = []

    for row in range(rows):
        for col in range(cols):
            if not empty[row][col] or seen[row][col]:
                continue
            stack = [(row, col)]
            seen[row][col] = True
            cells = []
            while stack:
                cr, cc = stack.pop()
                cells.append((cr, cc))
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nr, nc = cr + dr, cc + dc
                    if nr < 0 or nc < 0 or nr >= rows or nc >= cols:
                        continue
                    if seen[nr][nc] or not empty[nr][nc]:
                        continue
                    seen[nr][nc] = True
                    stack.append((nr, nc))

            # Ignore tiny leftover slivers; keep real landscape islands.
            if len(cells) < 4:
                continue
            xs = [min_x + (c + 0.5) * cell for _, c in cells]
            ys = [min_y + (r + 0.5) * cell for r, _ in cells]
            pad = cell * 0.5
            u0, u1 = min(xs) - pad, max(xs) + pad
            v0, v1 = min(ys) - pad, max(ys) + pad
            if u1 - u0 < STALL_WIDTH or v1 - v0 < STALL_WIDTH:
                continue
            islands.append(_closed_xyz([(u0, v0), (u1, v0), (u1, v1), (u0, v1)], z))

    return [island for island in islands if island]


def acute_corner_pocket_curbs(site_polygon, z, keep_out=ACUTE_KEEP_OUT):
    """Small curb loops in sharp tips where stalls/aisles are banned."""
    curbs = []
    count = len(site_polygon)
    for index, _angle, (vx, vy) in acute_vertices(site_polygon):
        r = min(keep_out * 0.45, 18.0)
        if r < 6.0 or not point_inside(site_polygon, vx, vy):
            continue
        # Move slightly inward from the vertex along the angle bisector.
        prev = site_polygon[(index - 1) % count]
        nxt = site_polygon[(index + 1) % count]
        bax, bay = prev[0] - vx, prev[1] - vy
        bcx, bcy = nxt[0] - vx, nxt[1] - vy
        bal = math.hypot(bax, bay) or 1.0
        bcl = math.hypot(bcx, bcy) or 1.0
        ix = bax / bal + bcx / bcl
        iy = bay / bal + bcy / bcl
        il = math.hypot(ix, iy) or 1.0
        cx = vx + ix / il * r
        cy = vy + iy / il * r
        if not point_inside(site_polygon, cx, cy):
            continue
        pocket = [
            (cx - r * 0.7, cy - r * 0.7),
            (cx + r * 0.7, cy - r * 0.7),
            (cx + r * 0.7, cy + r * 0.7),
            (cx - r * 0.7, cy + r * 0.7),
        ]
        curbs.append(_closed_xyz(pocket, z))
    return curbs


def build_curb_polylines(site_polygon, z, layout, setback, street_edge=None):
    """Build curb curves along regions cars do not drive through.

    Includes:
    - setback / perimeter landscape curb (gapped at street entries)
    - ring faces (drive aisle curb lines)
    - chained stall-back curbs
    - terminal / interior end-of-row landscape islands
    - interior non-drive landscape islands inside the ring
    - acute-corner keep-out pockets
    """
    curbs = []

    setback_poly = offset_polygon(site_polygon, max(setback, 0.0))
    if setback_poly:
        curbs.extend(split_closed_curb_at_street(setback_poly, z, street_edge))

    outer, inner = layout_ring_polylines(layout, site_polygon, z)
    if outer:
        outer_xy = as_xy_polygon(outer)
        # Ring outer is the curb between perimeter field and the drive loop.
        curbs.extend(split_closed_curb_at_street(outer_xy, z, street_edge))
    if inner:
        inner_closed = _closed_xyz(as_xy_polygon(inner), z)
        if inner_closed:
            curbs.append(inner_closed)

    curbs.extend(stall_back_curbs(layout.get("stalls", []), site_polygon, z))
    for island in layout.get("islands", []):
        closed = _closed_xyz(as_xy_polygon(island), z)
        if closed:
            curbs.append(closed)
    curbs.extend(interior_landscape_island_curbs(site_polygon, z, layout))
    curbs.extend(acute_corner_pocket_curbs(site_polygon, z))

    # Drop degenerate runs.
    cleaned = []
    for curb in curbs:
        if not curb or len(curb) < 2:
            continue
        length = 0.0
        for index in range(len(curb) - 1):
            length += math.hypot(curb[index + 1][0] - curb[index][0], curb[index + 1][1] - curb[index][1])
        if length >= STALL_WIDTH:
            cleaned.append(curb)
    return cleaned
