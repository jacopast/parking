# Rhino Plugin Parking

Rhino Plugin Parking is an early feasibility toolkit for generating parking concepts directly inside Rhino. The current repository is intentionally Rhino-first: it contains Rhino Python scripts only, with no browser application or Node development environment.

The immediate goal is to help a design team answer early site-planning questions:

- How many stalls can the available land support as surface parking?
- Is a parking garage needed to meet the required stall count?
- If a garage is needed, what approximate footprint and level count are required?
- What schematic Rhino geometry should be generated so the team can continue design exploration?

## Contents

```text
README.md
rhino/
  parking_feasibility_panel.py
  parking_layout.py
```

## Primary workflow: feasibility panel

Use this script for project feasibility studies:

```text
rhino/parking_feasibility_panel.py
```

Run it with Rhino's `RunPythonScript` command. It opens a compact panel-style dialog inside Rhino.

### Inputs

| Input | Required | Description |
| --- | --- | --- |
| Site curve | Yes | Closed curve defining the usable parking area. |
| Access points | No | Optional vehicle entry or exit reference points. |
| Required stalls | Yes | Target parking count for the project. |
| Setback | Yes | Offset from the selected site boundary bounding box. |
| Maximum garage levels | Yes | Upper limit used when testing garage options. |

### Outputs

The panel first generates a surface parking layout. It then compares the surface stall count against the required stall count.

If surface parking is sufficient:

- surface stalls are drawn
- drive aisles are drawn
- a summary note confirms that no garage is required

If surface parking is short:

- the deficit is calculated
- up to three schematic garage options are generated
- each option reports:
  - bay count
  - footprint width and depth
  - required levels
  - stalls per level
  - total stalls
  - approximate square feet per stall
  - whether the option exceeds the maximum level limit

### Rhino layers

The feasibility panel writes geometry to:

```text
Parking Feasibility::Site
Parking Feasibility::Surface
Parking Feasibility::Access
Parking Feasibility::Garage Options
Parking Feasibility::Stats
```

## Secondary workflow: command-style layout script

Use this script for a simpler direct layout test:

```text
rhino/parking_layout.py
```

It prompts for:

1. a usable area curve
2. an entrance point
3. an exit point
4. setback
5. maximum row count

It generates:

```text
Parking Layout::Available Area
Parking Layout::Stalls
Parking Layout::Aisles
Parking Layout::Circulation
Parking Layout::Labels
```

## Design assumptions

The MVP uses fixed assumptions to keep feasibility studies fast and consistent:

| Item | Value |
| --- | --- |
| Stall size | 9 ft x 18 ft |
| Parking angle | 90 degrees |
| Drive aisle | 24 ft |
| Garage bay module | 18 ft stall + 24 ft aisle + 18 ft stall = 60 ft |
| Garage ramp/core loss | 15 percent capacity reduction |
| Geometry type | 2D schematic curves and labels |

The Rhino model should use feet if these values are intended as feet.

## Garage feasibility logic

Garage output is schematic and intended for concept-level feasibility studies.

The feasibility panel tests simple double-loaded garage modules:

```text
single bay: 60 ft deep
two bays: 120 ft deep
three bays: 180 ft deep
```

For each garage option:

```text
raw stalls per level = floor(garage length / 9 ft) * 2 * bay count
net stalls per level = raw stalls per level * (1 - ramp/core loss)
levels = ceil(deficit / net stalls per level)
```

This gives a practical first-pass answer on whether a garage is needed and what scale it may require.

## How to run in Rhino

1. Open the target `.3dm` file in Rhino.
2. Make sure the usable site boundary is a closed curve or polyline.
3. Run `RunPythonScript`.
4. Select one of the scripts in the `rhino/` folder.
5. Follow the prompts or panel controls.
6. Review the generated layers and summary labels.

## Validation

This repository does not require Node, npm, or a web build.

Use Python syntax validation:

```bash
python3 -m py_compile rhino/parking_layout.py rhino/parking_feasibility_panel.py
```

Use this optional check to confirm shared repository text remains English-only:

```bash
rg --pcre2 "\\p{Hangul}" .
```

## Known limitations

This is not yet a construction-documentation tool. It does not handle:

- detailed ramp geometry
- turning templates
- column grids
- stair or elevator cores
- ADA or EV allocation
- fire lanes
- compact stall mixes
- local code checks
- sloped decks
- structural system validation
- detailed cost modeling

Garage output should be treated as a concept-level massing and capacity study only.

## Recommended next steps

1. Convert `parking_feasibility_panel.py` into a compiled Rhino plugin command.
2. Replace bounding-box based surface layout with a stronger polygon solver.
3. Add garage presets for efficient, typical, and conservative square feet per stall.
4. Add a simple ramp/core footprint option.
5. Add option scoring and ranking.
6. Add support for no-build zones and existing building footprints.
7. Add export of a clean option summary table.
8. Package the Rhino workflow through Yak or a standard installer.

## Open-source references evaluated

The current implementation does not vendor these projects, but they informed the approach:

- `ucalyptus/ParkSolver`: MIT, useful surface parking solver concepts.
- `V-prajit/Polaris`: MIT, useful structured parking capacity heuristic.
- `UDST/urbansim`: BSD 3-Clause, useful development feasibility assumptions for parking area per stall.
- Food4Rhino `Parking Solver`: useful Rhino/Grasshopper UX reference for open plot parking.
- Food4Rhino `Parking Square`: useful structured/grid parking UX reference.
