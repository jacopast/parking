# Parking Layout Lab

Tools for quickly generating parking layout concepts from Rhino geometry. The browser workflow uploads a `.3dm`, generates design options from the readable model bounds, previews each option, and downloads a new `.3dm` with the selected parking layout added as Rhino geometry. A Rhino Python script is also included for direct in-Rhino generation.

## Browser workflow

1. Run the web app.
2. Upload a Rhino `.3dm` file.
3. Review the generated parking options.
4. Adjust site width/depth, setback, and maximum row count if needed.
5. Select the preferred option.
6. Download the selected option as a `.3dm`.

The downloaded Rhino file preserves the uploaded source document and adds the generated parking layout on new `Parking Layout Lab` layers.

The web options use fixed 9 x 18 perpendicular stalls and fixed 24 aisles/circulation. These values are applied in Rhino model units, so uploaded files should use feet when those values are intended as feet.

## Rhino Python workflow

1. Open the target `.3dm` file in Rhino.
2. Prepare the usable parking area as a closed curve or polyline.
3. Identify the entrance center point and exit center point.
4. Run Rhino's `RunPythonScript` command.
5. Select `rhino/parking_layout.py`.
6. Pick the usable area curve, entrance point, and exit point in order.
7. Enter only the setback and maximum row count.

The script creates geometry on these layers in the current Rhino file:

- `Parking Layout::Available Area`
- `Parking Layout::Stalls`
- `Parking Layout::Aisles`
- `Parking Layout::Circulation`
- `Parking Layout::Labels`

The entrance-to-exit line defines the layout axis. The script uses fixed 9 ft x 18 ft stalls, fixed perpendicular parking, and fixed 24 ft aisles/circulation. Stalls are only generated when they fit inside the usable area and do not overlap the entrance-to-exit circulation corridor.

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

- Upload a Rhino `.3dm`, generate parking design options, and export a selected option back to `.3dm`
- Generate parking layouts in Rhino from a usable area, entrance point, and exit point
- Use fixed 9 ft x 18 ft perpendicular stalls with 24 ft aisles
- Inspect Rhino layer, object count, and model bounds metadata
- Apply readable model bounds to the layout canvas
- Adjust site width/depth, setback, and maximum row count
- Preview parking concepts on an SVG canvas
- Export layout options as Rhino `.3dm`, JSON, or SVG

## Notes

Browser-side Rhino parsing uses `rhino3dm`. Depending on geometry type, some reference outlines may fall back to bounding boxes.
