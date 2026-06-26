import { ChangeEvent, useMemo, useState } from 'react';
import rhino3dm from 'rhino3dm/rhino3dm.module.js';

type Point = {
  x: number;
  y: number;
};

type Bounds = {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  width: number;
  depth: number;
};

type RhinoReference = {
  fileName: string;
  fileSize: number;
  objectCount: number;
  layers: string[];
  bounds?: Bounds;
  outlines: Point[][];
  modelUnitSystem?: number;
  sourceBytes: Uint8Array;
  warnings: string[];
};

type Orientation = 'horizontal' | 'vertical';

type LayoutSettings = {
  siteWidth: number;
  siteDepth: number;
  setback: number;
  maxRows: number;
};

type LayoutOptionSpec = {
  id: string;
  name: string;
  description: string;
  orientation: Orientation;
  setback: number;
  maxRows: number;
};

type Stall = {
  id: string;
  row: number;
  column: number;
  polygon: Point[];
};

type Aisle = {
  id: string;
  polygon: Point[];
};

type LayoutOption = LayoutOptionSpec & {
  stalls: Stall[];
  aisles: Aisle[];
  rowCount: number;
  columnCount: number;
  usedArea: number;
  density: number;
};

const STALL_WIDTH = 9;
const STALL_DEPTH = 18;
const AISLE_WIDTH = 24;
const DEFAULT_SETTINGS: LayoutSettings = {
  siteWidth: 220,
  siteDepth: 150,
  setback: 6,
  maxRows: 12,
};

const OPTION_COLORS = {
  stall: '#ffb703',
  stallAlt: '#7b9acc',
  aisle: '#d6e4f4',
  reference: '#ef476f',
};

