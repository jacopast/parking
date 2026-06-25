import { ChangeEvent, useMemo, useState } from 'react';
import rhino3dm from 'rhino3dm';

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
  warnings: string[];
};

type LayoutSettings = {
  siteWidth: number;
  siteDepth: number;
  rows: number;
  columns: number;
  stallWidth: number;
  stallDepth: number;
  aisleWidth: number;
  angle: number;
  margin: number;
};

type Stall = {
  id: string;
  row: number;
  column: number;
  polygon: Point[];
};

const DEFAULT_SETTINGS: LayoutSettings = {
  siteWidth: 62,
  siteDepth: 42,
  rows: 4,
  columns: 12,
  stallWidth: 2.5,
  stallDepth: 5,
  aisleWidth: 6,
  angle: 90,
  margin: 3,
};

function formatMeters(value: number) {
  return `${value.toLocaleString('ko-KR', { maximumFractionDigits: 1 })} m`;
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
  const buffer = await file.arrayBuffer();
  const doc = rhino.File3dm.fromByteArray(new Uint8Array(buffer));

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
      warnings.push('Rhino geometry bounds could not be read. The layout canvas is still available.');
    }

    return {
      fileName: file.name,
      fileSize: file.size,
      objectCount,
      layers,
      bounds,
      outlines,
      warnings,
    };
  } finally {
    if (typeof doc?.delete === 'function') doc.delete();
  }
}

function generateStalls(settings: LayoutSettings): Stall[] {
  const radians = (settings.angle * Math.PI) / 180;
  const horizontalShift = Math.cos(radians) * settings.stallDepth;
  const bayDepth = Math.max(Math.sin(radians) * settings.stallDepth, settings.stallDepth * 0.5);
  const maxRows = Math.max(1, Math.floor((settings.siteDepth - settings.margin * 2 + settings.aisleWidth) / (bayDepth + settings.aisleWidth)));
  const maxColumns = Math.max(1, Math.floor((settings.siteWidth - settings.margin * 2 - Math.abs(horizontalShift)) / settings.stallWidth));
  const rows = Math.min(settings.rows, maxRows);
  const columns = Math.min(settings.columns, maxColumns);
  const stalls: Stall[] = [];

  for (let row = 0; row < rows; row += 1) {
    const direction = row % 2 === 0 ? 1 : -1;
    const shift = horizontalShift * direction;
    const baseX = settings.margin + (shift < 0 ? Math.abs(shift) : 0);
    const baseY = settings.margin + row * (bayDepth + settings.aisleWidth);

    for (let column = 0; column < columns; column += 1) {
      const x = baseX + column * settings.stallWidth;
      const y = baseY;
      const polygon = [
        { x, y },
        { x: x + settings.stallWidth, y },
        { x: x + settings.stallWidth + shift, y: y + bayDepth },
        { x: x + shift, y: y + bayDepth },
      ];

      stalls.push({
        id: `${row + 1}-${column + 1}`,
        row,
        column,
        polygon,
      });
    }
  }

  return stalls;
}

function normalizeReferencePoint(point: Point, bounds: Bounds, settings: LayoutSettings): Point {
  return {
    x: ((point.x - bounds.minX) / bounds.width) * settings.siteWidth,
    y: settings.siteDepth - ((point.y - bounds.minY) / bounds.depth) * settings.siteDepth,
  };
}

function polygonPoints(points: Point[]) {
  return points.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(' ');
}

function downloadText(fileName: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  link.click();
  URL.revokeObjectURL(url);
}

