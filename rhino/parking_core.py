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
5. Keep only stalls that have a clear **24 ft** maneuvering aisle in front (SUDAS / ULI
   90 degree rule). Reject stalls in sharp tips, and reject any stall that cannot
   reach the chamfered ring — the tip between the site corner and the ring curb
   stays empty (manual dead zone).
6. Circulation may follow the site. Obtuse aisle corners are fine; only
   acute drive corners (< 90 deg) are forbidden. Sharp tips on an offset
   ring are chamfered so the drive stays driveable without cutting the
   whole site down to a tiny rectangle.
7. Leave clear entry/exit openings on the street frontage — no stalls in
   the driveway throats. Driveway throats project straight inward from the
   street.
8. Reserve terminal (end-cap) landscape islands at both ends of every
   parking row so the turn into the cross aisle stays clear. Long runs
   also get interior islands so no more than ten stalls sit in a row
   without a break.
9. Keep the candidate with the most driveable stalls. Street-aligned grids
   and long-axis island aisles break ties.

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
# Interior angles at or below a right angle cannot host 90 degree stalls.
# Must match MIN_DRIVE_CORNER_DEG so chamfered tips and stall bans agree.
ACUTE_CORNER_DEG = 90.0
# Keep stalls this far from a sharp tip (stall depth + aisle throat).
ACUTE_KEEP_OUT = STALL_STRIPE + AISLE_WIDTH * 0.5
# Drive aisles / ring loops must not ask for a turn sharper than a right angle.
MIN_DRIVE_CORNER_DEG = 90.0
# End-cap / terminal islands replace the last stall column(s) at each row
# end so cars can turn at the aisle intersection. Width tracks the stall
# pitch (codes often cite 6x6 min or ~11 ft landscape islands).
TERMINAL_ISLAND_COLUMNS = 1
# Maximum consecutive stalls between landscape islands in a run.
MAX_STALLS_BETWEEN_ISLANDS = 10
# Manual drawing standard for terminal islands and curb returns.
CURB_FILLET_RADIUS = 5.0
# Two-way 24 ft ring corners need a real turning radius. R5 island noses are
# fine at stall ends, but a passenger car circulating the ring cannot clear a
# sharp 90 deg curb. Prefer ~15 ft on the drive loop itself.
RING_CORNER_RADIUS = 15.0
# Bay islands are drawn with a generous end return, like the manual plan:
# a 36 ft back-to-back island reads as a capsule, and an uneven nose
# tapers instead of showing a raw orthogonal step.
BAY_FILLET_RADIUS = 9.0
FILLET_ARC_SEGMENTS = 8

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


