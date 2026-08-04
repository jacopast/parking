# Rhino Plugin Parking

Rhino Plugin Parking is a Rhino 8 plug-in for generating early parking concepts directly inside Rhino. It automatically registers a dockable launcher panel and packages the existing Rhino Python generators inside the plug-in, so normal use does not require `RunPythonScript` or a script path.

The immediate goal is to help a design team answer early site-planning questions:

- How many stalls can the available land support as surface parking?
- Is a parking garage needed to meet the required stall count?
- If a garage is needed, what approximate footprint and level count are required?
- What schematic Rhino geometry should be generated so the team can continue design exploration?

## Contents

```text
README.md
plugin/
  RhinoPluginParking/
    RhinoPluginParking.csproj
    ParkingPlugin.cs
    ParkingPanel.cs
    ParkingCommands.cs
    EmbeddedPythonRunner.cs
rhino/
  parking_feasibility_panel.py
  parking_layout.py
```

## Install in Rhino 8

1. Open the latest successful `Build Rhino plug-in` run on GitHub Actions.
2. Download the `RhinoPluginParking-installer` artifact.
3. Extract the artifact, then double-click `RhinoPluginParking.rhi`.
4. Complete the Rhino installer and restart Rhino.
5. Open `Panels` in Rhino and select `Parking Feasibility`.

The plug-in loads at Rhino startup and registers three commands:

```text
ParkingTools
ParkingFeasibility
ParkingLayout
```

`ParkingTools` opens the dockable launcher if it is not already visible. The other two commands start the corresponding generators directly.

## Primary workflow: feasibility generator

Use the `Open Feasibility Generator` button in the `Parking Feasibility` panel for project feasibility studies. The embedded source is:

```text
rhino/parking_feasibility_panel.py
```

It opens a compact dialog inside Rhino.

### Inputs

| Input | Required | Description |
| --- | --- | --- |
| Site curve | Yes | Closed curve defining the usable parking area. |
| Street edge | Yes | Pick a point on the boundary edge that fronts the public street. |
| Required stalls | Yes | Target parking count for the project. |
| Setback | Yes | Offset from the selected site boundary. |
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

## Secondary workflow: direct layout

Use the `Run Direct Layout` button for a simpler layout test. The embedded source is:

```text
rhino/parking_layout.py
```

It prompts for:

1. a usable area curve
2. the street-frontage edge (click a point on that boundary side)
3. setback

It generates:

```text
Parking Layout::Available Area
Parking Layout::Stalls
Parking Layout::Aisles
Parking Layout::Islands
Parking Layout::Circulation
Parking Layout::Curbs
```

## Design basis

The hard part is geometry, not the dimension table. Published module widths define the lattice period (how wide a bay is). The layout itself comes from searching aisle orientation and lattice phase until the most stalls fit and still connect to the ring drive.

