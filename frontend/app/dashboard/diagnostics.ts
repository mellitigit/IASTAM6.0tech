// Types, demo patches and .npy encoding for the satellite health diagnostics view.
// Response types mirror backend/app/schemas/diagnostics.py.

export type Severity = "NOMINAL" | "WARNING" | "CRITICAL" | "ALERT";

export type AiOutputs = {
  // null marks a non-finite model output
  urgency_probs: (number | null)[];
  usability_pred: number | null;
  entropy: number | null;
  recon_error: number | null;
};

export type HealthReport = {
  health_status: string;
  severity: Severity;
  root_cause: string;
  recommended_action: string;
  ai_prediction_trusted: boolean;
  battery_safe: boolean;
  diagnostic_message: string;
  timestamp: string;
};

export type DiagnosticsResponse = {
  // null when inference was skipped (NaN/Inf pixels in the patch)
  ai: AiOutputs | null;
  health: HealthReport;
};

type ApiErrorBody = { detail?: string | { msg: string }[] };

export const BANDS = ["B04 red", "B03 green", "B02 blue", "B08 NIR", "B11 SWIR1", "B12 SWIR2"];

// Thresholds used by the backend watchdog, shown next to the model outputs.
export const ENTROPY_LIMIT = 0.82;
export const NOVELTY_LIMIT = 0.35;

export function errorMessage(status: number, body: unknown): string {
  const detail = (body as ApiErrorBody | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) return detail.map((d) => d.msg).join("; ");
  return `Request failed (${status})`;
}

export type DemoKind = "nominal" | "dead-band" | "saturated" | "bit-flip" | "novelty";

export const demoPatches: { kind: DemoKind; label: string; description: string }[] = [
  { kind: "nominal", label: "Nominal", description: "Smooth vegetation / soil scene, 128 × 128" },
  { kind: "dead-band", label: "Dead band", description: "SWIR2 detector stuck at a constant value" },
  { kind: "saturated", label: "Saturated band", description: "Red band fully clipped at 1.0" },
  { kind: "bit-flip", label: "NaN / bit-flip", description: "One corrupted pixel in the blue band" },
  { kind: "novelty", label: "High novelty", description: "Featureless 64 × 64 surface, unlike the training scenes" },
];

export type Patch = { data: Float32Array; shape: [number, number, number]; name: string };

// Two low-frequency waves mixing vegetation and bare-soil spectra: spatially correlated, not noise.
function smoothScene(size: number): Float32Array {
  const veg = [0.05, 0.08, 0.04, 0.35, 0.2, 0.1];
  const soil = [0.18, 0.15, 0.12, 0.28, 0.32, 0.25];
  const field = new Float64Array(size * size);
  let min = Infinity;
  let max = -Infinity;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const u = x / size;
      const v = y / size;
      const f = Math.sin(2 * Math.PI * (1.3 * u + 0.7 * v)) + Math.sin(2 * Math.PI * (0.4 * u - 1.1 * v) + 1);
      field[y * size + x] = f;
      min = Math.min(min, f);
      max = Math.max(max, f);
    }
  }
  const data = new Float32Array(6 * size * size);
  for (let b = 0; b < 6; b++) {
    for (let i = 0; i < size * size; i++) {
      const f = (field[i] - min) / (max - min);
      data[b * size * size + i] = veg[b] * f + soil[b] * (1 - f);
    }
  }
  return data;
}

export function makeDemoPatch(kind: DemoKind): Patch {
  if (kind === "novelty") {
    const size = 64;
    const data = new Float32Array(6 * size * size);
    for (let b = 0; b < 6; b++) {
      for (let i = 0; i < size * size; i++) data[b * size * size + i] = 0.2 + 0.05 * ((i % size) / size);
    }
    return { data, shape: [6, size, size], name: `demo-${kind}.npy` };
  }
  const size = 128;
  const plane = size * size;
  const data = smoothScene(size);
  if (kind === "dead-band") data.fill(0.3, 5 * plane, 6 * plane);
  if (kind === "saturated") data.fill(1, 0, plane);
  if (kind === "bit-flip") data[2 * plane] = NaN;
  return { data, shape: [6, size, size], name: `demo-${kind}.npy` };
}

// NumPy .npy v1.0, little-endian float32, C order.
export function encodeNpy({ data, shape }: Patch): Blob {
  let header = `{'descr': '<f4', 'fortran_order': False, 'shape': (${shape.join(", ")}), }`;
  const preamble = 10; // magic (6) + version (2) + header length (2)
  header += " ".repeat(64 - ((preamble + header.length + 1) % 64)) + "\n";
  const buffer = new ArrayBuffer(preamble + header.length + data.length * 4);
  const bytes = new Uint8Array(buffer);
  bytes.set([0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59, 1, 0]); // \x93NUMPY v1.0
  const view = new DataView(buffer);
  view.setUint16(8, header.length, true);
  for (let i = 0; i < header.length; i++) bytes[preamble + i] = header.charCodeAt(i);
  const offset = preamble + header.length;
  for (let i = 0; i < data.length; i++) view.setFloat32(offset + i * 4, data[i], true);
  return new Blob([buffer], { type: "application/octet-stream" });
}
