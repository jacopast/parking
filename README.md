# Parking Layout Lab

Tools for quickly generating parking layout concepts from Rhino geometry. The primary workflow is the Rhino Python script in `rhino/parking_layout.py`, which runs directly inside an open `.3dm` file. A browser prototype for uploading `.3dm` files and sketching 2D options is also included.

## Rhino Python workflow

1. Open the target `.3dm` file in Rhino.
2. Prepare the usable parking area as a closed curve or polyline.
3. Identify the entrance center point and exit center point.
4. Run Rhino's `RunPythonScript` command.
5. Select `rhino/parking_layout.py`.
6. Pick the usable area curve, entrance point, and exit point in order.
7. Enter stall width/depth, aisle width, entrance-to-exit drive width, angle, setback, and maximum row count.

The script creates geometry on these layers in the current Rhino file:

- `Parking Layout::Available Area`
- `Parking Layout::Stalls`
- `Parking Layout::Aisles`
- `Parking Layout::Circulation`
- `Parking Layout::Labels`

The entrance-to-exit line defines the layout axis. The configured `Circulation` corridor is kept clear, and stalls are only generated when they fit inside the usable area and do not overlap that corridor.

## Browser prototype

## Run

```bash
npm install
npm run dev
```

## Build

```bash
npm run build
```

## Features

- Generate parking layouts in Rhino from a usable area, entrance point, and exit point
- Upload Rhino `.3dm` files in the browser prototype
- Inspect Rhino layer, object count, and model bounds metadata
- Apply readable model bounds to the layout canvas
- Adjust site width/depth, stall width/depth, row/column count, aisle width, and parking angle
- Preview parking concepts on an SVG canvas
- Export layout options as JSON or SVG

## Notes

Browser-side Rhino parsing uses `rhino3dm`. Depending on geometry type, some reference outlines may fall back to bounding boxes.