function formatUnits(value: number) {
  return `${value.toLocaleString('en-US', { maximumFractionDigits: 1 })}`;
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function countItems(collection: any): number {
  if (!collection) return 0;
  if (typeof collection.count === 'function') return Number(collection.count());
  if (typeof collection.count === 'number') return collection.count;
  if (typeof collection.length === 'number') return collection.length;
  return 0;
}

function getItem(collection: any, index: number) {
  if (!collection) return undefined;
  if (typeof collection.get === 'function') return collection.get(index);
  if (typeof collection.getItem === 'function') return collection.getItem(index);
  return collection[index];
}

function readValue(source: any, key: string) {
  const value = source?.[key];
  return typeof value === 'function' ? value.call(source) : value;
}

function readNumber(source: any, keys: string[]): number | undefined {
  for (const key of keys) {
    const value = readValue(source, key);
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function toPoint(source: any): Point | undefined {
  if (!source) return undefined;
  const x = readNumber(source, ['x', 'X', '0']);
  const y = readNumber(source, ['y', 'Y', '1']);

  if (x === undefined || y === undefined) return undefined;
  return { x, y };
}

function boundsFromPoints(points: Point[]): Bounds | undefined {
  if (points.length === 0) return undefined;

  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const maxX = Math.max(...xs);
  const maxY = Math.max(...ys);

  if (![minX, minY, maxX, maxY].every(Number.isFinite)) return undefined;

  return {
    minX,
    minY,
    maxX,
    maxY,
    width: Math.max(maxX - minX, 1),
    depth: Math.max(maxY - minY, 1),
  };
}

function mergeBounds(bounds: Array<Bounds | undefined>): Bounds | undefined {
  const valid = bounds.filter(Boolean) as Bounds[];
  if (valid.length === 0) return undefined;

  const minX = Math.min(...valid.map((bound) => bound.minX));
  const minY = Math.min(...valid.map((bound) => bound.minY));
  const maxX = Math.max(...valid.map((bound) => bound.maxX));
  const maxY = Math.max(...valid.map((bound) => bound.maxY));

  return {
    minX,
    minY,
    maxX,
    maxY,
    width: Math.max(maxX - minX, 1),
    depth: Math.max(maxY - minY, 1),
  };
}

function pointsFromCollection(collection: any): Point[] {
  const points: Point[] = [];
  const total = countItems(collection);

  for (let index = 0; index < total; index += 1) {
    const point = toPoint(getItem(collection, index));
    if (point) points.push(point);
  }

  return points;
}

function extractGeometryPoints(geometry: any): Point[] {
  if (!geometry) return [];

  const candidates = [
    readValue(geometry, 'vertices'),
    readValue(geometry, 'points'),
    readValue(geometry, 'Points'),
  ];

  for (const candidate of candidates) {
    const points = pointsFromCollection(candidate);
    if (points.length > 0) return points;
  }

  const point = toPoint(geometry);
  return point ? [point] : [];
}

function boundsFromBoundingBox(box: any): Bounds | undefined {
  if (!box) return undefined;

  const min = readValue(box, 'min') ?? readValue(box, 'Min') ?? getItem(box, 0);
  const max = readValue(box, 'max') ?? readValue(box, 'Max') ?? getItem(box, 1);
  const minPoint = toPoint(min);
  const maxPoint = toPoint(max);

  if (!minPoint || !maxPoint) return undefined;

  return {
    minX: Math.min(minPoint.x, maxPoint.x),
    minY: Math.min(minPoint.y, maxPoint.y),
    maxX: Math.max(minPoint.x, maxPoint.x),
    maxY: Math.max(minPoint.y, maxPoint.y),
    width: Math.max(Math.abs(maxPoint.x - minPoint.x), 1),
    depth: Math.max(Math.abs(maxPoint.y - minPoint.y), 1),
  };
}

function extractGeometryBounds(geometry: any): Bounds | undefined {
  const boundingBox = (() => {
    try {
      if (typeof geometry?.getBoundingBox === 'function') return geometry.getBoundingBox(true);
      if (typeof geometry?.boundingBox === 'function') return geometry.boundingBox();
      return readValue(geometry, 'boundingBox');
    } catch {
      return undefined;
    }
  })();

  return boundsFromBoundingBox(boundingBox) ?? boundsFromPoints(extractGeometryPoints(geometry));
}

function boundsToOutline(bounds: Bounds): Point[] {
  return [
    { x: bounds.minX, y: bounds.minY },
    { x: bounds.maxX, y: bounds.minY },
    { x: bounds.maxX, y: bounds.maxY },
    { x: bounds.minX, y: bounds.maxY },
  ];
}

async function parseRhinoFile(file: File): Promise<RhinoReference> {
  const warnings: string[] = [];
  const rhino = await rhino3dm();
  const sourceBytes = new Uint8Array(await file.arrayBuffer());
  const doc = rhino.File3dm.fromByteArray(sourceBytes);

  try {
    const layersCollection = doc.layers?.();
    const layers = Array.from({ length: countItems(layersCollection) }, (_, index) => {
      const layer = getItem(layersCollection, index);
      return String(readValue(layer, 'name') ?? `Layer ${index + 1}`);
    });

    const objectsCollection = doc.objects?.();
    const objectCount = countItems(objectsCollection);
    const objectBounds: Bounds[] = [];
    const outlines: Point[][] = [];

    for (let index = 0; index < objectCount; index += 1) {
      const object = getItem(objectsCollection, index);
      const geometry = typeof object?.geometry === 'function' ? object.geometry() : readValue(object, 'geometry');
      const points = extractGeometryPoints(geometry);
      const bounds = extractGeometryBounds(geometry);

      if (bounds) {
        objectBounds.push(bounds);
        outlines.push(points.length >= 2 ? points : boundsToOutline(bounds));
      }
    }

    const bounds = mergeBounds(objectBounds);
    if (!bounds) {
      warnings.push('Rhino geometry bounds could not be read. Default site dimensions are being used.');
    }

    return {
      fileName: file.name,
      fileSize: file.size,
      objectCount,
      layers,
      bounds,
      outlines,
      modelUnitSystem: readValue(doc.settings?.(), 'modelUnitSystem'),
      sourceBytes,
      warnings,
    };
  } finally {
    const disposableDoc = doc as { delete?: () => void; destroy?: () => void };
    if (typeof disposableDoc.delete === 'function') disposableDoc.delete();
    if (typeof disposableDoc.destroy === 'function') disposableDoc.destroy();
  }
}

function createOptionSpecs(settings: LayoutSettings): LayoutOptionSpec[] {
  return [
    {
      id: 'balanced',
      name: 'Option A - Balanced bays',
      description: 'Uses the uploaded bounds with the default setback and horizontal bays.',
      orientation: 'horizontal',
      setback: settings.setback,
      maxRows: settings.maxRows,
    },
    {
      id: 'rotated',
      name: 'Option B - Rotated bays',
      description: 'Rotates the bays 90 degrees to compare circulation and yield.',
      orientation: 'vertical',
      setback: settings.setback,
      maxRows: settings.maxRows,
    },
    {
      id: 'yield',
      name: 'Option C - Higher yield',
      description: 'Uses a tighter setback while keeping 9 x 18 stalls and 24 aisles.',
      orientation: settings.siteWidth >= settings.siteDepth ? 'horizontal' : 'vertical',
      setback: Math.max(0, settings.setback / 2),
      maxRows: settings.maxRows + 2,
    },
  ];
}

function rectangle(x: number, y: number, width: number, height: number): Point[] {
  return [
    { x, y },
    { x: x + width, y },
    { x: x + width, y: y + height },
    { x, y: y + height },
  ];
}

function generateOption(spec: LayoutOptionSpec, settings: LayoutSettings): LayoutOption {
  const rowsByDepth = Math.max(
    0,
    Math.floor((settings.siteDepth - spec.setback * 2 + AISLE_WIDTH) / (STALL_DEPTH + AISLE_WIDTH)),
  );
  const rowsByWidth = Math.max(
    0,
    Math.floor((settings.siteWidth - spec.setback * 2 + AISLE_WIDTH) / (STALL_DEPTH + AISLE_WIDTH)),
  );
  const columnsByWidth = Math.max(0, Math.floor((settings.siteWidth - spec.setback * 2) / STALL_WIDTH));
  const columnsByDepth = Math.max(0, Math.floor((settings.siteDepth - spec.setback * 2) / STALL_WIDTH));

  const rowCount = Math.min(spec.maxRows, spec.orientation === 'horizontal' ? rowsByDepth : rowsByWidth);
  const columnCount = spec.orientation === 'horizontal' ? columnsByWidth : columnsByDepth;
  const stalls: Stall[] = [];
  const aisles: Aisle[] = [];

  for (let row = 0; row < rowCount; row += 1) {
    if (spec.orientation === 'horizontal') {
      const y = spec.setback + row * (STALL_DEPTH + AISLE_WIDTH);
      const aisleY = y + STALL_DEPTH;

      if (aisleY < settings.siteDepth - spec.setback) {
        aisles.push({
          id: `${spec.id}-aisle-${row + 1}`,
          polygon: rectangle(
            spec.setback,
            aisleY,
            Math.max(settings.siteWidth - spec.setback * 2, 0),
            Math.min(AISLE_WIDTH, settings.siteDepth - spec.setback - aisleY),
          ),
        });
      }

      for (let column = 0; column < columnCount; column += 1) {
        const x = spec.setback + column * STALL_WIDTH;
        stalls.push({
          id: `${spec.id}-${row + 1}-${column + 1}`,
          row,
          column,
          polygon: rectangle(x, y, STALL_WIDTH, STALL_DEPTH),
        });
      }
    } else {
      const x = spec.setback + row * (STALL_DEPTH + AISLE_WIDTH);
      const aisleX = x + STALL_DEPTH;

      if (aisleX < settings.siteWidth - spec.setback) {
        aisles.push({
          id: `${spec.id}-aisle-${row + 1}`,
          polygon: rectangle(
            aisleX,
            spec.setback,
            Math.min(AISLE_WIDTH, settings.siteWidth - spec.setback - aisleX),
            Math.max(settings.siteDepth - spec.setback * 2, 0),
          ),
        });
      }

      for (let column = 0; column < columnCount; column += 1) {
        const y = spec.setback + column * STALL_WIDTH;
        stalls.push({
          id: `${spec.id}-${row + 1}-${column + 1}`,
          row,
          column,
          polygon: rectangle(x, y, STALL_DEPTH, STALL_WIDTH),
        });
      }
    }
  }

  const usedArea = settings.siteWidth * settings.siteDepth;
  const density = stalls.length / Math.max(usedArea / 10000, 1);

  return {
    ...spec,
    stalls,
    aisles,
    rowCount,
    columnCount,
    usedArea,
    density,
  };
}

function normalizeReferencePoint(point: Point, bounds: Bounds, settings: LayoutSettings): Point {
  return {
    x: ((point.x - bounds.minX) / bounds.width) * settings.siteWidth,
    y: settings.siteDepth - ((point.y - bounds.minY) / bounds.depth) * settings.siteDepth,
  };
}

function layoutPointToWorld(point: Point, reference: RhinoReference | null, settings: LayoutSettings): Point {
  if (!reference?.bounds) return point;

  return {
    x: reference.bounds.minX + (point.x / settings.siteWidth) * reference.bounds.width,
    y: reference.bounds.maxY - (point.y / settings.siteDepth) * reference.bounds.depth,
  };
}

function polygonPoints(points: Point[]) {
  return points.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(' ');
}

function closePolygon(points: Point[]) {
  if (points.length === 0) return points;
  return [...points, points[0]];
}

function toRhinoPoints(points: Point[], reference: RhinoReference | null, settings: LayoutSettings) {
  return closePolygon(points).map((point) => {
    const world = layoutPointToWorld(point, reference, settings);
    return [world.x, world.y, 0];
  });
}

function downloadBlob(fileName: string, content: BlobPart, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}

function referenceSummary(reference: RhinoReference | null) {
  if (!reference) return null;
  return {
    fileName: reference.fileName,
    fileSize: reference.fileSize,
    objectCount: reference.objectCount,
    layers: reference.layers,
    bounds: reference.bounds,
    warnings: reference.warnings,
  };
}

function safeFileStem(fileName: string) {
  return fileName.replace(/\.[^.]+$/, '').replace(/[^a-z0-9-_]+/gi, '-').replace(/^-|-$/g, '') || 'parking-layout';
}

function addLayer(doc: any, name: string, color: { r: number; g: number; b: number }) {
  const layers = doc.layers();
  const existing = typeof layers.findName === 'function' ? layers.findName(name, '') : null;
  if (existing && Number.isFinite(existing.index)) return existing.index;
  return layers.addLayer(name, color);
}

function createAttributes(rhino: any, layerIndex: number, name: string) {
  const attributes = new rhino.ObjectAttributes();
  attributes.layerIndex = layerIndex;
  attributes.name = name;
  return attributes;
}

function addPolyline(doc: any, rhino: any, points: number[][], layerIndex: number, name: string) {
  try {
    const curve = new rhino.PolylineCurve(points);
    const attributes = createAttributes(rhino, layerIndex, name);
    doc.objects().add(curve, attributes);
    const disposableCurve = curve as { delete?: () => void; destroy?: () => void };
    if (typeof disposableCurve.delete === 'function') disposableCurve.delete();
    if (typeof disposableCurve.destroy === 'function') disposableCurve.destroy();
  } catch {
    doc.objects().addPolyline(points);
  }
}

async function exportRhinoFile(reference: RhinoReference, option: LayoutOption, settings: LayoutSettings) {
  const rhino = await rhino3dm();
  const doc = rhino.File3dm.fromByteArray(reference.sourceBytes);

  try {
    doc.applicationName = 'Parking Layout Lab';
    doc.applicationDetails = `Generated option: ${option.name}`;

    const layerRoot = `Parking Layout Lab - ${option.name}`;
    const stallLayer = addLayer(doc, `${layerRoot} - Stalls`, { r: 255, g: 183, b: 3 });
    const aisleLayer = addLayer(doc, `${layerRoot} - Aisles`, { r: 61, g: 90, b: 128 });
    const summaryLayer = addLayer(doc, `${layerRoot} - Summary`, { r: 20, g: 30, b: 45 });

    option.aisles.forEach((aisle, index) => {
      addPolyline(doc, rhino, toRhinoPoints(aisle.polygon, reference, settings), aisleLayer, `Aisle ${index + 1}`);
    });

    option.stalls.forEach((stall, index) => {
      addPolyline(doc, rhino, toRhinoPoints(stall.polygon, reference, settings), stallLayer, `Stall ${index + 1}`);
    });

    const summaryPoint = reference.bounds
      ? [reference.bounds.minX, reference.bounds.maxY + AISLE_WIDTH, 0]
      : [0, settings.siteDepth + AISLE_WIDTH, 0];
    doc.objects().addTextDot(
      `${option.name}\nStalls: ${option.stalls.length}\nStall: 9 x 18\nAisle: 24`,
      summaryPoint,
    );

    const bytes = doc.toByteArray();
    downloadBlob(`${safeFileStem(reference.fileName)}-${option.id}.3dm`, bytes, 'model/vnd.rhino.3dm');
  } finally {
    const disposableDoc = doc as { delete?: () => void; destroy?: () => void };
    if (typeof disposableDoc.delete === 'function') disposableDoc.delete();
    if (typeof disposableDoc.destroy === 'function') disposableDoc.destroy();
  }
}

function App() {
  const [settings, setSettings] = useState<LayoutSettings>(DEFAULT_SETTINGS);
  const [reference, setReference] = useState<RhinoReference | null>(null);
  const [selectedOptionId, setSelectedOptionId] = useState('balanced');
  const [isImporting, setIsImporting] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const options = useMemo(
    () => createOptionSpecs(settings).map((spec) => generateOption(spec, settings)),
    [settings],
  );
  const selectedOption = options.find((option) => option.id === selectedOptionId) ?? options[0];
  const normalizedOutlines = useMemo(() => {
    if (!reference?.bounds) return [];
    return reference.outlines.map((outline) => outline.map((point) => normalizeReferencePoint(point, reference.bounds!, settings)));
  }, [reference, settings]);

  function updateSetting(key: keyof LayoutSettings, value: number) {
    setSettings((current) => ({
      ...current,
      [key]: value,
    }));
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    setIsImporting(true);
    setError(null);

    try {
      const parsed = await parseRhinoFile(file);
      setReference(parsed);

      if (parsed.bounds) {
        setSettings((current) => ({
          ...current,
          siteWidth: Number(parsed.bounds!.width.toFixed(1)),
          siteDepth: Number(parsed.bounds!.depth.toFixed(1)),
        }));
      }
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : 'Unknown Rhino import error';
      setError(`Could not read the Rhino file: ${message}`);
    } finally {
      setIsImporting(false);
      event.target.value = '';
    }
  }

  function exportJson() {
    downloadBlob(
      'parking-layout-options.json',
      JSON.stringify({ settings, reference: referenceSummary(reference), options }, null, 2),
      'application/json',
    );
  }

  function exportSvg() {
    const svg = document.querySelector('.layout-canvas')?.outerHTML;
    if (!svg) return;
    downloadBlob(`${selectedOption.id}.svg`, svg, 'image/svg+xml');
  }

  async function handleExportRhino() {
    if (!reference) {
      setError('Upload a Rhino file before exporting a .3dm option.');
      return;
    }

    setIsExporting(true);
    setError(null);

    try {
      await exportRhinoFile(reference, selectedOption, settings);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : 'Unknown Rhino export error';
      setError(`Could not export the Rhino file: ${message}`);
    } finally {
      setIsExporting(false);
    }
  }

  return (
    <main className="app">
      <section className="hero">
        <div>
          <p className="eyebrow">Parking Layout Lab</p>
          <h1>Upload a Rhino file, compare parking options, export a new .3dm.</h1>
          <p className="lede">
            The web app reads the uploaded model bounds, generates multiple parking concepts with fixed 9 x 18 stalls and
            24 aisles, and writes the selected option back into a downloadable Rhino file.
          </p>
        </div>
        <label className="upload-card">
          <span>{isImporting ? 'Reading Rhino file...' : 'Upload Rhino .3dm'}</span>
          <small>Original geometry is preserved; generated parking layers are added on export.</small>
          <input type="file" accept=".3dm" onChange={handleFileChange} disabled={isImporting} />
        </label>
      </section>

      {error && <div className="notice error">{error}</div>}
      {reference?.warnings.map((warning) => (
        <div className="notice" key={warning}>
          {warning}
        </div>
      ))}

      <section className="workspace">
        <aside className="panel">
          <h2>Design inputs</h2>
          <div className="fixed-specs">
            <span>Fixed stall</span>
            <strong>9 x 18</strong>
            <span>Fixed aisle</span>
            <strong>24</strong>
            <span>Parking angle</span>
            <strong>90 deg</strong>
          </div>

          <div className="control-grid">
            <NumberControl label="Site width" value={settings.siteWidth} min={30} max={1000} step={1} suffix="units" onChange={(value) => updateSetting('siteWidth', value)} />
            <NumberControl label="Site depth" value={settings.siteDepth} min={30} max={1000} step={1} suffix="units" onChange={(value) => updateSetting('siteDepth', value)} />
            <NumberControl label="Setback" value={settings.setback} min={0} max={60} step={1} suffix="units" onChange={(value) => updateSetting('setback', value)} />
            <NumberControl label="Maximum rows" value={settings.maxRows} min={1} max={40} step={1} onChange={(value) => updateSetting('maxRows', value)} />
          </div>

          <div className="actions">
            <button type="button" onClick={exportJson}>
              Export options JSON
            </button>
            <button type="button" onClick={exportSvg}>
              Export selected SVG
            </button>
            <button type="button" onClick={handleExportRhino} disabled={!reference || isExporting}>
              {isExporting ? 'Writing .3dm...' : 'Download selected .3dm'}
            </button>
          </div>
        </aside>

        <section className="canvas-panel">
          <div className="option-grid">
            {options.map((option) => (
              <button
                className={option.id === selectedOption.id ? 'option-card active' : 'option-card'}
                key={option.id}
                type="button"
                onClick={() => setSelectedOptionId(option.id)}
              >
                <span>{option.name}</span>
                <strong>{option.stalls.length} stalls</strong>
                <small>{option.description}</small>
              </button>
            ))}
          </div>

          <div className="stats">
            <Stat label="Selected option" value={selectedOption.name.replace('Option ', '')} />
            <Stat label="Stalls" value={`${selectedOption.stalls.length}`} />
            <Stat label="Rows x columns" value={`${selectedOption.rowCount} x ${selectedOption.columnCount}`} />
            <Stat label="Density" value={`${selectedOption.density.toFixed(1)} stalls / 10k sq units`} />
          </div>

          <svg
            className="layout-canvas"
            viewBox={`0 0 ${settings.siteWidth} ${settings.siteDepth}`}
            role="img"
            aria-label="Parking layout option preview"
          >
            <rect className="site-fill" x="0" y="0" width={settings.siteWidth} height={settings.siteDepth} rx="0.4" />

            {normalizedOutlines.map((outline, index) => (
              <polyline className="rhino-outline" key={`outline-${index}`} points={polygonPoints(outline)} />
            ))}

            {selectedOption.aisles.map((aisle) => (
              <polygon className="aisle" key={aisle.id} points={polygonPoints(aisle.polygon)} />
            ))}

            {selectedOption.stalls.map((stall) => (
              <polygon className={stall.row % 2 === 0 ? 'stall' : 'stall alternate'} key={stall.id} points={polygonPoints(stall.polygon)} />
            ))}

            <rect className="site-border" x="0" y="0" width={settings.siteWidth} height={settings.siteDepth} />
          </svg>
        </section>
      </section>

      <section className="reference-panel">
        <h2>Rhino reference file</h2>
        {reference ? (
          <div className="reference-grid">
            <Stat label="File" value={reference.fileName} />
            <Stat label="Size" value={formatFileSize(reference.fileSize)} />
            <Stat label="Objects" value={`${reference.objectCount}`} />
            <Stat label="Layers" value={reference.layers.length ? reference.layers.join(', ') : 'None'} />
            <Stat
              label="Read bounds"
              value={
                reference.bounds
                  ? `${formatUnits(reference.bounds.width)} x ${formatUnits(reference.bounds.depth)}`
                  : 'No bounds'
              }
            />
          </div>
        ) : (
          <p className="empty">Upload a .3dm file to use its model bounds as the design basis and enable Rhino export.</p>
        )}
      </section>
    </main>
  );
}

type NumberControlProps = {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix?: string;
  onChange: (value: number) => void;
};

function NumberControl({ label, value, min, max, step, suffix, onChange }: NumberControlProps) {
  return (
    <label className="control">
      <span>
        {label}
        <strong>
          {value}
          {suffix ? ` ${suffix}` : ''}
        </strong>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

type StatProps = {
  label: string;
  value: string;
};

function Stat({ label, value }: StatProps) {
  return (
    <div className="stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export default App;
