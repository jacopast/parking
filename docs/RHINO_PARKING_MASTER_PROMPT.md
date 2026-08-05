# Rhino Parking Layout — Master Prompt & Rules

Copy the **MASTER PROMPT** section below into a new AI chat to recreate this toolkit.
Optionally attach reference PDFs/manual drawings and add:
`Deliver as rhino/parking_core.py + rhino/parking_layout.py; Rhino RunPythonScript; feet units.`

Repo layout: keep the whole `rhino/` folder together. Scripts force-reload `parking_core` each run.

---

## MASTER PROMPT

```text
You are building a Rhino Python surface-parking layout toolkit.

GOAL
----
Given ANY closed site boundary polygon (arbitrary shape) and ONE designated
street-frontage edge, generate the maximum number of driveable parking stalls
that a car can actually use. Geometry and circulation come first. Stall count
is the score AFTER the layout is driveable.

Do NOT invent a different algorithm. Follow the pipeline and hard rules below
exactly. Prefer Rhino `rhinoscriptsyntax` + pure-Python geometry (no Grasshopper
required). Deliver:

- `parking_core.py`  : shared geometry engine (pure Python except optional helpers)
- `parking_layout.py`: Rhino RunPythonScript UX (pick one or more sites;
  each site gets its own street edge + independent layout; shared setback)
- optional feasibility panel may exist, but surface layout is primary

Units: FEET throughout.


═══════════════════════════════════════════════════════════════════════════════
0. PRODUCT INTENT / UX
═══════════════════════════════════════════════════════════════════════════════

User flow in Rhino (collect ALL human inputs first, then solve):
1. Pick ONE OR MORE CLOSED available-area curves (multi-select / preselect OK).
   Each selected curve is a separate site designed independently.
2. Ask for setback from property line (default 5 ft, min 0) — shared across sites.
3. Orientation policy up front: Yes = auto-best per site; No = ListBox after solve.
4. For EACH site, pick ONE EXISTING EDGE of that curve as street frontage.
   - Do NOT ask the user to draw a new line.
   - Highlight temporary segments of existing edges for selection.
   - Do NOT start computing until every street edge has been picked (or skipped).
5. Compute all sites. If policy was No, show orientation ListBox per site.
6. Draw all sites into layers, grouped per site (`Parking Layout — Site N`).
   NO model text labels on geometry.
7. Combined MessageBox summary per site: stall count, perimeter stalls,
   aisle orientation, street length, ADA counts.
8. ESC cancels the WHOLE run at any time:
   - poll during direction screening, every developed option / lattice phase,
     field combinations, grid erosion, flood fill, and drawing loops
   - raise one core `LayoutCancelled` signal; Rhino catches it at the command
     boundary
   - delete geometry created by the active run, unselect, redraw, and return
     without a modal error dialog

Layers (Parking Layout):
- Available Area
- NonDrivable   # tip pockets, setbacks, end-caps, mid-islands (GREEN)
- Stalls        # standing cars
- Circulation   # street edge / access throats only
- Curbs         # diagnostic curb returns
- Islands       # legacy alias; prefer NonDrivable

Land-use model (universal):
  The parcel is first split into NON-DRIVABLE vs DRIVABLE.
  NON-DRIVABLE (green) = anything a car must not roll on:
    setback landscape, tip dead-zone pockets, terminal end-caps,
    mid-row islands / medians.
  DRIVABLE then splits into:
    STANDING = parking stalls
    MOVING   = residual pavement between non-drivable greens and stall faces
  The 24 ft aisle is NOT an authored polyline. It EMERGES as the gap created
  by placing / shaping non-drivable end-caps and medians. A sharp end-cap
  creates an acute turn; a square or obtuse end-cap creates a legal turn.
  Score by valid standing stalls only after the moving residual is driveable.

Reload rule: parking_layout.py must force-reload parking_core each run
(sys.modules.pop) so Rhino does not keep a stale module.


═══════════════════════════════════════════════════════════════════════════════
1. HARD DIMENSIONS (SUDAS / ULI 90° two-way primary)
═══════════════════════════════════════════════════════════════════════════════

Constants:
  STALL_WIDTH              = 9.0
  STALL_STRIPE / DEPTH     = 18.0
  AISLE_WIDTH (90° 2-way)  = 24.0
  DOUBLE_LOADED_MODULE     = 60.0   # 18 + 24 + 18
  RING_WIDTH               = 24.0   # perimeter circulation drive
  DRIVEWAY_CLEAR           = 28.0   # street curb-cut throat clear of stalls
  MIN_RUN_COLUMNS          = 3
  TERMINAL_ISLAND_COLUMNS  = 1      # end-cap island width in stall columns
  MAX_STALLS_BETWEEN_ISLANDS = 10
  CURB_FILLET_RADIUS       = 5.0    # nominal; clamp locally if edges too short
  ACUTE_CORNER_DEG         = 90.0
  MIN_DRIVE_CORNER_DEG     = 90.0
  ACUTE_KEEP_OUT           = 18 + 12 = 30.0  # tip keep-out depth seed

Active packing: 90° two-way ONLY.
Diagonal 60°/45° modules are LAST RESORT only if 90° finds zero stalls.

Module formulas (for fallback angles):
  stall_pitch_along_aisle = stall_width / sin(angle)
  stall_projection        = stall_stripe * sin(angle)
  double_module           = 2 * stall_projection + aisle
  single_module           = stall_projection + aisle

ADA: 2010 ADA Standards Table 208.2; van stalls = ceil(accessible / 6).


═══════════════════════════════════════════════════════════════════════════════
2. PIPELINE ORDER (MANDATORY — matches manual / CAD study drawings)
═══════════════════════════════════════════════════════════════════════════════

This order is NOT optional. Do NOT place stalls first and delete failures later
as the primary strategy. Structural invalidity must be prevented before stalls.

STEP A — Site + street
  - Closed polygon P.
  - Street edge E (one existing side of P).
  - Setback S ≥ 0.

STEP B — Circulation ring FIRST (site-following)
  - Outer ring curb distance from site ≈ S + 18 (if perimeter stalls used)
    OR ≈ S (if no perimeter row variant).
  - PER-EDGE offset, not one scalar: the street frontage carries no
    perimeter stall row, so its ring curb sits at S while the other edges
    sit at S + 18. Offset each edge on its own line and intersect
    neighbouring offset lines to rebuild the vertices.
  - Ring drive band width = 24 ft.
  - Offset polygon inward; CHAMFER only corners with interior angle < 90°.
  - VALIDITY GATE on every offset (bisector or per-edge):
      * area sign matches the parent
      * no self-intersection
      * vertices / edge midpoints stay inside the parent (inward)
      * abs(area) is meaningful (> ~1 sf)
    On failure: fall back to grid erosion (raster cells with clearance ≥
    offset distance, extract connected components). Multi-lobe cores are a
    valid result — never invent a flipped courtyard. If no core fits but
    perimeter stalls do, emit a perimeter-only layout.
  - At an acute tip, prefer an ASYMMETRIC chamfer: one new drive corner is
    exactly 90° and the other is obtuse. Evaluate the two mirrored chamfers
    and keep the completed layout with more valid stalls.
  - Build the chamfered INNER ring / parking core first, then offset it
    OUTWARD exactly 24 ft to create the outer curb. Do not chamfer both curbs
    independently: that creates a false 40–50 ft wedge at acute tips.
  - Validate the actual 12 ft-offset DRIVE CENTRELINE:
      * no interior corner below 90°
      * each straight is long enough for the R15 tangencies at both ends
    Reject a candidate that fails either test, even if both curb polygons
    separately report legal angles.
  - Obtuse corners stay. Never collapse irregular sites to a tiny ortho
    rectangle just to force square turns.
  - Prefer site-following offset ring. Orthogonal racetrack is FALLBACK only
    when no valid site-following option exists.

STEP C — Parking core
  - Core = actual chamfered INNER ring polygon (not a scalar setback alone).
  - The irregular core is only a CONTAINER. Do not make the interior parking
    field imitate every curve, notch, taper, or acute side.
  - Fit one clean orthogonal rectangle first. In broad L / U / multi-lobe
    cores, compose up to THREE non-overlapping clean rectangles, separated by
    a 24 ft drive/cross-aisle gap.
  - Every rectangle must be wholly inside the core; reject rectangles that
    bridge a concave notch even when all four corners happen to be inside.
  - Find rectangles by rasterizing the core and taking maximal all-inside
    rectangles (greedy peel plus half-plane restarts). Symmetric growth from
    a seed point misses off-center fields and wastes large areas.
  - A field rectangle BOUNDS the lattice; it does not replace the core for
    containment. Stalls are still validated against the real core polygon.
  - Capacity stays the objective. Prefer ordered fields, but fall back to the
    boundary-following lattice when it wins by a wide margin, otherwise a
    curved or tapered parcel throws away usable parking.

STEP D — Aisle-orientation options (screen many, develop three)
  Seed directions: street-perpendicular, street-parallel, dominant edges.
  Also coarse-sweep every 15° by projected bay length and keep the best
  slots (up to MAX_ORIENTATIONS).
  SCREEN before developing: the ring does not depend on orientation, so score
  each direction with one coarse lattice pass, then fully develop only the top
  DEVELOPED_ORIENTATIONS that are at least ORIENTATION_SPREAD_DEG apart.
  Developing every swept direction costs minutes on a large parcel, and
  near-duplicate angles return effectively the same plan.
  Present the completed options in the Rhino ListBox.

  Performance rules that matter at scale (an 18-acre parcel went from ~11 min
  to ~1 min by observing these):
  - Solve each module band once as U intervals; never probe nine points per
    stall column.
  - Memoize polygon vertices in the parking frame, strip intervals, tip
    keep-out zones, and grid erosion.
  - Flood fill by scanlining the drive region per row, then subtracting each
    obstacle over its own bounding box only.
  - Check stall overlap with a spatial bucket grid, not every pair.
  - Trim field candidates and lattice phase steps once the core exceeds
    LARGE_CORE_AREA.
  - Report progress through the ``progress`` callback so a long solve is not
    a silent wait.

STEP D.5 — Entrance alignment (absorbed "spine aisle" idea)
  - Project the entrance point(s) inward and add grid phases that seat a bay
    AISLE on the entrance, so a car drives straight in without an immediate
    turn at the mouth. This is ADDITIVE to the phase search; the best-scoring
    phase still wins, so alignment never costs stalls.
  - Do NOT hard-overwrite orientation to entrance-parallel (a common mistake):
    that can discard a higher-capacity street-parallel option. Keep all three
    orientations and let the score / user pick.

STEP E — Ordered rectangular fields BEFORE bays
  - For each orientation, grow rectangular field candidates from interior
    sample centers.
  - Snap field dimensions inward to 9 ft stall / 18 ft depth increments.
  - Rank fully developed combinations by final valid stall count.
  - Use one rectangle when that is best; use two or three only when secondary
    lobes add valid parking without overlap.
  - Curves and irregular leftovers outside the fields become landscape /
    pavement. A lower count is acceptable when the alternative is malformed
    geometry or inaccessible stalls.

STEP F — Bay islands inside each field (island-centred, NOT aisle-centred)
  The drawn module is the 36 ft BACK-TO-BACK STALL ISLAND, exactly as it is
  drafted by hand. Its 24 ft aisles sit OUTSIDE it and are shared with the
  next island or with the perimeter ring. The lattice period is still
  18 + 24 + 18 = 60 ft in V; only the phase differs from an aisle-centred
  scheme, but the ends and the outermost bay come out completely different.
  For a candidate direction (U along the island, V across it):
  - Island band = [center_v - 18, center_v + 18], lattice period 60 ft.
  - TWO REGIONS, not one:
      park region  = one clean rectangular field (stalls may sit here)
      drive region = chamfered OUTER ring polygon (aisles may sit here)
    An outer bay is legally served by the ring drive, so it must NOT be
    required to find a second interior aisle inside the core.

STEP G — Per-column, per-ROW fitting inside each clean field
  - Walk the shared 9 ft column grid across the island band.
  - For each column test each row separately:
      stall cell [u, u+9] x 18 ft   inside PARK region, and
      aisle cell [u, u+9] x 24 ft   in front of it inside DRIVE region.
  - The field itself is rectangular, so row ends stay ordered. Do not trim
    individual rows into a jagged imitation of the parcel boundary.
  - After the double-loaded lattice, fill leftover v-strips ≥ 42 ft with
    single-loaded modules (18 + 24).
  - If a leftover strip cannot take another clean row, GROW the outer
    setback / ring offset (or paint the void as non-drivable landscape /
    pavement). Stall count still wins when a valid row fits; when it does
    not, shrink the parking field rather than leave empty driveable waste.
  - After packing, flood-fill driveable cells from street access and drop
    stalls whose aisle is not reachable (disconnected concave lobes).
  - Two rows of the same island therefore have DIFFERENT lengths on a
    tapered site, and the island end steps/tapers with the parcel edge.
  - Minimum usable stall columns after end-caps: MIN_RUN_COLUMNS (3).

STEP H — Roles / islands BEFORE filling stalls
  Both rows share ONE absolute column grid:
  - First and last TERMINAL_ISLAND_COLUMNS columns of EACH row = terminal
    (end-cap) islands — each row caps at its own end.
  - Interior islands are placed on shared absolute column indices so they
    line up across the island; no more than MAX_STALLS_BETWEEN_ISLANDS
    consecutive stall columns.
  - Remaining columns = stalls.
  End-caps exist so cars can turn at the cross aisle. Never park into the tip
  of a row at the aisle intersection.

STEP I — Populate stalls LAST
  - Stalls are struck from the aisle face back to the shared island spine.
  - Emit a closed BAY ENVELOPE per island (its stepped outline) and draw
    that, filleted at BAY_FILLET_RADIUS ≈ 9 ft. Do NOT draw raw aisle
    rectangles: the aisle is the space between islands, and a drawn aisle
    box sticking out past the last stall is the classic tell that the
    layout is aisle-centred.
  - Perimeter stalls: single-loaded outside the ring, backs toward setback /
    property edge, fronts toward ring. Skip street-frontage edge entirely.
  - Run rows along MERGED nearly-collinear boundary chords, not single edges.
    A simplified curve is a chain of 10-20 ft chords, so per-edge testing
    rejects every perimeter row and leaves curved sites with zero perimeter
    parking. Merge while the heading stays within ~20 deg and no vertex
    strays more than about half a stall width from the run chord.
  - Interior of ring is NOT also single-loaded as a second perimeter ring
    (that wastes a module edge).

STEP J — Tip dead zones / acute corners
  - Site tips with interior angle ≤ 90° cannot host stalls.
  - Chamfer the RING at those tips; leave the region between tip and
    chamfered ring EMPTY (manual dead zone).
  - Tip keep-out triangles deepen to the ring curb when ring geometry exists.
  - Stall ban threshold must MATCH ring chamfer threshold (both 90°).
    Never chamfer at 90° while banning stalls only below 80°.

STEP K — Street access
  - Place entry/exit stations along street edge (e.g. 1/3 and 2/3; mid if short).
  - Driveway throats project STRAIGHT INWARD along the street inward normal
    onto the ring — NEVER to the nearest ring vertex (that creates diagonal
    W/V slashes).
  - No stalls in driveway clear zones (DRIVEWAY_CLEAR ≈ 28 ft).
  - Setback / outer-ring curbs are gapped at street curb cuts.

STEP L — Finish curbs / islands with fillets
  - Nominal fillet radius = 5 ft on:
    terminal islands, interior islands, perimeter islands,
    setback curb, ring outer, ring inner, acute tip pockets.
  - Represent fillets as dense polylines (Rhino RunPythonScript friendly).
  - Locally clamp radius when edges are too short (e.g. 9 ft-wide island
    cannot hold two full R5 tangencies; use max feasible ~R4.5).
  - Concave / nearly straight corners stay sharp.

STEP M — Score and pick
  Rank completed options primarily by FINAL driveable stall count.
  Tie-breakers (secondary): connected run count, aisle length,
  street alignment, site-following ring preferred over ortho fallback.
  Do NOT let a sparse malformed interior beat a higher-capacity valid layout
  via a stale “has any connected run” bit.


═══════════════════════════════════════════════════════════════════════════════
3. HARD RULES (cars must be able to move)
═══════════════════════════════════════════════════════════════════════════════

R1. Every 90° stall needs a clear 24 ft maneuvering aisle in front.
R2. Both sides of a double-loaded aisle must survive together.
    BUG TO AVOID: probing exactly 24 ft onto the opposing stall’s front edge
    and treating boundary contact as collision (polygon point-in-poly
    asymmetry deletes one side). Touching the opposing stripe endpoint is
    NOT an obstruction.
R3. No stalls in acute tips / unreachable tip dead zones beyond the
    chamfered ring.
R4. No stalls blocking street entry/exit throats.
R5. Drive aisle corners may be obtuse or 90°; acute (<90°) drive corners
    are forbidden. Prefer one square + one obtuse chamfer corner, test both
    mirrored sides. Derive the outer curb 24 ft from the inner curb and test
    the actual drive centreline plus R15 tangent fit.
R6. Every interior bay aisle must connect to the perimeter ring.
R7. Terminal islands at BOTH ends of every parking row; max 10 stalls
    between islands.
R8. Perimeter stalls only on the OUTSIDE of the ring; never fill tip spikes.
R9. Prefer maximizing driveable stalls on the real parcel shape — do not
    reward tiny inscribed rectangles that “look clean” but waste area.
R10. Do not emit ghost axis-aligned leftover AABB “islands” that cover real
     stalls. Explicit islands + stall-back curbs only.
R11. No text baked into the Rhino model geometry.


═══════════════════════════════════════════════════════════════════════════════
4. GEOMETRY DATA STRUCTURES
═══════════════════════════════════════════════════════════════════════════════

Skeleton run (before stalls):
  {
    center_v, u0, u1, count, roles[],
    aisle_left, aisle_right,
    left_connected, right_connected,
    stall_count  # roles.stall_count * rows
  }

Final layout dict:
  stalls, aisles, islands,
  stall_count, perimeter_stalls, run_count,
  connected_run_count, aisle_length,
  angle, park_angle, flow,
  ring_mode ("offset"|"ortho"),
  ring_outer, ring_inner,
  ring_outer_poly, ring_inner_poly,
  access_points, access_clear,
  orientation_options: [{angle, stall_count, connected_run_count, aisle_length}, ...],
  ada: {accessible, van}


═══════════════════════════════════════════════════════════════════════════════
5. KEY ALGORITHMS (IMPLEMENT THESE)
═══════════════════════════════════════════════════════════════════════════════

A. Offset polygon inward by distance (vertex bisector method).
B. Chamfer acute corners (<90°) asymmetrically so one new corner is 90° and
   the other is obtuse. Test both mirrored sides and score by final stalls.
   A ~30 ft far cut on a ~53° tip creates an approximately 24 ft face without
   the capacity loss of a deep symmetric cut.
C. Tip keep-out triangles: cut from tip along both edges by tip_clearance_depth
   (from wedge width needed for stall+ring); deepen to ring curb when available.
D. primary_skeleton_orientations → exactly 3 angles.
E. strip_common_intervals(core, basis, v0, v1) for directional module fit.
F. extend_aisle_to_ring along U using aisle_slice_fits samples.
G. run_column_roles(n) → terminal / stall / interior.
H. materialize: stall_shape both sides + aisle rectangle + island quads.
I. fillet_closed_polygon(points, radius=5).
J. compose_candidate:
   - Perimeter stalls: tip/ring/access QA filters.
   - Interior stalls: generated as paired rows around explicit aisle;
     do NOT re-run the buggy generic opposing-stall collision filter that
     deletes one side.
K. street_inward_normal + ray cast for driveway_throat_target.


═══════════════════════════════════════════════════════════════════════════════
6. ANTI-PATTERNS (DO NOT DO THESE — they already failed in practice)
═══════════════════════════════════════════════════════════════════════════════

❌ Place stalls everywhere then filter as the main design method.
❌ Score by stall count before ring connectivity / tip clearance.
❌ Ban stalls only below 80° while chamfering ring at 90°.
❌ Snap access lines to nearest ring vertex (diagonal slash throats).
❌ Isotropic 30 ft erosion of the whole core (shortens aisles wrongly).
❌ Requiring a single common U rectangle for the whole 60 ft module strip
   (truncates every row to the narrowest scanline on a tapered parcel).
❌ Aisle-centred modules that force the outermost bay to find an interior
   aisle instead of being served by the perimeter ring.
❌ Drawing the aisle rectangle as the module outline.
❌ Axis-aligned leftover flood-fill AABBs as “landscape islands”.
❌ Treating exact contact with opposing stall front as blocked aisle
   (kills one side of every double-loaded row).
❌ Collapsing irregular parcels to tiny ortho racetracks by default.
❌ Parking into acute tips because “24 ft empty tip space” looks like an aisle.
❌ Leaving row ends without terminal islands / turn space.
❌ Drawing freehand street edges; must pick existing boundary edge.


═══════════════════════════════════════════════════════════════════════════════
7. VERIFICATION CHECKLIST (MUST PASS)
═══════════════════════════════════════════════════════════════════════════════

□ On a rectangle: each interior double-loaded run has EQUAL stall counts
  on both sides of the aisle.
□ On an irregular acute-tip site: tip between site corner and chamfered
  ring is empty of stalls.
□ Exactly three orientation options are evaluated and reported.
□ Interior aisles lie inside/on chamfered inner ring; aisles extend to ring.
□ Street throats are perpendicular inward, not diagonal.
□ Terminal islands exist at both ends of each accepted row.
□ Island/curb corners are filleted at nominal R=5 ft.
□ No ghost large axis-aligned boxes over the center.
□ Rhino scripts reload parking_core each run.
□ On a tapered parcel the two rows of a bay have DIFFERENT lengths and the
  island nose steps/tapers with the site edge.
□ The outermost bay is served by the perimeter ring, not by a second
  interior aisle.
□ All three developed options are selectable by the user, not only the
  highest stall count.


═══════════════════════════════════════════════════════════════════════════════
8. IMPLEMENTATION STYLE
═══════════════════════════════════════════════════════════════════════════════

- Pure functions in parking_core.py; Rhino I/O only in parking_layout.py.
- Feet, floats, math module only for core geometry.
- Prefer closed polylines for stalls/aisles/islands/curbs.
- Comments in English; user-facing MessageBox English is OK.
- Keep the whole rhino/ folder together for distribution.


═══════════════════════════════════════════════════════════════════════════════
9. BACKLOG — LESSONS FROM ParkCAD (Transoft)
═══════════════════════════════════════════════════════════════════════════════

Observed in the ParkCAD demo and adopted as planned work. Items marked DONE
are already implemented; the rest are queued.

□ 1. Interactive orientation preview
     ParkCAD rotates the module axis live under the cursor and reports stall
     count as it turns.
     DONE (headless): edge-parallel / perpendicular seeds plus a 15 deg sweep.
     TODO: Rhino-side live rotation preview with running stall count.

□ 2. Row groups as first-class objects
     ParkCAD treats each aisle-sharing row as one editable entity ("Edit Row"):
     per-row orientation, one-way vs two-way, delete, restripe.
     TODO: promote skeleton runs to a stable Row Group record (id, aisle,
     flow, orientation, stalls) and emit one Rhino group + sublayer per row so
     a single row can be edited without regenerating the whole lot.

□ 3. Flow arrows and turn-aware island shaping
     End-cap islands exist; ParkCAD additionally draws flow direction and
     reshapes the island nose to match the legal turn into the aisle.
     DONE: end-cap / mid-row islands, R5 island and R15 ring fillets.
     TODO: per-row flow arrows, and island nose fillet driven by the actual
     turn direction and one-way vs two-way flow.

□ 4. Swept-path verification of aisle width
     ParkCAD sweeps a template vehicle reversing out of a stall and turning at
     intersections, then flags conflicts.
     DONE: fixed 24 ft aisle rule, ring centreline corner test, connectivity
     flood fill.
     TODO: template-vehicle swept path for backing out and for ring / cross
     aisle turns; on conflict widen the aisle or drop the offending stalls and
     re-score, so aisle width becomes an outcome rather than a constant.

Build the toolkit now.
```

---

## One-line core

**Order:** boundary + street → per-edge ring (park region + drive region) → 3 orientations → 60 ft bay-island lattice → per-column, per-row fit against both regions → shared-grid island roles → stalls → tip/access QA → R5 curb / R9 bay fillets → compare valid stall counts, user picks the option.

**Forbidden:** stall-first-then-delete; tip parking; diagonal access; one-sided double-loaded rows; isotropic 30 ft shrink; leaving acute tips occupied.
