# Parking Layout Lab

Parking Layout Lab is an early feasibility toolkit for generating parking concepts from Rhino geometry. The project currently includes:

1. A Rhino panel-style feasibility script for surface parking and schematic garage options.
2. A Rhino command-style layout script for direct in-model parking layout generation.
3. A browser prototype for uploading a `.3dm`, comparing parking options, and exporting a selected option back to `.3dm`.

The immediate goal is not construction documentation. The goal is to help a design team quickly answer:

- How many stalls can the site support as surface parking?
- Is a garage needed to meet the required stall count?
- If a garage is needed, how many levels and what approximate footprint are required?
- What rough geometry should be placed in Rhino so the team can continue design exploration?

## Current design assumptions

The MVP uses deliberately fixed assumptions to keep early studies fast and consistent:

| Item | Value |
| --- | --- |
| Stall size | 9 ft x 18 ft |
| Parking angle | 90 degrees |
| Drive aisle | 24 ft |
| Garage bay module | 18 ft stall + 24 ft aisle + 18 ft stall = 60 ft |
| Garage ramp/core loss | 15 percent capacity reduction |
| Geometry type | 2D schematic curves and labels |

All Rhino scripts assume the Rhino model is using feet if the fixed values above are intended as feet.

## Recommended workflow

For company demos and project feasibility studies, start with:

```text
rhino/parking_feasibility_panel.py
```

This script opens a compact dialog inside Rhino and produces surface and garage feasibility geometry directly in the active model.

Use the browser prototype when you want a quick upload-preview-export workflow outside Rhino.

## Rhino feasibility panel

### File

```text
rhino/parking_feasibility_panel.py
```

### How to run

1. Open a project `.3dm` file in Rhino.
2. Prepare a closed curve representing the usable parking site area.
3. Run Rhino's `RunPythonScript` command.
4. Select `rhino/parking_feasibility_panel.py`.
5. Use the dialog to select the site curve, optional access points, and study inputs.
6. Click `Generate Feasibility Geometry`.

### Panel inputs

| Input | Required | Description |
| --- | --- | --- |
| Site curve | Yes | Closed curve defining the usable site area. |
| Access points | No | Optional vehicle entry/exit points. Used for visual reference in the current MVP. |
| Required stalls | Yes | Target stall count for the study. |
| Setback | Yes | Offset from the selected site boundary bounding box. |
| Maximum garage levels | Yes | Upper limit used when testing garage options. |

### What the panel generates

The script first generates a surface parking layout. It then compares the surface stall count against the required stall count.

If surface parking is sufficient:

- Surface stalls are drawn.
- A summary note confirms that no garage is required.

If surface parking is short:

- The deficit is calculated.
- Up to three schematic garage options are generated.
- Each option reports:
  - bay count
  - footprint width and depth
  - required levels
  - stalls per level
  - total stalls
  - approximate square feet per stall
  - whether the option exceeds the maximum level limit

### Rhino layers

The feasibility panel writes geometry to these layers:

```text
Parking Feasibility::Site
Parking Feasibility::Surface
Parking Feasibility::Access
Parking Feasibility::Garage Options
Parking Feasibility::Stats
```

### Garage option logic

The garage generator is intentionally schematic. It tests simple double-loaded garage modules:

```text
single bay: 60 ft deep
two bays: 120 ft deep
three bays: 180 ft deep
```

For each option, capacity is estimated as:

```text
raw stalls per level = floor(garage length / 9 ft) * 2 * bay count
net stalls per level = raw stalls per level * (1 - ramp/core loss)
levels = ceil(deficit / net stalls per level)
```

This gives the team a practical first-pass answer on whether a garage is needed and what scale it may require.

## Rhino command-style layout script

### File

```text
rhino/parking_layout.py
```

This is a simpler direct-layout script. It is useful for testing site-boundary, entrance, and exit based parking generation without the feasibility panel.

### How to run

1. Open the target `.3dm` file in Rhino.
2. Prepare a closed curve or polyline for the usable parking area.
3. Run Rhino's `RunPythonScript` command.
4. Select `rhino/parking_layout.py`.
5. Pick the usable area curve, entrance point, and exit point.
6. Enter the setback and maximum row count.

### Output layers

```text
Parking Layout::Available Area
Parking Layout::Stalls
Parking Layout::Aisles
Parking Layout::Circulation
Parking Layout::Labels
```

## Browser prototype

The browser prototype is a React/Vite application that can read `.3dm` files with `rhino3dm`.

### Workflow

1. Run the web app.
2. Upload a Rhino `.3dm` file.
3. Review generated parking options.
4. Adjust site width, site depth, setback, and maximum row count if needed.
5. Select an option.
6. Export the selected option as:
   - Rhino `.3dm`
   - SVG
   - JSON

The exported `.3dm` preserves the uploaded source document and adds generated parking layout curves on new `Parking Layout Lab` layers.

### Run locally

```bash
npm install
npm run dev
```

### Build

```bash
npm run build
```

## Repository structure

```text
README.md
index.html
package.json
src/
  App.tsx
  main.tsx
  styles.css
  rhino3dm.d.ts
rhino/
  parking_feasibility_panel.py
  parking_layout.py
```

## Technical notes

### Rhino scripts

The Rhino scripts use `rhinoscriptsyntax` and, for the feasibility panel, Rhino's Eto UI bindings when available.

The panel script includes a command-prompt fallback path so the core feasibility logic remains usable if Eto is unavailable.

### Browser app

The browser app uses:

- React
- Vite
- TypeScript
- `rhino3dm`

The app reads uploaded `.3dm` files, extracts readable model bounds and reference outlines, previews parking options in SVG, and writes selected option geometry back to a downloadable `.3dm`.

## Known limitations

This is an early feasibility prototype. It does not yet handle:

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

Garage output should be treated as a concept-level massing and capacity study, not a permit or construction drawing.

## Near-term roadmap

Recommended next steps:

1. Convert `parking_feasibility_panel.py` into a compiled Rhino plugin command.
2. Replace bounding-box based surface layout with a stronger polygon solver.
3. Add garage presets for efficient, typical, and conservative square feet per stall.
4. Add a simple ramp/core footprint option.
5. Add option scoring and ranking.
6. Add support for no-build zones and existing building footprints.
7. Add export of a clean option summary table.
8. Package the Rhino workflow through Yak or a standard installer.

## Open-source references evaluated

The following references informed the current approach:

- `ucalyptus/ParkSolver`: MIT, useful surface parking solver concepts.
- `V-prajit/Polaris`: MIT, useful structured parking capacity heuristic.
- `UDST/urbansim`: BSD 3-Clause, useful development feasibility assumptions for parking area per stall.
- Food4Rhino `Parking Solver`: useful Rhino/Grasshopper UX reference for open plot parking.
- Food4Rhino `Parking Square`: useful structured/grid parking UX reference.

The current implementation does not vendor these projects. They are references for future solver improvements.

## Validation commands

```bash
python3 -m py_compile rhino/parking_layout.py rhino/parking_feasibility_panel.py
rg --pcre2 "\\p{Hangul}" .
npm run build
```