def chamfer_acute_corners(polygon, min_corner_deg=MIN_DRIVE_CORNER_DEG,
                          max_passes=10, right_angle_side=1):
    """Replace each acute tip with an asymmetric right-angle chamfer.

    Equal cuts make two merely-obtuse corners and discard more parking field.
    An asymmetric cut makes one new corner exactly 90 degrees and the other
    obtuse. ``right_angle_side`` chooses which incident edge gets the square
    turn; callers can test both and retain the layout with more final stalls.
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

            # A 30 ft far cut at a ~53 degree tip gives a ~24 ft chamfer face.
            # The corner is already wider than the nominal 24 ft aisle, so do
            # not sacrifice stalls with the previous 60 ft symmetric cut.
            angle = interior_angle_deg(poly, index)
            target = max(RING_WIDTH, STALL_STRIPE * 0.75)
            if angle < 60.0:
                target = max(target, RING_WIDTH * 1.25)

            cosine = max(0.05, math.cos(math.radians(angle)))
            if right_angle_side >= 0:
                # Far cut on next edge; cut_prev = cut_next*cos(angle)
                # makes the chamfer perpendicular to the previous edge.
                far = min(
                    target,
                    d_next * 0.85,
                    d_prev * 0.85 / cosine,
                )
                cut_prev = far * cosine
                cut_next = far
            else:
                # Mirrored candidate: square turn on the next edge.
                far = min(
                    target,
                    d_prev * 0.85,
                    d_next * 0.85 / cosine,
                )
                cut_prev = far
                cut_next = far * cosine
            if min(cut_prev, cut_next) < 1.0:
                new_poly.append(curr)
                continue

            p1 = (
                curr[0] + (prev[0] - curr[0]) * (cut_prev / d_prev),
                curr[1] + (prev[1] - curr[1]) * (cut_prev / d_prev),
            )
            p2 = (
                curr[0] + (nxt[0] - curr[0]) * (cut_next / d_next),
                curr[1] + (nxt[1] - curr[1]) * (cut_next / d_next),
            )
            new_poly.append(p1)
            new_poly.append(p2)

        if len(new_poly) < 3:
            return poly
        poly = new_poly

    return poly


def ring_drive_is_acceptable(ring_outer_poly, ring_inner_poly=None):
    """Validate the path a driver follows, not just the two curb polygons.

    The centreline must have no acute corner, and every edge must be long
    enough to seat the requested R15 curb-return tangencies at both ends.
    This prevents two individually-valid curb offsets from forming a short
    V-shaped compound turn that a car cannot actually negotiate.
    """
    if not ring_outer_poly:
        return False
    if drive_path_has_sharp_turn(ring_outer_poly):
        return False
    if ring_inner_poly and drive_path_has_sharp_turn(ring_inner_poly):
        return False
    outer = as_xy_polygon(ring_outer_poly)
    centerline = offset_polygon_edges(
        outer, [RING_WIDTH * 0.5] * len(outer),
    )
    if not centerline or len(centerline) < 3:
        return False
    if drive_path_has_sharp_turn(centerline):
        return False

    # A fillet of radius R consumes R/tan(interior/2) from each adjacent
    # straight. Two consecutive turns must fit on their shared edge.
    count = len(centerline)
    tangencies = []
    for index in range(count):
        angle = interior_angle_deg(centerline, index)
        if angle < MIN_DRIVE_CORNER_DEG - 0.5:
            return False
        if angle >= 175.0:
            tangencies.append(0.0)
            continue
        half = math.radians(angle * 0.5)
        tangent = RING_CORNER_RADIUS / max(math.tan(half), 1e-6)
        tangencies.append(tangent)
    for index in range(count):
        ax, ay = centerline[index]
        bx, by = centerline[(index + 1) % count]
        length = math.hypot(bx - ax, by - ay)
        required = tangencies[index] + tangencies[(index + 1) % count]
        if length + 0.01 < required:
            return False
    return True


def tip_clearance_depth(angle_deg):
    """Distance from a tip before stall + ring can fit in the wedge.

    Matches the manual dead zone: leave the whole unreachable tip empty,
    not just a small circle around the vertex.
    """
    clamped = max(min(angle_deg, 89.0), 5.0)
    half = math.radians(clamped * 0.5)
    # Stall row + ring + stall row: narrower tips need a deeper empty pocket.
    needed = 2.0 * STALL_STRIPE + RING_WIDTH
    depth = needed / (2.0 * math.tan(half))
    return max(ACUTE_KEEP_OUT, min(depth, 180.0))


def tip_keepout_triangles(polygon, cut=None, threshold_deg=None, ring_outer_poly=None):
    """Triangles cut off sharp tips — no stalls may sit inside these.

    When a chamfered ring is available, deepen each triangle to the ring curb
    so the whole tip dead zone stays empty (manual layout behavior).
    """
    if threshold_deg is None:
        threshold_deg = MIN_DRIVE_CORNER_DEG
    outer = as_xy_polygon(ring_outer_poly) if ring_outer_poly else None
    tris = []
    count = len(polygon)
    for index in range(count):
        angle = interior_angle_deg(polygon, index)
        if angle >= threshold_deg - 0.5:
            continue
        curr = polygon[index]
        prev = polygon[(index - 1) % count]
        nxt = polygon[(index + 1) % count]
        d_prev = math.hypot(curr[0] - prev[0], curr[1] - prev[1])
        d_next = math.hypot(nxt[0] - curr[0], nxt[1] - curr[1])
        if d_prev < 2.0 or d_next < 2.0:
            continue
        # Cut deep enough that the remaining wedge can hold a stall + ring.
        depth = tip_clearance_depth(angle) if cut is None else cut
        if outer and len(outer) >= 3:
            # Clear from the tip all the way to the chamfered ring face.
            depth = max(depth, distance_to_polygon(outer, curr[0], curr[1]) - 1.0)
        cut_len = min(depth, d_prev * 0.9, d_next * 0.9)
        if cut_len < STALL_WIDTH:
            continue
        p1 = (
            curr[0] + (prev[0] - curr[0]) * (cut_len / d_prev),
            curr[1] + (prev[1] - curr[1]) * (cut_len / d_prev),
        )
        p2 = (
            curr[0] + (nxt[0] - curr[0]) * (cut_len / d_next),
            curr[1] + (nxt[1] - curr[1]) * (cut_len / d_next),
        )
        tris.append([curr, p1, p2])
    return tris


def near_acute_corner(x, y, polygon, keep_out=ACUTE_KEEP_OUT, ring_outer_poly=None):
    """True when a point sits in a tip that cars cannot serve."""
    for tri in tip_keepout_triangles(polygon, ring_outer_poly=ring_outer_poly):
        if point_inside(tri, x, y):
            return True
    for _index, angle, (vx, vy) in acute_vertices(polygon, MIN_DRIVE_CORNER_DEG):
        radius = max(keep_out, tip_clearance_depth(angle) * 0.5)
        if ring_outer_poly:
            outer = as_xy_polygon(ring_outer_poly)
            if len(outer) >= 3:
                radius = max(radius, distance_to_polygon(outer, vx, vy) - 1.0)
        if math.hypot(x - vx, y - vy) <= radius:
            return True
    return False


def stall_in_acute_tip(stall, site_polygon, keep_out=ACUTE_KEEP_OUT, ring_outer_poly=None):
    """Reject a stall if its center or any corner sits in a sharp tip."""
    samples = [
        (
            sum(p[0] for p in stall) / 4.0,
            sum(p[1] for p in stall) / 4.0,
        )
    ]
    samples.extend((p[0], p[1]) for p in stall)
    return any(
        near_acute_corner(x, y, site_polygon, keep_out, ring_outer_poly)
        for x, y in samples
    )


def stall_reachable_from_ring(stall, ring_outer_poly, ring_inner_poly=None, reach=None):
    """True when this stall can enter the chamfered ring drive.

    Perimeter stalls in a chamfered tip often have 24 ft of empty space in
    front, but that space is a dead zone — the ring was cut away. Those
    stalls must be removed, matching the manual empty tip.
    """
    if reach is None:
        reach = STALL_STRIPE + 4.0
    if not ring_outer_poly:
        return True

    outer = as_xy_polygon(ring_outer_poly)
    if len(outer) < 3:
        return True

    cx = sum(p[0] for p in stall) / 4.0
    cy = sum(p[1] for p in stall) / 4.0

    if ring_inner_poly:
        inner = as_xy_polygon(ring_inner_poly)
        if len(inner) >= 3 and point_inside(inner, cx, cy):
            return True

    # Inside the outer ring (drive band or core) is always served.
    if point_inside(outer, cx, cy):
        return True

    # Outside the ring: only the perimeter stall band hugging the curb is OK.
    # Tip leftovers sit far from the chamfer cut and fail this test.
    if distance_to_polygon(outer, cx, cy) > reach:
        return False

    for px, py in ((p[0], p[1]) for p in stall):
        if point_inside(outer, px, py):
            continue
        if distance_to_polygon(outer, px, py) > reach + STALL_WIDTH:
            return False
    return True


def filter_ring_served_stalls(stalls, ring_outer_poly, ring_inner_poly=None):
    if not ring_outer_poly:
        return list(stalls)
    return [
        stall for stall in stalls
        if stall_reachable_from_ring(stall, ring_outer_poly, ring_inner_poly)
    ]


def filter_ring_served_islands(islands, ring_outer_poly, ring_inner_poly=None):
    """Drop end-cap islands that landed in the unreachable tip dead zone."""
    if not ring_outer_poly or not islands:
        return list(islands or [])
    kept = []
    for island in islands:
        if stall_reachable_from_ring(island, ring_outer_poly, ring_inner_poly):
            kept.append(island)
    return kept


def point_in_stall_xy(x, y, stall):
    polygon = [(p[0], p[1]) for p in stall]
    # Touching the opposing stall's front stripe at exactly 24 ft is not an
    # obstruction. point_inside is asymmetric on polygon boundaries, which
    # previously deleted one side of every double-loaded row.
    return (
        point_inside(polygon, x, y)
        and distance_to_polygon(polygon, x, y) > 1e-6
    )


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


def filter_driveable_stalls(stalls, site_polygon, ring_outer_poly=None, ring_inner_poly=None):
    """Drop stalls in acute tips or without a 24 ft clear aisle in front."""
    remaining = list(stalls)
    changed = True
    # Iterate: removing one stall can free aisle space for others, and also
    # reveal that a neighbor was only "clear" because we were wrong about tips.
    while changed:
        changed = False
        kept = []
        for stall in remaining:
            if stall_in_acute_tip(stall, site_polygon, ring_outer_poly=ring_outer_poly):
                changed = True
                continue
            if not stall_reachable_from_ring(stall, ring_outer_poly, ring_inner_poly):
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


def street_inward_normal(street_edge, site_polygon):
    """Unit normal of the street edge pointing into the site."""
    ax, ay = street_edge["a"]
    bx, by = street_edge["b"]
    length = math.hypot(bx - ax, by - ay) or 1.0
    dx, dy = (bx - ax) / length, (by - ay) / length
    nx, ny = -dy, dx
    mx = 0.5 * (ax + bx)
    my = 0.5 * (ay + by)
    if not point_inside(site_polygon, mx + nx * 2.0, my + ny * 2.0):
        nx, ny = -nx, -ny
    return nx, ny


def _segment_ray_hit(ox, oy, dx, dy, ax, ay, bx, by):
    """Ray (ox,oy)+t(dx,dy), t>=0, vs segment ab. Returns t or None."""
    ex, ey = bx - ax, by - ay
    denom = dx * ey - dy * ex
    if abs(denom) < 1e-12:
        return None
    sx, sy = ax - ox, ay - oy
    t = (sx * ey - sy * ex) / denom
    u = (sx * dy - sy * dx) / denom
    if t < 0.05 or u < -1e-9 or u > 1.0 + 1e-9:
        return None
    return t


def driveway_throat_target(access_point, street_edge, site_polygon, ring_outer, ring_inner=None):
    """Project street access straight inward onto the ring centerline.

    Avoids diagonal slashes caused by snapping to the nearest ring vertex.
    """
    if not street_edge or not ring_outer:
        return None

    ax, ay = access_point[0], access_point[1]
    nx, ny = street_inward_normal(street_edge, site_polygon)
    outer = as_xy_polygon(ring_outer)
    best_t = None
    count = len(outer)
    for index in range(count):
        p0 = outer[index]
        p1 = outer[(index + 1) % count]
        hit = _segment_ray_hit(ax, ay, nx, ny, p0[0], p0[1], p1[0], p1[1])
        if hit is None:
            continue
        if best_t is None or hit < best_t:
            best_t = hit

    if best_t is None:
        # Fallback: fixed depth from ring band metadata is handled by caller.
        return None

    # Land in the middle of the ring drive, not on the outer curb.
    depth = RING_WIDTH * 0.5
    if ring_inner:
        inner = as_xy_polygon(ring_inner)
        inner_t = None
        for index in range(len(inner)):
            p0 = inner[index]
            p1 = inner[(index + 1) % len(inner)]
            hit = _segment_ray_hit(ax, ay, nx, ny, p0[0], p0[1], p1[0], p1[1])
            if hit is None:
                continue
            if inner_t is None or hit < inner_t:
                inner_t = hit
        if inner_t is not None and inner_t > best_t:
            depth = 0.5 * (inner_t - best_t)

    t = best_t + depth
    return (ax + nx * t, ay + ny * t)


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


def primary_skeleton_orientations(polygon, street_edge=None):
    """Return the three aisle skeleton directions used for final comparison.

    These correspond to the manual workflow: street perpendicular, street
    parallel, and the dominant non-street site edge. Duplicate directions
    modulo 180 degrees are removed.
    """
    result = []

    def add(angle):
        key = angle_key(angle)
        if all(
            min(
                abs(angle_key(existing) - key),
                180.0 - abs(angle_key(existing) - key),
            ) > 0.5
            for existing in result
        ):
            result.append(key)

    street_index = street_edge.get("index") if street_edge else None
    if street_edge:
        ax, ay = street_edge["a"]
        bx, by = street_edge["b"]
        street_angle = math.degrees(math.atan2(by - ay, bx - ax))
        add(street_angle + 90.0)
        add(street_angle)

    edges = []
    for index in range(len(polygon)):
        if street_index is not None and index == street_index:
            continue
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % len(polygon)]
        length = math.hypot(bx - ax, by - ay)
        if length < 5.0:
            continue
        angle = math.degrees(math.atan2(by - ay, bx - ax))
        edges.append((length, angle))
    edges.sort(reverse=True)
    for _length, angle in edges:
        add(angle)
        if len(result) >= 3:
            break

    for fallback in (0.0, 90.0, 45.0):
        if len(result) >= 3:
            break
        add(fallback)
    return result[:3]


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

        # Keep only the fit interval. Stall polygons are materialized after
        # the aisle skeleton has been clipped and accepted.
        columns.append((u, fits))
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


def build_bay_skeleton(
    polygon, basis, v, geometry, rows, depth, clearance,
    min_u, max_u, u_phase,
):
    """Build and clean one aisle/row skeleton before creating any stalls."""
    columns = bay_columns(polygon, basis, v, geometry, rows, clearance, min_u, max_u, u_phase)
    raw_runs = []
    run_start = None
    for index in range(len(columns) + 1):
        fits = columns[index][1] if index < len(columns) else False
        if fits and run_start is None:
            run_start = index
        elif not fits and run_start is not None:
            raw_runs.append((run_start, index))
            run_start = None

    runs = []
    v_aisle = v + geometry["row_depth"]
    aisle_depth = geometry["aisle"]

    for start, end in raw_runs:
        # Roles are assigned only after the run has been clipped to the
        # inner-ring core. This is the manual "clean row ends" step.
        roles = run_column_roles(end - start)
        if roles is None:
            continue

        stall_indices = [start + offset for offset, role in enumerate(roles) if role == "stall"]
        if not stall_indices:
            continue
        left_u = columns[stall_indices[0]][0]
        right_u = columns[stall_indices[-1]][0] + geometry["stall_pitch"]
        bay_left_u = columns[start][0]
        bay_right_u = columns[end - 1][0] + geometry["stall_pitch"]
        aisle_left, aisle_right = extend_aisle_to_ring(
            polygon, basis, bay_left_u, bay_right_u, v_aisle, aisle_depth, clearance, min_u, max_u,
        )

        # Because polygon is the actual chamfered inner-ring polygon, touching
        # its boundary means this aisle really reaches circulation.
        connected = (
            touches_ring(polygon, basis, aisle_left, v, depth, clearance)
            or touches_ring(polygon, basis, aisle_right, v, depth, clearance)
            or touches_ring(polygon, basis, left_u, v, depth, clearance)
            or touches_ring(polygon, basis, right_u, v, depth, clearance)
        )
        if not connected:
            continue

        runs.append({
            "v": v,
            "rows": rows,
            "start": start,
            "end": end,
            "roles": roles,
            "columns": columns,
            "aisle_left": aisle_left,
            "aisle_right": aisle_right,
            "v_aisle": v_aisle,
            "aisle_depth": aisle_depth,
            "stall_count": roles.count("stall") * rows,
        })

    return runs


def materialize_bay_skeleton(runs, basis, geometry, z):
    """Populate stalls/islands only after the aisle skeleton is finalized."""
    stalls = []
    aisles = []
    islands = []
    for run in runs:
        front_v = run["v"] + geometry["row_depth"]
        for offset, role in enumerate(run["roles"]):
            index = run["start"] + offset
            u = run["columns"][index][0]
            shapes = [stall_shape(u, front_v, geometry, -1.0)]
            if run["rows"] == 2:
                shapes.append(stall_shape(
                    u, front_v + geometry["aisle"], geometry, 1.0,
                ))
            target = stalls if role == "stall" else islands
            for shape in shapes:
                target.append(island_from_local_shape(basis, shape, z))

        aisles.append(rectangle_world(
            basis,
            run["aisle_left"],
            run["v_aisle"],
            run["aisle_right"] - run["aisle_left"],
            run["aisle_depth"],
            z,
        ))
    return stalls, aisles, islands


def place_bay_runs(polygon, basis, v, geometry, rows, depth, clearance, min_u, max_u, u_phase, z):
    """Compatibility wrapper: skeleton first, then materialize."""
    runs = build_bay_skeleton(
        polygon, basis, v, geometry, rows, depth, clearance,
        min_u, max_u, u_phase,
    )
    stalls, aisles, islands = materialize_bay_skeleton(runs, basis, geometry, z)
    return stalls, aisles, islands, len(runs)


def layout_for_phase(polygon, basis, clearance, z, geometry, u_phase, v_phase):
    min_u, max_u, min_v, max_v = local_bounds(polygon, basis)
    period = geometry["double_module"]
    if period <= 1e-9:
        return None

    skeleton_runs = []

    start_v = min_v + (v_phase % period)
    while start_v > min_v + 1e-9:
        start_v -= period

    v = start_v
    while v <= max_v + 0.001:
        if v + geometry["double_module"] <= max_v + 0.001:
            bay_skeleton = build_bay_skeleton(
                polygon, basis, v, geometry, 2, geometry["double_module"],
                clearance, min_u, max_u, u_phase,
            )
            if bay_skeleton:
                skeleton_runs.extend(bay_skeleton)
                v += geometry["double_module"]
                continue

        if v + geometry["single_module"] <= max_v + 0.001:
            bay_skeleton = build_bay_skeleton(
                polygon, basis, v, geometry, 1, geometry["single_module"],
                clearance, min_u, max_u, u_phase,
            )
            if bay_skeleton:
                skeleton_runs.extend(bay_skeleton)
                v += geometry["single_module"]
                continue

        v += geometry["row_depth"] if geometry["row_depth"] > 1.0 else 6.0

    if not skeleton_runs:
        return None

    stalls, aisles, islands = materialize_bay_skeleton(
        skeleton_runs, basis, geometry, z,
    )
    return {
        "stalls": stalls,
        "aisles": aisles,
        "islands": islands,
        "stall_count": len(stalls),
        "run_count": len(skeleton_runs),
        "connected_run_count": len(skeleton_runs),
        "aisle_length": sum(
            run["aisle_right"] - run["aisle_left"] for run in skeleton_runs
        ),
        "skeleton_runs": skeleton_runs,
        "angle": basis["angle"],
        "park_angle": geometry["park_angle"],
        "flow": geometry["flow"],
        "u_phase": u_phase,
        "v_phase": v_phase,
    }


def scanline_intervals(polygon, basis, v):
    """Intersect an infinite U-direction line with a polygon in parking UV."""
    local = [to_local(basis, p[0], p[1]) for p in as_xy_polygon(polygon)]
    hits = []
    for index in range(len(local)):
        u0, v0 = local[index]
        u1, v1 = local[(index + 1) % len(local)]
        if abs(v1 - v0) < 1e-9:
            continue
        # Half-open edge rule avoids counting polygon vertices twice.
        if not ((v0 <= v < v1) or (v1 <= v < v0)):
            continue
        t = (v - v0) / (v1 - v0)
        hits.append(u0 + (u1 - u0) * t)
    hits.sort()
    intervals = []
    for index in range(0, len(hits) - 1, 2):
        if hits[index + 1] - hits[index] > 0.5:
            intervals.append((hits[index], hits[index + 1]))
    return intervals


def containing_interval(intervals, u):
    for left, right in intervals:
        if left - 0.01 <= u <= right + 0.01:
            return left, right
    return None


def intersect_interval_sets(left_set, right_set):
    intersections = []
    for left0, right0 in left_set:
        for left1, right1 in right_set:
            left = max(left0, left1)
            right = min(right0, right1)
            if right - left > 0.5:
                intersections.append((left, right))
    intersections.sort()
    return intersections


def strip_common_intervals(polygon, basis, v0, v1):
    """U intervals whose full perpendicular strip stays inside polygon.

    This is a directional erosion: 30 ft across a parking module, zero
    artificial erosion along its aisle. Sampling every polygon vertex within
    the strip captures where tapered/concave boundaries change slope.
    """
    local = [to_local(basis, p[0], p[1]) for p in as_xy_polygon(polygon)]
    samples = [v0, v1, 0.5 * (v0 + v1)]
    for _u, vertex_v in local:
        if v0 + 0.01 < vertex_v < v1 - 0.01:
            samples.extend([vertex_v - 0.01, vertex_v + 0.01])
    samples = sorted(set(round(v, 6) for v in samples))

    common = None
    for sample_v in samples:
        intervals = scanline_intervals(polygon, basis, sample_v)
        if not intervals:
            return []
        common = intervals if common is None else intersect_interval_sets(common, intervals)
        if not common:
            return []
    return common or []


def _cell_inside(region_polygon, basis, u0, u1, v0, v1):
    """True when a UV cell (one stall or one aisle bite) lies inside region."""
    if region_polygon is None:
        return False
    if u1 - u0 < 0.5 or v1 - v0 < 0.5:
        return False
    mid_u = 0.5 * (u0 + u1)
    mid_v = 0.5 * (v0 + v1)
    samples = [
        (u0, v0), (u1, v0), (u1, v1), (u0, v1),
        (mid_u, v0), (mid_u, v1), (u0, mid_v), (u1, mid_v), (mid_u, mid_v),
    ]
    for u, v in samples:
        x, y = to_world(basis, u, v)
        if not point_inside(region_polygon, x, y):
            return False
    return True


def _longest_true_span(flags):
    """Longest contiguous run of True as (start, end) with end exclusive."""
    best = None
    start = None
    for index in range(len(flags) + 1):
        value = flags[index] if index < len(flags) else False
        if value and start is None:
            start = index
        elif not value and start is not None:
            if best is None or index - start > best[1] - best[0]:
                best = (start, index)
            start = None
    return best


def module_column_roles(spans, max_between=MAX_STALLS_BETWEEN_ISLANDS):
    """Assign terminal / interior island columns across a whole bay island.

    Both rows of a back-to-back bay share one column grid, so interior
    islands line up across the island the way they do on a drawn plan.
    Terminal islands cap each row at its own ends, because a tapered parcel
    makes the two rows different lengths.
    """
    if not spans:
        return {}
    start = min(first for first, _last in spans.values())
    end = max(last for _first, last in spans.values())

    interior_columns = set()
    column = start + TERMINAL_ISLAND_COLUMNS + max_between
    while column < end - TERMINAL_ISLAND_COLUMNS:
        interior_columns.add(column)
        column += max_between + 1

    roles = {}
    for sign, (first, last) in spans.items():
        row = []
        for column in range(first, last):
            if (column < first + TERMINAL_ISLAND_COLUMNS
                    or column >= last - TERMINAL_ISLAND_COLUMNS):
                row.append("terminal")
            elif column in interior_columns:
                row.append("interior")
            else:
                row.append("stall")
        roles[sign] = row
    return roles


def _row_v_range(geometry, center_v, sign):
    """The 18 ft stall band on one side of a back-to-back bay island."""
    depth = geometry["row_depth"]
    if sign < 0:
        return (center_v - depth, center_v)
    return (center_v, center_v + depth)


def _aisle_v_range(geometry, center_v, sign):
    """The 24 ft drive aisle serving that stall band."""
    depth = geometry["row_depth"]
    aisle = geometry["aisle"]
    if sign < 0:
        return (center_v - depth - aisle, center_v - depth)
    return (center_v + depth, center_v + depth + aisle)


def _row_fit_flags(core_polygon, drive_polygon, basis, geometry,
                   center_v, sign, u_start, count, site_polygon=None):
    """Per column: does the stall fit AND is there a 24 ft aisle in front?

    Each row is measured on its own. On a tapered parcel the two rows of a
    bay are not the same length, exactly like the manual drawing, so the
    ends step with the site edge instead of both rows being cut back to the
    shortest common rectangle.
    """
    pitch = geometry["stall_pitch"]
    sv0, sv1 = _row_v_range(geometry, center_v, sign)
    av0, av1 = _aisle_v_range(geometry, center_v, sign)
    flags = []
    for index in range(count):
        u0 = u_start + index * pitch
        u1 = u0 + pitch
        ok = (
            _cell_inside(core_polygon, basis, u0, u1, sv0, sv1)
            and _cell_inside(drive_polygon, basis, u0, u1, av0, av1)
        )
        if ok and site_polygon:
            # R3: an acute tip is a dead zone, interior rows included.
            cx, cy = to_world(basis, 0.5 * (u0 + u1), 0.5 * (sv0 + sv1))
            if near_acute_corner(
                cx, cy, site_polygon, ring_outer_poly=drive_polygon,
            ):
                ok = False
        flags.append(ok)
    return flags


def _build_bay_island(core_polygon, drive_polygon, basis, geometry, center_v,
                      u_min, u_max, site_polygon=None):
    """One back-to-back bay island: two rows sharing a spine, aisles outside."""
    pitch = geometry["stall_pitch"]
    if u_max - u_min < pitch * (TERMINAL_ISLAND_COLUMNS * 2 + MIN_RUN_COLUMNS):
        return None
    count = int((u_max - u_min + 0.001) / pitch)
    if count < TERMINAL_ISLAND_COLUMNS * 2 + MIN_RUN_COLUMNS:
        return None
    leftover = (u_max - u_min) - count * pitch

    best = None
    for phase in (0.0, leftover * 0.5, leftover):
        u_start = u_min + phase
        spans = {}
        for sign in (-1.0, 1.0):
            flags = _row_fit_flags(
                core_polygon, drive_polygon, basis, geometry,
                center_v, sign, u_start, count, site_polygon,
            )
            span = _longest_true_span(flags)
            if span is None:
                continue
            if span[1] - span[0] < TERMINAL_ISLAND_COLUMNS * 2 + MIN_RUN_COLUMNS:
                continue
            spans[sign] = span
        # Interior islands are double-loaded by definition.  Accepting one
        # surviving side here recreates the old aisle-centred edge rows: the
        # first and last "islands" become single rows instead of the paired
        # 36 ft bays shown in the manual layout.
        if len(spans) != 2:
            continue

        roles = module_column_roles(spans)
        stall_total = sum(row.count("stall") for row in roles.values())
        if stall_total <= 0:
            continue
        if best is None or stall_total > best[0]:
            best = (stall_total, u_start, spans, roles)

    if best is None:
        return None

    stall_total, u_start, spans, roles = best
    rows = []
    for sign, (first, last) in sorted(spans.items()):
        rows.append({
            "sign": sign,
            "u0": u_start + first * pitch,
            "u1": u_start + last * pitch,
            "roles": roles[sign],
        })

    return {
        "center_v": center_v,
        "u_start": u_start,
        "rows": rows,
        "u0": min(row["u0"] for row in rows),
        "u1": max(row["u1"] for row in rows),
        "stall_count": stall_total,
    }


def build_module_skeleton_phase(core_polygon, basis, geometry, v_phase,
                                drive_polygon=None, site_polygon=None):
    """Lay out bay islands before any stall is drawn.

    A bay island is the 36 ft back-to-back stall pair the manual drawing
    uses. Its 24 ft aisles sit outside it and are shared with the next bay
    or with the perimeter ring drive, which is why the lattice period is
    still 18 + 24 + 18. Stalls must sit inside the parking core; the aisle
    in front of them only has to be drivable, so an outer bay may legally
    be served by the ring instead of by another interior aisle.
    """
    if drive_polygon is None:
        drive_polygon = core_polygon
    depth = geometry["row_depth"]
    period = geometry["double_module"]
    core_min_u, core_max_u, core_min_v, core_max_v = local_bounds(core_polygon, basis)
    min_v = core_min_v + depth
    max_v = core_max_v - depth
    if max_v < min_v - 0.001:
        return None

    center_v = min_v + (v_phase % period)
    while center_v > min_v + 1e-9:
        center_v -= period

    runs = []
    while center_v <= max_v + 0.001:
        if center_v < min_v - 0.001:
            center_v += period
            continue
        run = _build_bay_island(
            core_polygon, drive_polygon, basis, geometry, center_v,
            core_min_u, core_max_u, site_polygon,
        )
        if run:
            runs.append(run)
        center_v += period

    if not runs:
        return None
    return {
        "runs": runs,
        "module_core": None,
        "connected_run_count": len(runs),
        "aisle_length": sum(run["u1"] - run["u0"] for run in runs),
        "stall_count": sum(run["stall_count"] for run in runs),
        "v_phase": v_phase,
    }


def _dedupe_uv(points, tol=0.05):
    cleaned = []
    for u, v in points:
        if cleaned and abs(cleaned[-1][0] - u) < tol and abs(cleaned[-1][1] - v) < tol:
            continue
        cleaned.append((u, v))
    if (len(cleaned) > 1
            and abs(cleaned[0][0] - cleaned[-1][0]) < tol
            and abs(cleaned[0][1] - cleaned[-1][1]) < tol):
        cleaned.pop()
    return cleaned


def bay_envelope_uv(run, geometry):
    """Closed outline of one bay island, stepping where the rows differ."""
    depth = geometry["row_depth"]
    center_v = run["center_v"]
    lower = None
    upper = None
    for row in run["rows"]:
        if row["sign"] < 0:
            lower = row
        else:
            upper = row

    if lower is None or upper is None:
        # Single-loaded bay: the island is only 18 ft deep.
        row = lower or upper
        sign = -1.0 if lower else 1.0
        v0, v1 = _row_v_range(geometry, center_v, sign)
        return _dedupe_uv([
            (row["u0"], v0), (row["u1"], v0),
            (row["u1"], v1), (row["u0"], v1),
        ])

    points = [
        (lower["u0"], center_v - depth),
        (lower["u1"], center_v - depth),
        (lower["u1"], center_v),
        (upper["u1"], center_v),
        (upper["u1"], center_v + depth),
        (upper["u0"], center_v + depth),
        (upper["u0"], center_v),
        (lower["u0"], center_v),
    ]
    return _dedupe_uv(points)


def materialize_module_skeleton(skeleton, basis, geometry, z):
    """Populate stall stripes, end caps and bay outlines around the skeleton."""
    stalls = []
    islands = []
    aisles = []
    envelopes = []
    pitch = geometry["stall_pitch"]

    for run in skeleton["runs"]:
        center_v = run["center_v"]
        island_cells = {}
        for row in run["rows"]:
            # Stalls are struck from the aisle face back to the spine.
            front_v = center_v + row["sign"] * geometry["row_depth"]
            lean_sign = -row["sign"]
            for index, role in enumerate(row["roles"]):
                u = row["u0"] + index * pitch
                shape = stall_shape(u, front_v, geometry, lean_sign)
                if role == "stall":
                    stalls.append(island_from_local_shape(basis, shape, z))
                elif role == "interior":
                    # Rows share an absolute column grid.  Hold landscape
                    # cells until both sides are known so matching divider
                    # cells become one continuous 36 ft island.
                    key = round(u, 6)
                    island_cells.setdefault(key, []).append(
                        (row["sign"], u, shape)
                    )

            av0, av1 = _aisle_v_range(geometry, center_v, row["sign"])
            aisles.append(rect_world_polygon(
                basis, row["u0"], row["u1"], av0, av1, z,
            ))

        depth = geometry["row_depth"]
        for cells in island_cells.values():
            signs = set(cell[0] for cell in cells)
            if -1.0 in signs and 1.0 in signs:
                u = cells[0][1]
                shape = [
                    (u, center_v - depth),
                    (u + pitch, center_v - depth),
                    (u + pitch, center_v + depth),
                    (u, center_v + depth),
                ]
                islands.append(island_from_local_shape(basis, shape, z))
            else:
                islands.append(island_from_local_shape(basis, cells[0][2], z))

        rows_by_sign = dict((row["sign"], row) for row in run["rows"])
        lower = rows_by_sign.get(-1.0)
        upper = rows_by_sign.get(1.0)
        if lower is not None and upper is not None:
            # One stepped cap joins both row ends.  When a tapered parcel
            # makes one row longer, the centre-line connector fills the step
            # and produces the single wedge-shaped terminal island shown in
            # the manual plan.
            terminal_width = TERMINAL_ISLAND_COLUMNS * pitch
            for at_start in (True, False):
                if at_start:
                    lu0, lu1 = lower["u0"], lower["u0"] + terminal_width
                    uu0, uu1 = upper["u0"], upper["u0"] + terminal_width
                else:
                    lu0, lu1 = lower["u1"] - terminal_width, lower["u1"]
                    uu0, uu1 = upper["u1"] - terminal_width, upper["u1"]
                shape = [
                    (lu0, center_v - depth),
                    (lu1, center_v - depth),
                    (lu1, center_v),
                    (uu1, center_v),
                    (uu1, center_v + depth),
                    (uu0, center_v + depth),
                    (uu0, center_v),
                    (lu0, center_v),
                ]
                islands.append(island_from_local_shape(
                    basis, _dedupe_uv(shape), z,
                ))

        envelopes.append(island_from_local_shape(
            basis, bay_envelope_uv(run, geometry), z,
        ))

    return {
        "stalls": stalls,
        "aisles": aisles,
        "bay_envelopes": envelopes,
        "islands": islands,
        "stall_count": len(stalls),
        "run_count": len(skeleton["runs"]),
        "connected_run_count": skeleton["connected_run_count"],
        "aisle_length": skeleton["aisle_length"],
        "skeleton_runs": skeleton["runs"],
        "module_core": skeleton["module_core"],
        "angle": basis["angle"],
        "park_angle": geometry["park_angle"],
        "flow": geometry["flow"],
        "u_phase": 0.0,
        "v_phase": skeleton["v_phase"],
    }


def layout_for_angle(polygon, basis, clearance, z, geometry, drive_polygon=None,
                     site_polygon=None, entrance_points=None):
    # The PDF workflow uses centerline -> full module envelope -> trim ->
    # stalls. Keep the old tile path only for diagonal emergency fallback.
    if geometry["park_angle"] == 90 and clearance <= 0.001:
        period = geometry["double_module"]
        steps = max(V_PHASE_STEPS, 12)
        phases = [period * step / float(steps) for step in range(steps)]
        phases.append(0.0)
        # Entrance alignment (absorbed from the "spine aisle" idea): add phases
        # that seat a bay aisle centered on the entrance, so a car can drive
        # straight in instead of being forced into a turn at the mouth. This is
        # additive — the best-scoring phase still wins, so it never hurts.
        if entrance_points:
            _min_u, _max_u, ent_min_v, _max_v = local_bounds(polygon, basis)
            band_min_v = ent_min_v + geometry["row_depth"]
            aisle_offset = geometry["row_depth"] + geometry["aisle"] * 0.5
            for point in entrance_points:
                _eu, ev = to_local(basis, point[0], point[1])
                for sign in (-1.0, 1.0):
                    target_center = ev + sign * aisle_offset
                    phases.append((target_center - band_min_v) % period)
        best_skeleton = None
        best_rank = None
        seen = set()
        for phase in phases:
            key = round(phase % period, 3)
            if key in seen:
                continue
            seen.add(key)
            skeleton = build_module_skeleton_phase(
                polygon, basis, geometry, phase, drive_polygon, site_polygon,
            )
            if skeleton is None:
                continue
            rank = (
                skeleton["stall_count"],
                skeleton["connected_run_count"],
                skeleton["aisle_length"],
            )
            if best_skeleton is None or rank > best_rank:
                best_skeleton = skeleton
                best_rank = rank
        if best_skeleton:
            return materialize_module_skeleton(
                best_skeleton, basis, geometry, z,
            )
        return None

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
            skeleton_rank = (
                candidate["stall_count"],
                candidate.get("connected_run_count", 0),
                candidate.get("aisle_length", 0.0),
            )
            best_rank = (
                best["stall_count"],
                best.get("connected_run_count", 0),
                best.get("aisle_length", 0.0),
            ) if best else None
            if best is None or skeleton_rank > best_rank:
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

    ring_outer = ring_meta.get("ring_outer_poly")
    ring_inner = ring_meta.get("ring_inner_poly")
    # Perimeter stalls need site/tip/ring QA. Interior stalls were generated
    # as complete paired rows around an explicit 24 ft aisle inside the
    # chamfered core; running the generic edge-probe filter on them previously
    # deleted one side of every double-loaded row.
    perimeter_driveable = filter_driveable_stalls(
        ring_stalls, site_polygon,
        ring_outer_poly=ring_outer, ring_inner_poly=ring_inner,
    )
    perimeter_driveable = filter_street_access_stalls(
        perimeter_driveable, street_edge,
    )
    interior_stalls = list(interior["stalls"]) if interior else []
    driveable = perimeter_driveable + interior_stalls
    if not driveable:
        return None

    perimeter_kept = len(perimeter_driveable)
    access_pts = access_points_on_street_edge(street_edge) if street_edge else []
    perimeter_islands = filter_ring_served_islands(
        list(ring_islands or []), ring_outer, ring_inner,
    )
    perimeter_islands = [
        island for island in perimeter_islands
        if not stall_in_acute_tip(
            island, site_polygon, ring_outer_poly=ring_outer,
        )
    ]
    islands = perimeter_islands
    if interior:
        islands.extend(interior.get("islands") or [])

    pack_angle = basis["angle"]
    street_align = 0
    if street_edge:
        sax, say = street_edge["a"]
        sbx, sby = street_edge["b"]
        street_angle = math.degrees(math.atan2(sby - say, sbx - sax)) % 180.0
        delta = abs((pack_angle % 180.0) - street_angle) % 180.0
        delta = min(delta, 180.0 - delta)
        # 0 or 90 deg to the street is ideal; score the nearest ortho match.
        ortho = min(delta % 90.0, 90.0 - (delta % 90.0))
        if ortho < 1.0:
            street_align = 3
        elif ortho < 5.0:
            street_align = 1

    return {
        "stalls": driveable,
        "aisles": interior["aisles"] if interior else [],
        "bay_envelopes": interior.get("bay_envelopes", []) if interior else [],
        "islands": islands,
        "module_core": interior.get("module_core") if interior else None,
        "skeleton_runs": interior.get("skeleton_runs", []) if interior else [],
        "stall_count": len(driveable),
        "run_count": interior["run_count"] if interior else 0,
        "connected_run_count": interior.get("connected_run_count", 0) if interior else 0,
        "aisle_length": interior.get("aisle_length", 0.0) if interior else 0.0,
        "angle": pack_angle,
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
        "chamfer_side": ring_meta.get("chamfer_side"),
        "ortho_bonus": 1 if ring_meta["ring_mode"] == "ortho" else 0,
        "long_aisle_bonus": 1 if ring_meta.get("long_aisle") else 0,
        "street_align": street_align,
        "access_points": access_pts,
        "access_clear": DRIVEWAY_CLEAR,
    }


def candidate_rank(candidate):
    """Driveability is mandatory; capacity compares the valid options."""
    if candidate is None:
        return None
    return (
        candidate["stall_count"],
        candidate.get("connected_run_count", 0),
        candidate.get("aisle_length", 0.0),
        1 if candidate.get("long_aisle_bonus") else 0,
        candidate.get("street_align", 0),
        1 if candidate.get("ring_mode") == "offset" else 0,
    )


def better_candidate(current, challenger):
    if challenger is None:
        return current
    if current is None:
        return challenger
    if candidate_rank(challenger) > candidate_rank(current):
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
                    drive_poly = rect_polygon_2d(basis, ru0, ru1, rv0, rv1)
                    interior = layout_for_angle(
                        core_poly, pack_basis, 0.0, z, geometry, drive_poly,
                        polygon,
                    )

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

    street_index = street_edge["index"] if street_edge else None
    perimeter_edges = [
        setback if index == street_index else ring_outer
        for index in range(len(polygon))
    ]
    bare_edges = [setback] * len(polygon)

    variants = []
    if outer_row:
        # Clearance = inner curb of the ring; interior grid fills from there in.
        variants.append({
            "stalls": outer_row,
            "islands": outer_islands,
            "ring_outer": ring_outer,
            "edge_outer": perimeter_edges,
        })
    variants.append({
        "stalls": [],
        "islands": [],
        "ring_outer": setback,
        "edge_outer": bare_edges,
    })
    return variants


def try_offset_layouts(polygon, basis, z, setback, geometry, stall_width, street_edge=None):
    best = None
    origin = basis["origin"]
    min_u, max_u, min_v, max_v = local_bounds(polygon, basis)
    span_u = max_u - min_u
    span_v = max_v - min_v
    chamfer_sides = (
        (-1, 1)
        if acute_vertices(polygon, MIN_DRIVE_CORNER_DEG)
        else (1,)
    )

    for variant in build_offset_variants(
        polygon, z, setback, stall_width, street_edge,
    ):
        perimeter_stalls = variant["stalls"]
        perimeter_islands = variant["islands"]
        ring_outer = variant["ring_outer"]
        # An acute tip has two valid asymmetric chamfers. One makes the
        # previous side square, the other makes the next side square. Develop
        # both complete layouts and keep the one with more final stalls.
        for chamfer_side in chamfer_sides:
            outer_poly, inner_poly = ring_band_points(
                polygon, z, ring_outer, ring_outer + RING_WIDTH,
                edge_outer=variant["edge_outer"],
                chamfer_side=chamfer_side,
            )
            if not outer_poly or not inner_poly:
                continue

            pack_basis = basis
            core_poly = as_xy_polygon(inner_poly)
            drive_poly = as_xy_polygon(outer_poly)
            entrance_points = (
                access_points_on_street_edge(street_edge) if street_edge else None
            )
            interior = layout_for_angle(
                core_poly, pack_basis, 0.0, z, geometry, drive_poly, polygon,
                entrance_points=entrance_points,
            )
            long_aisle = span_u >= span_v
            ring_meta = {
                "ring_mode": "offset",
                "ring_outer": ring_outer,
                "ring_inner": ring_outer + RING_WIDTH,
                "ring_outer_poly": outer_poly,
                "ring_inner_poly": inner_poly,
                "long_aisle": long_aisle,
                "chamfer_side": chamfer_side,
            }
            candidate = compose_candidate(
                perimeter_stalls, interior, pack_basis, geometry, ring_meta, polygon,
                street_edge, ring_islands=perimeter_islands,
            )
            best = better_candidate(best, candidate)
    return best


def _search_layouts(polygon, z, setback, access_points, stall_width, park_configs, street_edge=None):
    """Compare three cleaned aisle skeleton options, then populate the winner."""
    origin = polygon_centroid(polygon)
    orientations = primary_skeleton_orientations(polygon, street_edge)
    geometries = [module_geometry(angle, flow, stall_width) for angle, flow in park_configs]
    best = None
    option_candidates = []

    for angle in orientations:
        basis = make_basis(origin, angle)
        orientation_best = None
        for geometry in geometries:
            # Prefer rings that follow the parcel so the site is not over-cut.
            offset = try_offset_layouts(
                polygon, basis, z, setback, geometry, stall_width, street_edge,
            )
            orientation_best = better_candidate(orientation_best, offset)
        if orientation_best:
            option_candidates.append(orientation_best)
            best = better_candidate(best, orientation_best)

    # A fitted orthogonal ring is a fallback only. It must not compete against
    # a valid site-following ring by cutting away most of an irregular site.
    if best is None:
        for angle in orientations:
            basis = make_basis(origin, angle)
            for geometry in geometries:
                ortho = try_ortho_layouts(
                    polygon, basis, z, setback, geometry, stall_width, street_edge,
                )
                best = better_candidate(best, ortho)

    if best:
        best["orientation_options"] = [
            {
                "angle": candidate["angle"],
                "stall_count": candidate["stall_count"],
                "connected_run_count": candidate.get("connected_run_count", 0),
                "aisle_length": candidate.get("aisle_length", 0.0),
            }
            for candidate in option_candidates
        ]
        # Every option is fully developed, so the user can draw any of them
        # instead of being forced onto the highest count.
        best["option_layouts"] = option_candidates
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
        for candidate in best.get("option_layouts", []) + [best]:
            candidate["ada"] = ada_stall_count(candidate["stall_count"])
            if street_edge:
                candidate["street_edge"] = street_edge

    return best


def option_layouts(layout):
    """Developed orientation options, best first, for user selection."""
    if not layout:
        return []
    options = list(layout.get("option_layouts") or [])
    if not options:
        return [layout]
    options.sort(key=lambda candidate: candidate["stall_count"], reverse=True)
    for candidate in options:
        candidate.setdefault("orientation_options", layout.get("orientation_options", []))
        candidate.setdefault("option_layouts", options)
    return options


def ring_band_points(polygon, z, outer_distance, inner_distance,
                     edge_outer=None, chamfer_side=1):
    """Return the ring drive as (outer, inner) closed point lists.

    Build/chamfer the INNER curb (parking core) first, then offset it OUTWARD
    exactly 24 ft. This preserves a usable chamfer face on the driver's inside
    turn and spends the naturally wider acute-tip pocket outside the aisle,
    instead of shrinking the parking core. Both derived curbs remain within
    the independently computed raw outer limit.
    """
    if edge_outer:
        raw_outer = offset_polygon_edges(polygon, edge_outer)
        inner = offset_polygon_edges(
            polygon, [distance + RING_WIDTH for distance in edge_outer],
        )
    else:
        raw_outer = offset_polygon(polygon, outer_distance)
        inner = offset_polygon(polygon, inner_distance)
    if not raw_outer or not inner:
        return None, None

    inner = chamfer_acute_corners(
        inner, right_angle_side=chamfer_side,
    )
    outer = offset_polygon_edges(
        inner, [-RING_WIDTH] * len(inner),
    )
    if not outer or len(outer) < 3 or len(inner) < 3:
        return None, None

    # The outward construction must not escape the usable-site outer limit.
    # Check vertices and edge midpoints so concave parcels cannot bridge an
    # exterior notch.
    samples = list(outer)
    samples.extend([
        (
            0.5 * (outer[index][0] + outer[(index + 1) % len(outer)][0]),
            0.5 * (outer[index][1] + outer[(index + 1) % len(outer)][1]),
        )
        for index in range(len(outer))
    ])
    for x, y in samples:
        if (
            not point_inside(raw_outer, x, y)
            and distance_to_polygon(raw_outer, x, y) > 0.1
        ):
            return None, None

    # Packing uses the chamfered polygons. Filleting is applied when curbs are
    # drawn so the core is not eaten by R15 arcs at every corner.
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


def offset_polygon_edges(polygon, distances):
    """Inward offset where every edge carries its own distance.

    The street frontage has no perimeter stall row in front of it, so its
    ring curb sits at the bare setback while the other edges sit a stall
    depth further in. A single scalar offset cannot express that.
    """
    count = len(polygon)
    if count < 3 or len(distances) != count:
        return None

    orientation = 1.0 if signed_area(polygon) > 0.0 else -1.0
    lines = []
    for index in range(count):
        ax, ay = polygon[index]
        bx, by = polygon[(index + 1) % count]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return None
        ux, uy = dx / length, dy / length
        nx, ny = -uy * orientation, ux * orientation
        distance = distances[index]
        lines.append((ax + nx * distance, ay + ny * distance, ux, uy))

    result = []
    for index in range(count):
        px, py, ux, uy = lines[index - 1]
        qx, qy, vx, vy = lines[index]
        denominator = ux * vy - uy * vx
        if abs(denominator) < 1e-9:
            result.append((qx, qy))
            continue
        t = ((qx - px) * vy - (qy - py) * vx) / denominator
        result.append((px + ux * t, py + uy * t))

    if len(result) < 3:
        return None
    return result


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


def fillet_closed_polygon(points, z=0.0, radius=CURB_FILLET_RADIUS,
                          arc_segments=FILLET_ARC_SEGMENTS):
    """Approximate a constant-radius fillet at each convex polygon corner.

    The requested radius is 5 ft. It is locally reduced only when adjacent
    edges are too short (for example, a 9 ft-wide terminal island cannot
    physically contain two full R5 tangencies).
    """
    poly = as_xy_polygon(points)
    if len(poly) > 1 and math.hypot(
        poly[0][0] - poly[-1][0], poly[0][1] - poly[-1][1],
    ) < 1e-6:
        poly = poly[:-1]
    if len(poly) < 3 or radius <= 0.0:
        return _closed_xyz(poly, z)

    orientation = 1.0 if signed_area(poly) >= 0.0 else -1.0
    result = []
    count = len(poly)

    for index in range(count):
        prev = poly[(index - 1) % count]
        curr = poly[index]
        nxt = poly[(index + 1) % count]
        to_prev = (prev[0] - curr[0], prev[1] - curr[1])
        to_next = (nxt[0] - curr[0], nxt[1] - curr[1])
        len_prev = math.hypot(to_prev[0], to_prev[1])
        len_next = math.hypot(to_next[0], to_next[1])
        angle = interior_angle_deg(poly, index)

        # Concave / nearly straight vertices are kept sharp; a curb fillet
        # here would extend outside its landscape region.
        if (
            len_prev < 1e-6 or len_next < 1e-6
            or angle <= 5.0 or angle >= 175.0
        ):
            result.append((curr[0], curr[1], z))
            continue

        ux0, uy0 = to_prev[0] / len_prev, to_prev[1] / len_prev
        ux1, uy1 = to_next[0] / len_next, to_next[1] / len_next
        half = math.radians(angle * 0.5)
        tan_half = math.tan(half)
        sin_half = math.sin(half)
        if abs(tan_half) < 1e-6 or abs(sin_half) < 1e-6:
            result.append((curr[0], curr[1], z))
            continue

        tangent = radius / tan_half
        tangent = min(tangent, len_prev * 0.5, len_next * 0.5)
        effective_radius = tangent * tan_half
        if effective_radius < 0.25:
            result.append((curr[0], curr[1], z))
            continue

        p0 = (curr[0] + ux0 * tangent, curr[1] + uy0 * tangent)
        p1 = (curr[0] + ux1 * tangent, curr[1] + uy1 * tangent)
        bis_x, bis_y = ux0 + ux1, uy0 + uy1
        bis_len = math.hypot(bis_x, bis_y)
        if bis_len < 1e-6:
            result.append((curr[0], curr[1], z))
            continue
        center_dist = effective_radius / sin_half
        center = (
            curr[0] + bis_x / bis_len * center_dist,
            curr[1] + bis_y / bis_len * center_dist,
        )

        start = math.atan2(p0[1] - center[1], p0[0] - center[0])
        end = math.atan2(p1[1] - center[1], p1[0] - center[0])
        if orientation > 0.0:
            while end <= start:
                end += 2.0 * math.pi
        else:
            while end >= start:
                end -= 2.0 * math.pi
        sweep = end - start
        steps = max(2, int(math.ceil(
            abs(sweep) / (0.5 * math.pi) * arc_segments,
        )))
        for step in range(steps + 1):
            angle_at = start + sweep * step / float(steps)
            result.append((
                center[0] + effective_radius * math.cos(angle_at),
                center[1] + effective_radius * math.sin(angle_at),
                z,
            ))

    if result and (
        abs(result[0][0] - result[-1][0]) > 1e-6
        or abs(result[0][1] - result[-1][1]) > 1e-6
    ):
        result.append(result[0])
    return result


def rounded_bay_envelopes(layout, z, radius=BAY_FILLET_RADIUS):
    """Bay island outlines with generous returns.

    A large return turns the orthogonal step between two unequal rows into
    the tapered nose a hand drawing shows, and rounds the closed end of the
    island. fillet_closed_polygon clamps the radius per corner, so short
    edges still get a proportionate curve.
    """
    rounded = []
    for envelope in layout.get("bay_envelopes", []):
        curve = fillet_closed_polygon(envelope, z, radius)
        if curve:
            rounded.append(curve)
    return rounded


def tip_pocket_islands(site_polygon, layout, z, radius=CURB_FILLET_RADIUS * 1.6):
    """Landscape the unreachable tip outside the chamfered ring.

    The dead zone between an acute site corner and the outer ring curb is not
    driveable. Build the island from the site tip to the ring's chamfer face so
    it meets the curb cleanly instead of floating a circle in the aisle.
    """
    outer = layout.get("ring_outer_poly")
    if not outer:
        return []
    outer_xy = as_xy_polygon(outer)
    if len(outer_xy) < 3:
        return []

    islands = []
    for _index, _angle, (vx, vy) in acute_vertices(site_polygon, MIN_DRIVE_CORNER_DEG):
        # The chamfer face is the outer-ring edge closest to the tip.
        best = None
        count = len(outer_xy)
        for index in range(count):
            ax, ay = outer_xy[index]
            bx, by = outer_xy[(index + 1) % count]
            dist = distance_to_segment(vx, vy, ax, ay, bx, by)
            length = math.hypot(bx - ax, by - ay)
            if length < RING_CORNER_RADIUS:
                continue
            if best is None or dist < best[0]:
                best = (dist, (ax, ay), (bx, by))
        if best is None:
            continue
        _dist, a, b = best
        # The nearest outer-ring edge to an acute tip is the chamfer face,
        # even when the tip setback is deep on a narrow wedge.
        if _dist > max(tip_clearance_depth(_angle), RING_WIDTH * 5.0):
            continue
        shape = [(vx, vy), a, b]
        if signed_area(shape) == 0.0:
            continue
        # Keep the pocket outside the drive — reject if its centroid is inside.
        cx = (vx + a[0] + b[0]) / 3.0
        cy = (vy + a[1] + b[1]) / 3.0
        if point_inside(outer_xy, cx, cy):
            continue
        curve = fillet_closed_polygon(shape, z, radius)
        if curve and len(curve) >= 4:
            islands.append(curve)
    return islands


def rounded_layout_islands(layout, z, radius=CURB_FILLET_RADIUS):
    rounded = []
    for island in layout.get("islands", []):
        curve = fillet_closed_polygon(island, z, radius)
        if curve:
            rounded.append(curve)
    return rounded


def _edge_strip_polygon(ax, ay, bx, by, inward_nx, inward_ny, depth, z=0.0):
    """Closed strip from a boundary edge inward by ``depth``."""
    if depth <= 0.0:
        return None
    return [
        (ax, ay, z),
        (bx, by, z),
        (bx + inward_nx * depth, by + inward_ny * depth, z),
        (ax + inward_nx * depth, ay + inward_ny * depth, z),
    ]


def setback_landscape_polygons(site_polygon, setback, z=0.0, street_edge=None):
    """Perimeter landscape strips between the property line and the setback.

    These are non-drivable. Street driveway throats stay open.
    """
    if setback <= 0.0 or not site_polygon or len(site_polygon) < 3:
        return []

    orientation = 1.0 if signed_area(site_polygon) > 0.0 else -1.0
    street_index = street_edge.get("index") if street_edge else None
    strips = []
    count = len(site_polygon)
    for index in range(count):
        ax, ay = site_polygon[index][0], site_polygon[index][1]
        bx, by = site_polygon[(index + 1) % count][0], site_polygon[(index + 1) % count][1]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-9:
            continue
        ux, uy = dx / length, dy / length
        # Inward normal for CCW polygon is rotate(ux,uy) left = (-uy, ux).
        nx, ny = -uy * orientation, ux * orientation
        if street_index is not None and index == street_index:
            # Keep the setback band but punch driveway gaps.
            stations = access_points_on_street_edge(street_edge)
            if not stations:
                strip = _edge_strip_polygon(ax, ay, bx, by, nx, ny, setback, z)
                if strip:
                    strips.append(strip)
                continue
            gap = DRIVEWAY_CLEAR * 0.55
            open_runs = []
            cursor = 0.0
            for station in stations:
                sx, sy = station[0], station[1]
                t = max(0.0, min(length, (sx - ax) * ux + (sy - ay) * uy))
                lo, hi = max(0.0, t - gap), min(length, t + gap)
                if lo > cursor + 1.0:
                    open_runs.append((cursor, lo))
                cursor = max(cursor, hi)
            if length - cursor > 1.0:
                open_runs.append((cursor, length))
            for u0, u1 in open_runs:
                p0 = (ax + ux * u0, ay + uy * u0)
                p1 = (ax + ux * u1, ay + uy * u1)
                strip = _edge_strip_polygon(p0[0], p0[1], p1[0], p1[1], nx, ny, setback, z)
                if strip:
                    strips.append(strip)
            continue

        strip = _edge_strip_polygon(ax, ay, bx, by, nx, ny, setback, z)
        if strip:
            strips.append(strip)
    return strips


def layout_land_use(site_polygon, layout, setback=0.0, street_edge=None, z=0.0):
    """Classify the parcel into the three universal land-use sets.

    The site is first split into:

    - ``non_drivable``: cars never roll here — tip pockets, setback landscape,
      terminal end-caps, mid-row islands / medians.
    - ``drivable``: everything else inside the site.

    Drivable then splits into:

    - ``standing``: parking stalls (cars at rest).
    - ``moving``: the residual aisle. The 24 ft aisle is NOT an authored
      object — it emerges as the gap between non-drivable islands / end-caps
      and the stall faces. Shaping those green end-caps is what creates (or
      destroys) a driveable turn.

    Returns filled polygons suitable for preview / Rhino layers.
    """
    if not layout:
        return {
            "non_drivable": [],
            "standing": [],
            "moving_hint": None,
            "ring_outer": None,
            "ring_inner": None,
        }

    non_drivable = []
    non_drivable.extend(setback_landscape_polygons(
        site_polygon, setback, z, street_edge,
    ))
    non_drivable.extend(tip_pocket_islands(site_polygon, layout, z))
    non_drivable.extend(rounded_layout_islands(layout, z))

    standing = list(layout.get("stalls") or [])
    outer, inner = layout_ring_polylines(layout, site_polygon, z)

    return {
        "non_drivable": non_drivable,
        "standing": standing,
        "moving_hint": {
            # Diagnostic only: authored ring curbs that the green islands must
            # reproduce as residual gaps. Never draw these as the product aisle.
            "ring_outer": outer,
            "ring_inner": inner,
            "nominal_width": RING_WIDTH,
        },
        "ring_outer": outer,
        "ring_inner": inner,
    }


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
    """Disabled: axis-aligned leftover AABBs falsely covered real stalls.

    Terminal / interior end-cap islands are emitted explicitly during packing.
    Stall-back curbs already outline the parked field.
    """
    return []


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
    - acute-corner keep-out pockets
    """
    curbs = []

    setback_poly = offset_polygon(site_polygon, max(setback, 0.0))
    if setback_poly:
        rounded = fillet_closed_polygon(setback_poly, z)
        rounded_xy = as_xy_polygon(rounded[:-1]) if rounded else setback_poly
        tip_gaps = tip_keepout_triangles(
            site_polygon,
            ring_outer_poly=layout.get("ring_outer_poly"),
        )
        if tip_gaps:
            # Leave the setback curb open through acute tips — the tip pocket
            # island owns that landscape, and drawing both stacks ghost lines.
            runs = []
            count = len(rounded_xy)
            current = []
            for index in range(count):
                ax, ay = rounded_xy[index]
                bx, by = rounded_xy[(index + 1) % count]
                mid = (0.5 * (ax + bx), 0.5 * (ay + by))
                in_tip = any(point_inside(tri, mid[0], mid[1]) for tri in tip_gaps)
                if in_tip or _segment_near_street_gap(ax, ay, bx, by, street_edge):
                    if len(current) >= 2:
                        runs.append(current)
                    current = []
                    continue
                if not current:
                    current.append((ax, ay, z))
                current.append((bx, by, z))
            if len(current) >= 2:
                runs.append(current)
            curbs.extend(runs)
        else:
            curbs.extend(split_closed_curb_at_street(rounded_xy, z, street_edge))

    outer, inner = layout_ring_polylines(layout, site_polygon, z)
    if outer:
        # Fillet the drive loop at a two-way turning radius. Do not also emit
        # the sharp chamfered polyline — that stacked ghost corners under arcs.
        outer_xy = as_xy_polygon(outer)
        rounded = fillet_closed_polygon(outer_xy, z, RING_CORNER_RADIUS)
        rounded_xy = as_xy_polygon(rounded[:-1]) if rounded else outer_xy
        curbs.extend(split_closed_curb_at_street(rounded_xy, z, street_edge))
    if inner:
        inner_xy = as_xy_polygon(inner)
        rounded = fillet_closed_polygon(inner_xy, z, RING_CORNER_RADIUS)
        if rounded:
            curbs.append(rounded)

    curbs.extend(stall_back_curbs(layout.get("stalls", []), site_polygon, z))
    curbs.extend(rounded_layout_islands(layout, z))
    # Tip dead zones are drawn as tip_pocket_islands; skip the old square
    # keep-out markers that stacked on the same corner.

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