function App() {
  const [settings, setSettings] = useState<LayoutSettings>(DEFAULT_SETTINGS);
  const [reference, setReference] = useState<RhinoReference | null>(null);
  const [isImporting, setIsImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const stalls = useMemo(() => generateStalls(settings), [settings]);
  const bayDepth = Math.max(Math.sin((settings.angle * Math.PI) / 180) * settings.stallDepth, settings.stallDepth * 0.5);
  const usedArea = settings.siteWidth * settings.siteDepth;
  const capacityDensity = stalls.length / Math.max(usedArea / 100, 1);

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
      setError(`Rhino 파일을 읽지 못했습니다: ${message}`);
    } finally {
      setIsImporting(false);
      event.target.value = '';
    }
  }

  function exportJson() {
    downloadText(
      'parking-layout.json',
      JSON.stringify({ settings, reference, stalls }, null, 2),
      'application/json',
    );
  }

  function exportSvg() {
    const svg = document.querySelector('.layout-canvas')?.outerHTML;
    if (!svg) return;
    downloadText('parking-layout.svg', svg, 'image/svg+xml');
  }

  return (
    <main className="app">
      <section className="hero">
        <div>
          <p className="eyebrow">Parking Layout Lab</p>
          <h1>Rhino 파일을 던지고, 주차장 배치를 바로 스케치하세요.</h1>
          <p className="lede">
            .3dm 파일에서 사이트 경계와 레이어 정보를 읽어 참고선으로 깔고, 주차면 규격과 각도,
            통로 폭을 조절하면서 여러 배치안을 빠르게 비교할 수 있습니다.
          </p>
        </div>
        <label className="upload-card">
          <span>{isImporting ? 'Rhino 파일 읽는 중...' : 'Rhino .3dm 업로드'}</span>
          <small>모델 경계가 읽히면 캔버스 크기에 자동 반영됩니다.</small>
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
          <h2>배치 조건</h2>
          <div className="control-grid">
            <NumberControl label="대지 폭" value={settings.siteWidth} min={10} max={300} step={0.5} suffix="m" onChange={(value) => updateSetting('siteWidth', value)} />
            <NumberControl label="대지 깊이" value={settings.siteDepth} min={10} max={300} step={0.5} suffix="m" onChange={(value) => updateSetting('siteDepth', value)} />
            <NumberControl label="행 수" value={settings.rows} min={1} max={20} step={1} onChange={(value) => updateSetting('rows', value)} />
            <NumberControl label="열 수" value={settings.columns} min={1} max={80} step={1} onChange={(value) => updateSetting('columns', value)} />
            <NumberControl label="주차면 폭" value={settings.stallWidth} min={2} max={4} step={0.1} suffix="m" onChange={(value) => updateSetting('stallWidth', value)} />
            <NumberControl label="주차면 깊이" value={settings.stallDepth} min={4} max={7} step={0.1} suffix="m" onChange={(value) => updateSetting('stallDepth', value)} />
            <NumberControl label="통로 폭" value={settings.aisleWidth} min={3} max={12} step={0.1} suffix="m" onChange={(value) => updateSetting('aisleWidth', value)} />
            <NumberControl label="주차 각도" value={settings.angle} min={45} max={90} step={5} suffix="deg" onChange={(value) => updateSetting('angle', value)} />
            <NumberControl label="외곽 여유" value={settings.margin} min={0} max={15} step={0.5} suffix="m" onChange={(value) => updateSetting('margin', value)} />
          </div>

          <div className="actions">
            <button type="button" onClick={() => setSettings(DEFAULT_SETTINGS)}>
              기본값
            </button>
            <button type="button" onClick={exportJson}>
              JSON 내보내기
            </button>
            <button type="button" onClick={exportSvg}>
              SVG 내보내기
            </button>
          </div>
        </aside>

        <section className="canvas-panel">
          <div className="stats">
            <Stat label="주차면" value={`${stalls.length}`} />
            <Stat label="대지 면적" value={`${usedArea.toFixed(0)} m²`} />
            <Stat label="100m²당" value={`${capacityDensity.toFixed(1)} 면`} />
            <Stat label="행 피치" value={formatMeters(bayDepth + settings.aisleWidth)} />
          </div>

          <svg
            className="layout-canvas"
            viewBox={`0 0 ${settings.siteWidth} ${settings.siteDepth}`}
            role="img"
            aria-label="Parking layout canvas"
          >
            <rect className="site-fill" x="0" y="0" width={settings.siteWidth} height={settings.siteDepth} rx="0.4" />

            {normalizedOutlines.map((outline, index) => (
              <polyline className="rhino-outline" key={`outline-${index}`} points={polygonPoints(outline)} />
            ))}

            {Array.from({ length: settings.rows }).map((_, index) => {
              const y = settings.margin + index * (bayDepth + settings.aisleWidth) + bayDepth;
              if (y > settings.siteDepth - settings.margin) return null;
              return <rect className="aisle" key={index} x={settings.margin} y={y} width={settings.siteWidth - settings.margin * 2} height={settings.aisleWidth} />;
            })}

            {stalls.map((stall) => (
              <polygon className={stall.row % 2 === 0 ? 'stall' : 'stall alternate'} key={stall.id} points={polygonPoints(stall.polygon)} />
            ))}

            <rect className="site-border" x="0" y="0" width={settings.siteWidth} height={settings.siteDepth} />
          </svg>
        </section>
      </section>

      <section className="reference-panel">
        <h2>Rhino 참고 파일</h2>
        {reference ? (
          <div className="reference-grid">
            <Stat label="파일" value={reference.fileName} />
            <Stat label="크기" value={formatFileSize(reference.fileSize)} />
            <Stat label="오브젝트" value={`${reference.objectCount}`} />
            <Stat label="레이어" value={reference.layers.length ? reference.layers.join(', ') : '없음'} />
            <Stat
              label="읽은 경계"
              value={
                reference.bounds
                  ? `${formatMeters(reference.bounds.width)} x ${formatMeters(reference.bounds.depth)}`
                  : '경계 없음'
              }
            />
          </div>
        ) : (
          <p className="empty">아직 업로드된 Rhino 파일이 없습니다. .3dm 파일을 올리면 모델 경계가 참고선으로 표시됩니다.</p>
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