Module widths in `rhino/parking_core.py` reproduce [Iowa SUDAS Design Manual 8B-1, Table 8B-1.02](https://www.iowasudas.org/wp-content/uploads/sites/15/2020/03/8B-1.pdf), adapted from ULI and NPA, *The Dimensions of Parking*.

Active packing uses **90 degree two-way** stalls only (18 / 24 / 18 = 60 ft module, 9 ft pitch). Diagonal 60 / 45 modules remain in code as a last-resort fallback if perpendicular search returns zero stalls.

| Park angle | Flow | Stall projection | Aisle | Double-loaded module | Stall pitch along aisle | Interlock | Role |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 90 | two-way | 18 ft | 24 ft | 60 ft | 9 ft | 0 | primary |
| 60 | one-way | 15 ft 7 in | 20 ft 4 in | 51 ft 6 in | 10 ft 5 in | 2 ft 3 in | last resort |
| 60 | two-way | 15 ft 7 in | 25 ft 10 in | 57 ft | 10 ft 5 in | 2 ft 3 in | last resort |
| 45 | one-way | 12 ft 9 in | 21 ft 6 in | 47 ft | 12 ft 9 in | 3 ft 2 in | last resort |
| 45 | two-way | 12 ft 9 in | 29 ft 8 in | 55 ft 2 in | 12 ft 9 in | 3 ft 2 in | last resort |

```text
stall pitch along aisle = stall width / sin(angle)
stall projection        = stall stripe length * sin(angle)
double-loaded module    = 2 * stall projection + aisle
single-loaded module    = stall projection + aisle
```

Angles between 76 and 89 degrees are never generated.

Accessible stall counts come from the [2010 ADA Standards, Table 208.2](https://www.access-board.gov/ada/#ada-208), with one van accessible stall per six accessible stalls.

| Item | Value |
| --- | --- |
| Stall width | 9 ft |
| Stall stripe length | 18 ft |
| Ring drive | 24 ft |
| Efficiency target | 330 square feet per stall or better |
| Garage bay module | 18 ft stall + 24 ft aisle + 18 ft stall = 60 ft |
| Garage ramp/core loss | 15 percent capacity reduction |
| Geometry type | 2D schematic curves only, no model text labels |

The Rhino model should use feet.

## How the surface layout is built

The packer follows the same order as a manual parking study:

1. Fix the site boundary and street frontage.
2. Establish the **site-following perimeter circulation ring first**. Acute ring corners are chamfered; obtuse corners remain.
3. Use the actual chamfered inner ring as the parking core — not a nominal setback distance.
4. Build exactly **three aisle-skeleton options**: street-perpendicular, street-parallel, and dominant-edge aligned.
5. Clip each aisle/row skeleton to the inner ring and clean its ends. Disconnected or short runs are rejected before stalls exist.
6. After the skeleton is valid, populate its stalls and reserve terminal end-cap islands. Long runs get an interior island after every 10 stalls.
7. Place the perimeter row outside the ring, skipping the street frontage and the full dead zone between each acute site tip and the chamfered ring.
8. Validate a clear **24 ft** maneuvering aisle and straight inward entry/exit throats.
9. Compare the three completed, driveable options and return the highest-capacity layout. A fitted orthogonal racetrack is fallback only when no site-following option works.

Active packing is 90 degree two-way only. Diagonal 60/45 is last-resort if perpendicular search returns nothing. That matches the [ESGI 91 Arup study](https://miis.maths.ox.ac.uk/726/1/ESGI91-Arup_CaseStudy.pdf): perpendicular double-row modules pack best, and finite sites need orientation + shift search.

## Prior work reviewed

The layout logic follows these rather than starting from scratch:

- [thelandlord92/Feasibility](https://github.com/thelandlord92/Feasibility) (MIT): perimeter versus internal bay split, pattern set-out lines, fillet radii on the internal offset.
- [zhihengjiao/BarnacleParking](https://github.com/zhihengjiao/BarnacleParking): row-sequence solver and computed module table for a Grasshopper plugin.
- [badapinguino/parking-lot-algorithm](https://github.com/badapinguino/parking-lot-algorithm): the cleanest derivation of stall pitch, row depth, and the angled row run-out.
- [ucalyptus/ParkSolver](https://github.com/ucalyptus/ParkSolver) (MIT): edge-aligned candidate angle generation and stripe sweep structure.
- [ESGI 91, Optimisation of Car Park Designs](https://miis.maths.ox.ac.uk/726/1/ESGI91-Arup_CaseStudy.pdf) (Arup): names the method "tile and trim" and shows the problem is a bin-packing variant.

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

## Run the Python sources without installing

1. Open the target `.3dm` file in Rhino.
2. Make sure the usable site boundary is a closed curve or polyline.
3. Run `RunPythonScript`.
4. Select one of the scripts in the `rhino/` folder.
5. Follow the prompts or panel controls.
6. Review the generated layers and summary labels.

## Validation

This repository does not require Node, npm, or a web build. Building the `.rhp` requires the .NET 8 SDK.

Build the Rhino 8 plug-in:

```bash
dotnet build plugin/RhinoPluginParking/RhinoPluginParking.csproj --configuration Release
```

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

1. Replace bounding-box based surface layout with a stronger polygon solver.
2. Move the feasibility form controls directly into the dockable plug-in panel.
3. Add garage presets for efficient, typical, and conservative square feet per stall.
4. Add a simple ramp/core footprint option.
5. Add option scoring and ranking.
6. Add support for no-build zones and existing building footprints.
7. Add export of a clean option summary table.
8. Publish signed releases through Rhino Package Manager.

## Open-source references evaluated

The current implementation does not vendor these projects, but they informed the approach:

- `ucalyptus/ParkSolver`: MIT, useful surface parking solver concepts.
- `V-prajit/Polaris`: MIT, useful structured parking capacity heuristic.
- `UDST/urbansim`: BSD 3-Clause, useful development feasibility assumptions for parking area per stall.
- Food4Rhino `Parking Solver`: useful Rhino/Grasshopper UX reference for open plot parking.
- Food4Rhino `Parking Square`: useful structured/grid parking UX reference.
